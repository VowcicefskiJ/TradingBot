"""
The autonomous trading loop.

Every tick:
  1. Skip if the market is closed.
  2. Manage exits for positions the bot opened.
  3. Near the close, sell the bot's positions (optional) and stop buying.
  4. Refuse new trades if the daily loss limit or day-trade limit is hit.
  5. Scan top % gainers, score their intraday momentum, and buy the best.

Exits: brokers with bracket orders (Alpaca) hold the take-profit and stop-loss
themselves. For brokers without them (Robinhood) the bot keeps a stop-loss
order resting at the broker and sells at market itself once the price reaches
the take-profit.

The bot only ever sells positions it opened (tracked in logs/state.json), so
it never touches stocks you already own in the same account.
"""

import csv
import json
import logging
import os
import time
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from .broker import BrokerError
from .config import Config
from . import strategy


log = logging.getLogger("autotrader")
NY = ZoneInfo("America/New_York")
PDT_EQUITY = 25_000.0
PDT_MAX_DAY_TRADES = 3
# Cancel a buy that hasn't filled after this long.
BUY_TIMEOUT = timedelta(minutes=5)


def _parse_time(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


class AutoTrader:
    def __init__(self, cfg: Config, broker, dry_run: bool = False, data_dir: str = "logs"):
        self.cfg = cfg
        self.broker = broker
        self.dry_run = dry_run
        self.data_dir = data_dir
        os.makedirs(data_dir, exist_ok=True)
        self.state_path = os.path.join(data_dir, "state.json")
        self.journal_path = os.path.join(data_dir, "trades.csv")
        self.state = self._load_state()
        self.halted_reason: str | None = None

    # ── Persistent state ─────────────────────────────────────────
    # date:    trading day the "traded" list belongs to
    # traded:  symbols already bought today (never re-bought the same day)
    # managed: positions the bot opened -> exit targets and order ids

    def _load_state(self) -> dict:
        try:
            with open(self.state_path) as f:
                state = json.load(f)
        except (FileNotFoundError, json.JSONDecodeError):
            state = {}
        state.setdefault("date", "")
        state.setdefault("traded", [])
        state.setdefault("managed", {})
        return state

    def _save_state(self) -> None:
        with open(self.state_path, "w") as f:
            json.dump(self.state, f, indent=1)

    def _roll_day(self, today: str) -> None:
        if self.state["date"] != today:
            self.state["date"] = today
            self.state["traded"] = []
            self._save_state()
            self.halted_reason = None

    def _journal(self, row: dict) -> None:
        new_file = not os.path.exists(self.journal_path)
        with open(self.journal_path, "a", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=list(row))
            if new_file:
                writer.writeheader()
            writer.writerow(row)

    # ── Main loop ────────────────────────────────────────────────

    def run_forever(self) -> None:
        log.info("Auto-trader started (%s%s). Ctrl+C to stop.",
                 "PAPER" if self.cfg.paper else "LIVE MONEY",
                 ", DRY RUN" if self.dry_run else "")
        while True:
            try:
                wait = self.tick()
            except BrokerError as e:
                log.error("Broker error: %s", e)
                wait = self.cfg.scan_interval_seconds
            except Exception:
                log.exception("Unexpected error")
                wait = self.cfg.scan_interval_seconds
            time.sleep(wait)

    def tick(self) -> int:
        """Run one cycle. Returns seconds to wait before the next one."""
        clock = self.broker.get_clock()
        now = _parse_time(clock["timestamp"])
        if not clock["is_open"]:
            next_open = _parse_time(clock["next_open"])
            log.info("Market closed. Next open %s.", next_open.astimezone(NY).strftime("%a %H:%M ET"))
            # Wake up shortly after the open, but re-check at least hourly.
            return int(min(max((next_open - now).total_seconds() + 5, 30), 3600))

        today = now.astimezone(NY).strftime("%Y-%m-%d")
        self._roll_day(today)

        session = self.broker.get_calendar_day(today)
        open_time = datetime.fromisoformat(f"{session['date']}T{session['open']}").replace(tzinfo=NY)
        close_time = _parse_time(clock["next_close"])
        mins_since_open = (now - open_time).total_seconds() / 60
        mins_to_close = (close_time - now).total_seconds() / 60

        positions = {p["symbol"]: p for p in self.broker.get_positions()}
        open_orders = self.broker.get_open_orders()
        self._log_positions(positions)
        self.manage_exits(positions, open_orders, now)

        flatten = self.cfg.flatten_minutes_before_close
        if flatten > 0 and mins_to_close <= flatten:
            self.flatten(positions, mins_to_close)
            return self.cfg.scan_interval_seconds

        if mins_since_open < self.cfg.skip_first_minutes:
            log.info("Waiting out the opening volatility (%.0f min in).", mins_since_open)
            return self.cfg.scan_interval_seconds
        if mins_to_close < self.cfg.skip_last_minutes:
            log.info("Too close to the close for new trades.")
            return self.cfg.scan_interval_seconds

        account = self.broker.get_account()
        reason = self.check_risk(account)
        if reason:
            if reason != self.halted_reason:
                log.warning("No new trades today: %s", reason)
                self.halted_reason = reason
            return self.cfg.scan_interval_seconds

        managed = set(self.state["managed"])
        slots = self.cfg.max_positions - len(managed)
        if slots <= 0:
            return self.cfg.scan_interval_seconds

        pending = {o["symbol"] for o in open_orders}
        exclude = set(positions) | pending | managed | set(self.state["traded"])
        candidates = self.find_candidates(exclude)
        buying_power = float(account["buying_power"])
        for cand in candidates[:slots]:
            buying_power -= self.enter(cand, buying_power, now)
        return self.cfg.scan_interval_seconds

    # ── Risk ─────────────────────────────────────────────────────

    def check_risk(self, account: dict) -> str | None:
        """Return why trading must pause, or None if it's OK to buy."""
        if account.get("trading_blocked") or account.get("account_blocked"):
            return "account is blocked from trading"
        equity = float(account["equity"])
        last_equity = float(account["last_equity"])
        if last_equity > 0:
            change_pct = (equity - last_equity) / last_equity * 100
            if change_pct <= -self.cfg.max_daily_loss_pct:
                return f"daily loss limit hit ({change_pct:.2f}% today)"
        if equity < PDT_EQUITY and int(account.get("daytrade_count", 0)) >= PDT_MAX_DAY_TRADES:
            return ("pattern day trader limit: accounts under $25k get 3 day trades "
                    "per 5 business days")
        return None

    # ── Scanning ─────────────────────────────────────────────────

    def find_candidates(self, exclude: set[str]) -> list[strategy.Candidate]:
        """Top gainers that pass the filters, best momentum score first."""
        cfg = self.cfg
        gainers = self.broker.get_top_gainers(top=50)
        picks = []
        for g in gainers:
            symbol, price, pct = g["symbol"], float(g["price"]), float(g["percent_change"])
            if symbol in exclude:
                continue
            if not (cfg.min_gain_pct <= pct <= cfg.max_gain_pct):
                continue
            if not (cfg.min_price <= price <= cfg.max_price):
                continue
            if strategy.position_size(cfg.trade_amount, price) < 1:
                continue
            try:
                asset = self.broker.get_asset(symbol)
                if not asset.get("tradable") or asset.get("exchange") == "OTC":
                    continue
                if self.broker.get_daily_volume(symbol) < cfg.min_volume:
                    continue
                bars = self.broker.get_bars(symbol)
            except BrokerError as e:
                log.debug("Skipping %s: %s", symbol, e)
                continue
            score, reasons = strategy.score_momentum(bars)
            log.info("  %-6s +%5.1f%%  $%8.2f  score %3.0f  | %s",
                     symbol, pct, price, score, "; ".join(reasons))
            if score >= cfg.min_score:
                last = float(bars["close"].iloc[-1])
                picks.append(strategy.Candidate(symbol, last, pct, score, reasons))
        picks.sort(key=lambda c: c.score, reverse=True)
        return picks

    # ── Entries ──────────────────────────────────────────────────

    def enter(self, cand: strategy.Candidate, buying_power: float, now: datetime) -> float:
        """Buy a candidate. Returns dollars committed."""
        qty = strategy.position_size(self.cfg.trade_amount, cand.price)
        cost = qty * cand.price
        if qty < 1 or cost > buying_power:
            log.info("Skipping %s: not enough buying power.", cand.symbol)
            return 0.0
        tp, sl = strategy.exit_prices(cand.price, self.cfg.take_profit_pct, self.cfg.stop_loss_pct)
        log.info("BUY %d %s @ ~$%.2f (score %.0f) | take profit $%.2f, stop loss $%.2f",
                 qty, cand.symbol, cand.price, cand.score, tp, sl)

        order_id = "dry-run"
        if not self.dry_run:
            if self.broker.supports_brackets:
                order = self.broker.buy_bracket(cand.symbol, qty, tp, sl)
            else:
                order = self.broker.buy_market(cand.symbol, qty)
            order_id = order.get("id", "")
            self.state["managed"][cand.symbol] = {
                "qty": qty, "take_profit": tp, "stop_loss": sl,
                "buy_order_id": order_id, "opened_at": now.isoformat(),
                "stop_order_id": None, "exiting": False, "filled": False,
            }

        self.state["traded"].append(cand.symbol)
        self._save_state()
        self._journal({
            "time": now.isoformat(timespec="seconds"), "action": "BUY",
            "symbol": cand.symbol, "qty": qty, "price": f"{cand.price:.2f}",
            "take_profit": f"{tp:.2f}", "stop_loss": f"{sl:.2f}",
            "gain_today_pct": f"{cand.gain_pct:.1f}", "score": f"{cand.score:.0f}",
            "dry_run": self.dry_run, "order_id": order_id,
        })
        return cost

    # ── Exits ────────────────────────────────────────────────────

    def manage_exits(self, positions: dict, open_orders: list[dict], now: datetime) -> None:
        """Track the bot's positions and, for brokers without brackets, run the exits."""
        open_ids = {o["id"] for o in open_orders}
        open_symbols = {o["symbol"] for o in open_orders}
        changed = False
        for symbol, m in list(self.state["managed"].items()):
            pos = positions.get(symbol)
            if pos is None:
                if m["buy_order_id"] in open_ids:
                    if now - _parse_time(m["opened_at"]) > BUY_TIMEOUT:
                        log.info("%s buy didn't fill in time; cancelling.", symbol)
                        self.broker.cancel_order(m["buy_order_id"])
                        del self.state["managed"][symbol]
                        changed = True
                    continue
                if m["filled"]:
                    log.info("%s position closed.", symbol)
                    if not m["exiting"]:
                        self._journal_exit(symbol, now, "CLOSED AT BROKER", m)
                else:
                    log.info("%s buy order ended without a fill.", symbol)
                del self.state["managed"][symbol]
                changed = True
                continue

            if not m["filled"]:
                m["filled"] = True
                changed = True
                if not self.broker.supports_brackets:
                    # Re-anchor the exits on the real fill price.
                    entry = float(pos["avg_entry_price"])
                    m["take_profit"], m["stop_loss"] = strategy.exit_prices(
                        entry, self.cfg.take_profit_pct, self.cfg.stop_loss_pct)
            if m["exiting"] and symbol not in open_symbols:
                # Our sell is gone but the shares are still here: it was rejected
                # or cancelled, so try again.
                log.warning("%s sell didn't go through; retrying.", symbol)
                m["exiting"] = False
                m["stop_order_id"] = None
                changed = True
            if self.broker.supports_brackets or m["exiting"]:
                continue

            qty = min(m["qty"], int(float(pos["qty"])))
            price = float(pos["current_price"])
            stop_open = m["stop_order_id"] in open_ids
            if price >= m["take_profit"] or (price <= m["stop_loss"] and not stop_open):
                kind = "TAKE PROFIT" if price >= m["take_profit"] else "STOP LOSS"
                log.info("%s %s: selling %d @ ~$%.2f", kind, symbol, qty, price)
                if stop_open:
                    self.broker.cancel_order(m["stop_order_id"])
                self.broker.sell_market(symbol, qty)
                m["exiting"] = True
                self._journal_exit(symbol, now, kind, m, price)
                changed = True
            elif not stop_open:
                order = self.broker.sell_stop(symbol, qty, m["stop_loss"])
                m["stop_order_id"] = order.get("id")
                log.info("%s: stop-loss order placed at $%.2f (take profit $%.2f watched by bot).",
                         symbol, m["stop_loss"], m["take_profit"])
                changed = True
        if changed:
            self._save_state()

    def flatten(self, positions: dict, mins_to_close: float) -> None:
        """Sell every position the bot opened (never anything else in the account)."""
        mine = [s for s in self.state["managed"] if s in positions
                and not self.state["managed"][s]["exiting"]]
        if not mine:
            return
        log.info("%.0f min to close: selling the bot's %d positions.", mins_to_close, len(mine))
        for symbol in mine:
            if not self.dry_run:
                qty = min(self.state["managed"][symbol]["qty"], int(float(positions[symbol]["qty"])))
                self.broker.close_position(symbol, qty)
            self.state["managed"][symbol]["exiting"] = True
        self._save_state()

    def _journal_exit(self, symbol: str, now: datetime, action: str, m: dict,
                      price: float | None = None) -> None:
        self._journal({
            "time": now.isoformat(timespec="seconds"), "action": action,
            "symbol": symbol, "qty": m["qty"], "price": f"{price:.2f}" if price else "",
            "take_profit": f"{m['take_profit']:.2f}", "stop_loss": f"{m['stop_loss']:.2f}",
            "gain_today_pct": "", "score": "", "dry_run": self.dry_run, "order_id": "",
        })

    def _log_positions(self, positions: dict) -> None:
        for symbol in self.state["managed"]:
            p = positions.get(symbol)
            if p:
                log.info("  holding %s x%s  entry $%.2f  now $%.2f  P/L $%.2f (%.2f%%)",
                         symbol, p["qty"], float(p["avg_entry_price"]),
                         float(p["current_price"]), float(p["unrealized_pl"]),
                         float(p["unrealized_plpc"]) * 100)
