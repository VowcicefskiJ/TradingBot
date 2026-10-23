"""
Thin Alpaca REST client: account, clock, market data, screener and orders.

Docs: https://docs.alpaca.markets/reference
"""

from datetime import datetime, timedelta, timezone

import pandas as pd
import requests


PAPER_URL = "https://paper-api.alpaca.markets"
LIVE_URL = "https://api.alpaca.markets"
DATA_URL = "https://data.alpaca.markets"


class BrokerError(Exception):
    pass


class AlpacaBroker:
    # Alpaca holds take-profit and stop-loss exits itself.
    supports_brackets = True

    def __init__(self, api_key: str, api_secret: str, paper: bool = True,
                 data_feed: str = "iex", timeout: float = 15.0):
        self.trading_url = PAPER_URL if paper else LIVE_URL
        self.paper = paper
        self.data_feed = data_feed
        self.timeout = timeout
        self.session = requests.Session()
        self.session.headers.update({
            "APCA-API-KEY-ID": api_key,
            "APCA-API-SECRET-KEY": api_secret,
        })

    # ── HTTP helpers ─────────────────────────────────────────────

    def _request(self, method: str, url: str, **kwargs):
        resp = self.session.request(method, url, timeout=self.timeout, **kwargs)
        if resp.status_code >= 400:
            raise BrokerError(f"{method} {url} -> {resp.status_code}: {resp.text}")
        if resp.status_code == 204 or not resp.content:
            return None
        return resp.json()

    def _trading(self, method: str, path: str, **kwargs):
        return self._request(method, self.trading_url + path, **kwargs)

    def _data(self, path: str, **kwargs):
        return self._request("GET", DATA_URL + path, **kwargs)

    # ── Account ──────────────────────────────────────────────────

    def get_account(self) -> dict:
        return self._trading("GET", "/v2/account")

    def get_clock(self) -> dict:
        return self._trading("GET", "/v2/clock")

    def get_calendar_day(self, date: str) -> dict | None:
        """Trading session for a YYYY-MM-DD date ({date, open, close}), or None if closed."""
        days = self._trading("GET", "/v2/calendar", params={"start": date, "end": date})
        return days[0] if days else None

    def get_positions(self) -> list[dict]:
        return self._trading("GET", "/v2/positions")

    def get_open_orders(self) -> list[dict]:
        return self._trading("GET", "/v2/orders",
                             params={"status": "open", "limit": 500, "nested": "false"})

    def get_asset(self, symbol: str) -> dict:
        return self._trading("GET", f"/v2/assets/{symbol}")

    # ── Market data ──────────────────────────────────────────────

    def get_top_gainers(self, top: int = 50) -> list[dict]:
        """Today's biggest % gainers: [{symbol, percent_change, change, price}, ...]."""
        data = self._data("/v1beta1/screener/stocks/movers", params={"top": top})
        return data.get("gainers", [])

    def get_bars(self, symbol: str, timeframe: str = "5Min",
                 lookback: timedelta = timedelta(days=3)) -> pd.DataFrame:
        """Intraday OHLCV bars, oldest first."""
        start = (datetime.now(timezone.utc) - lookback).isoformat()
        params = {"timeframe": timeframe, "start": start, "limit": 10000,
                  "feed": self.data_feed, "adjustment": "raw"}
        bars = []
        while True:
            data = self._data(f"/v2/stocks/{symbol}/bars", params=params)
            bars.extend(data.get("bars") or [])
            token = data.get("next_page_token")
            if not token:
                break
            params["page_token"] = token

        if not bars:
            return pd.DataFrame(columns=["timestamp", "open", "high", "low", "close", "volume"])
        df = pd.DataFrame(bars).rename(columns={
            "t": "timestamp", "o": "open", "h": "high", "l": "low", "c": "close", "v": "volume",
        })
        df["timestamp"] = pd.to_datetime(df["timestamp"])
        return df[["timestamp", "open", "high", "low", "close", "volume"]]

    def get_daily_volume(self, symbol: str) -> int:
        """Today's cumulative share volume from the latest daily bar snapshot."""
        data = self._data(f"/v2/stocks/{symbol}/snapshot", params={"feed": self.data_feed})
        daily = data.get("dailyBar") or {}
        return int(daily.get("v", 0))

    # ── Orders ───────────────────────────────────────────────────

    def buy_bracket(self, symbol: str, qty: int, take_profit: float,
                    stop_loss: float) -> dict:
        """
        Market buy with an attached take-profit limit and stop-loss.
        The exits live at the broker, so they still fire if this bot crashes.
        """
        order = {
            "symbol": symbol,
            "qty": str(qty),
            "side": "buy",
            "type": "market",
            "time_in_force": "gtc",
            "order_class": "bracket",
            "take_profit": {"limit_price": f"{take_profit:.2f}"},
            "stop_loss": {"stop_price": f"{stop_loss:.2f}"},
        }
        return self._trading("POST", "/v2/orders", json=order)

    def cancel_order(self, order_id: str) -> None:
        self._trading("DELETE", f"/v2/orders/{order_id}")

    def close_position(self, symbol: str, qty: int) -> None:
        """Cancel the symbol's open orders (bracket legs) and market-sell qty shares."""
        for order in self.get_open_orders():
            if order["symbol"] == symbol:
                self.cancel_order(order["id"])
        self._trading("DELETE", f"/v2/positions/{symbol}", params={"qty": str(qty)})
