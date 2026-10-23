"""
Robinhood adapter for the auto-trader, built on the unofficial robin_stocks
library. It exposes the same methods as AlpacaBroker.

Differences from Alpaca:
  - Real money only: Robinhood has no paper-trading account.
  - No bracket orders. The trader keeps a stop-loss order resting at
    Robinhood and sells at market itself when the take-profit is reached,
    so profits are only taken while the bot is running.
  - robin_stocks returns None instead of raising on failure, so every call
    here is checked and turned into a BrokerError.
"""

import time
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

import pandas as pd

from .broker import BrokerError


NY = ZoneInfo("America/New_York")
MARKET_MIC = "XNYS"
# Re-login well before Robinhood's 24h session expires.
SESSION_MAX_AGE_SECONDS = 12 * 3600


class RobinhoodBroker:
    supports_brackets = False

    def __init__(self, username: str, password: str, totp_secret: str | None = None, api=None):
        if api is None:
            import robin_stocks.robinhood as api
        self.rh = api
        self.username = username
        self.password = password
        self.totp_secret = totp_secret
        self.logged_in_at = 0.0
        self._symbols: dict[str, str] = {}  # instrument url -> symbol

    # ── Session ──────────────────────────────────────────────────

    def login(self) -> None:
        kwargs = {"username": self.username, "password": self.password, "store_session": True}
        if self.totp_secret:
            import pyotp
            kwargs["mfa_code"] = pyotp.TOTP(self.totp_secret).now()
        result = self.rh.login(**kwargs)
        if not result or "access_token" not in result:
            raise BrokerError(f"Robinhood login failed: {result}")
        self.logged_in_at = time.time()

    def logout(self) -> None:
        self.rh.logout()

    def _ensure_session(self) -> None:
        if time.time() - self.logged_in_at > SESSION_MAX_AGE_SECONDS:
            self.login()

    @staticmethod
    def _check(result, what: str):
        if result is None or result == [None]:
            raise BrokerError(f"Robinhood: {what} failed (no data returned)")
        return result

    def _symbol_for(self, instrument_url: str) -> str:
        if instrument_url not in self._symbols:
            self._symbols[instrument_url] = self._check(
                self.rh.stocks.get_symbol_by_url(instrument_url), "symbol lookup")
        return self._symbols[instrument_url]

    # ── Market clock ─────────────────────────────────────────────

    def _hours(self) -> dict:
        return self._check(self.rh.markets.get_market_today_hours(MARKET_MIC), "market hours")

    def get_clock(self) -> dict:
        """Same shape as Alpaca's clock: timestamp, is_open, next_open, next_close."""
        self._ensure_session()
        now = datetime.now(timezone.utc)
        hours = self._hours()
        opens = _ts(hours["opens_at"]) if hours.get("is_open") else None
        closes = _ts(hours["closes_at"]) if hours.get("is_open") else None
        is_open = bool(opens and opens <= now < closes)

        if opens and now < opens:
            next_open = opens
        else:
            nxt = self._check(self.rh.helper.request_get(hours["next_open_hours"]),
                              "next market hours")
            next_open = _ts(nxt["opens_at"])
        next_close = closes if is_open else next_open
        return {"timestamp": now.isoformat(), "is_open": is_open,
                "next_open": next_open.isoformat(), "next_close": next_close.isoformat()}

    def get_calendar_day(self, date: str) -> dict | None:
        hours = self._hours()
        if not hours.get("is_open") or hours.get("date") != date:
            return None
        return {"date": date,
                "open": _ts(hours["opens_at"]).astimezone(NY).strftime("%H:%M"),
                "close": _ts(hours["closes_at"]).astimezone(NY).strftime("%H:%M")}

    # ── Account ──────────────────────────────────────────────────

    def get_account(self) -> dict:
        """Same keys the trader reads from Alpaca's account."""
        acct = self._check(self.rh.profiles.load_account_profile(), "account profile")
        port = self._check(self.rh.profiles.load_portfolio_profile(), "portfolio profile")
        prev = port.get("adjusted_equity_previous_close") or port.get("equity_previous_close")
        day_trades = self.rh.account.get_day_trades() or {}
        if isinstance(day_trades, dict):
            day_trades = day_trades.get("equity_day_transactions") or []
        return {
            "equity": port["equity"],
            "last_equity": prev or port["equity"],
            "buying_power": acct["buying_power"],
            "daytrade_count": len(day_trades),
            "trading_blocked": bool(acct.get("deactivated")),
        }

    def get_positions(self) -> list[dict]:
        raw = self.rh.account.get_open_stock_positions() or []
        held = []
        for p in raw:
            if not p or float(p["quantity"]) <= 0:
                continue
            held.append((p.get("symbol") or self._symbol_for(p["instrument"]), p))
        if not held:
            return []
        prices = self._check(self.rh.stocks.get_latest_price([s for s, _ in held]), "prices")
        positions = []
        for (symbol, p), price in zip(held, prices):
            qty, entry, price = float(p["quantity"]), float(p["average_buy_price"]), float(price)
            positions.append({
                "symbol": symbol, "qty": p["quantity"], "avg_entry_price": entry,
                "current_price": price, "unrealized_pl": (price - entry) * qty,
                "unrealized_plpc": (price - entry) / entry if entry else 0.0,
            })
        return positions

    def get_open_orders(self) -> list[dict]:
        orders = self.rh.orders.get_all_open_stock_orders() or []
        return [{"id": o["id"], "side": o["side"], "type": o["type"],
                 "symbol": o.get("symbol") or self._symbol_for(o["instrument"])}
                for o in orders if o]

    def get_asset(self, symbol: str) -> dict:
        inst = self._check(self.rh.stocks.get_instruments_by_symbols(symbol), "instrument")[0]
        market = inst.get("market") or ""
        return {"tradable": bool(inst.get("tradeable")) and inst.get("state") == "active"
                            and inst.get("type") == "stock",
                "exchange": "OTC" if "OTC" in market.upper() else market}

    # ── Market data ──────────────────────────────────────────────

    def get_top_gainers(self, top: int = 50) -> list[dict]:
        """
        Robinhood has no full-market gainers screener, so this combines its
        Top Movers, S&P 500 up-movers and 100 most popular lists.
        """
        gainers: dict[str, dict] = {}

        for m in self.rh.markets.get_top_movers_sp500("up") or []:
            if not m:
                continue
            move = m.get("price_movement") or {}
            try:
                gainers[m["symbol"]] = {"symbol": m["symbol"],
                                        "percent_change": float(move["market_hours_last_movement_pct"]),
                                        "price": float(move["market_hours_last_price"])}
            except (KeyError, TypeError, ValueError):
                continue

        quotes = (self.rh.markets.get_top_movers() or []) + (self.rh.markets.get_top_100() or [])
        for q in quotes:
            if not q or q.get("trading_halted"):
                continue
            try:
                price = float(q["last_trade_price"])
                prev = float(q.get("adjusted_previous_close") or q["previous_close"])
            except (KeyError, TypeError, ValueError):
                continue
            if prev > 0:
                gainers.setdefault(q["symbol"], {"symbol": q["symbol"], "price": price,
                                                 "percent_change": (price / prev - 1) * 100})

        ranked = sorted(gainers.values(), key=lambda g: g["percent_change"], reverse=True)
        return [g for g in ranked if g["percent_change"] > 0][:top]

    def get_bars(self, symbol: str, **_) -> pd.DataFrame:
        bars = self.rh.stocks.get_stock_historicals(symbol, interval="5minute",
                                                    span="week", bounds="regular")
        bars = [b for b in (bars or []) if b]
        if not bars:
            raise BrokerError(f"Robinhood: no bars for {symbol}")
        df = pd.DataFrame(bars).rename(columns={
            "begins_at": "timestamp", "open_price": "open", "high_price": "high",
            "low_price": "low", "close_price": "close",
        })
        df["timestamp"] = pd.to_datetime(df["timestamp"], utc=True)
        for col in ("open", "high", "low", "close", "volume"):
            df[col] = df[col].astype(float)
        return df[["timestamp", "open", "high", "low", "close", "volume"]].sort_values(
            "timestamp").reset_index(drop=True)

    def get_daily_volume(self, symbol: str) -> int:
        fund = self._check(self.rh.stocks.get_fundamentals(symbol), "fundamentals")[0]
        return int(float(fund.get("volume") or 0))

    # ── Orders ───────────────────────────────────────────────────

    @staticmethod
    def _order_result(result, what: str) -> dict:
        if not result or "id" not in result:
            raise BrokerError(f"Robinhood rejected {what}: {result}")
        return result

    def buy_market(self, symbol: str, qty: int) -> dict:
        # robin_stocks sends this as a limit order with Robinhood's 5% price collar.
        return self._order_result(
            self.rh.orders.order_buy_market(symbol, qty, timeInForce="gfd"), f"buy {symbol}")

    def sell_market(self, symbol: str, qty: int) -> dict:
        return self._order_result(
            self.rh.orders.order_sell_market(symbol, qty, timeInForce="gfd"), f"sell {symbol}")

    def sell_stop(self, symbol: str, qty: int, stop_price: float) -> dict:
        return self._order_result(
            self.rh.orders.order_sell_stop_loss(symbol, qty, stop_price, timeInForce="gtc"),
            f"stop loss {symbol}")

    def cancel_order(self, order_id: str, wait_seconds: float = 10.0) -> None:
        """Cancel and wait until Robinhood confirms, so the shares are free to sell."""
        self.rh.orders.cancel_stock_order(order_id)
        deadline = time.time() + wait_seconds
        while time.time() < deadline:
            info = self.rh.orders.get_stock_order_info(order_id) or {}
            if info.get("state") in ("cancelled", "filled", "rejected", "failed"):
                return
            time.sleep(1)
        raise BrokerError(f"Robinhood: order {order_id} was not cancelled in time")

    def close_position(self, symbol: str, qty: int) -> None:
        """Cancel the symbol's open orders and market-sell qty shares."""
        for order in self.get_open_orders():
            if order["symbol"] == symbol:
                self.cancel_order(order["id"])
        self.sell_market(symbol, qty)


def _ts(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))
