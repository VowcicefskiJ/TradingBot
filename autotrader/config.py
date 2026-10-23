"""
Settings for the autonomous trader, loaded from environment / .env.
"""

import os
from dataclasses import dataclass


def _float(name: str, default: float) -> float:
    return float(os.getenv(name, str(default)))


def _int(name: str, default: int) -> int:
    return int(os.getenv(name, str(default)))


def _bool(name: str, default: bool) -> bool:
    return os.getenv(name, str(default)).strip().lower() in ("1", "true", "yes", "on")


@dataclass
class Config:
    # "alpaca" or "robinhood"
    broker: str
    # Alpaca credentials
    api_key: str
    api_secret: str
    # True only for an Alpaca paper account. Robinhood is always real money.
    paper: bool

    # How much to spend on each trade, in dollars
    trade_amount: float
    # Never hold more than this many positions at once
    max_positions: int

    # Exit targets (percent, relative to the fill price)
    take_profit_pct: float
    stop_loss_pct: float

    # Scanner filters: only look at stocks up between these % today
    min_gain_pct: float
    max_gain_pct: float
    min_price: float
    max_price: float
    min_volume: int

    # Minimum momentum score (0-100) required to buy
    min_score: float

    # Stop opening new trades if today's equity drops by this % or more
    max_daily_loss_pct: float
    # Close everything this many minutes before the market closes (0 = hold overnight)
    flatten_minutes_before_close: int
    # Don't open new trades this many minutes after the open / before the close
    skip_first_minutes: int
    skip_last_minutes: int

    # Seconds between scans
    scan_interval_seconds: int

    @classmethod
    def from_env(cls) -> "Config":
        broker = os.getenv("BROKER", "alpaca").strip().lower()
        return cls(
            broker=broker,
            api_key=os.getenv("ALPACA_API_KEY", "").strip(),
            api_secret=os.getenv("ALPACA_API_SECRET", "").strip(),
            paper=broker == "alpaca" and _bool("ALPACA_PAPER", True),
            trade_amount=_float("TRADE_AMOUNT", 1000.0),
            max_positions=_int("MAX_POSITIONS", 3),
            take_profit_pct=_float("TAKE_PROFIT_PCT", 4.0),
            stop_loss_pct=_float("STOP_LOSS_PCT", 2.0),
            min_gain_pct=_float("MIN_GAIN_PCT", 5.0),
            max_gain_pct=_float("MAX_GAIN_PCT", 30.0),
            min_price=_float("MIN_PRICE", 5.0),
            max_price=_float("MAX_PRICE", 500.0),
            min_volume=_int("MIN_VOLUME", 500_000),
            min_score=_float("MIN_SCORE", 60.0),
            max_daily_loss_pct=_float("MAX_DAILY_LOSS_PCT", 3.0),
            flatten_minutes_before_close=_int("FLATTEN_MINUTES_BEFORE_CLOSE", 10),
            skip_first_minutes=_int("SKIP_FIRST_MINUTES", 15),
            skip_last_minutes=_int("SKIP_LAST_MINUTES", 30),
            scan_interval_seconds=_int("SCAN_INTERVAL_SECONDS", 60),
        )

    def validate(self) -> list[str]:
        """Return a list of problems; empty means the config is usable."""
        problems = []
        if self.broker not in ("alpaca", "robinhood"):
            problems.append("BROKER must be 'alpaca' or 'robinhood'")
        if self.broker == "alpaca" and (not self.api_key or not self.api_secret):
            problems.append("ALPACA_API_KEY and ALPACA_API_SECRET must be set")
        if self.trade_amount <= 0:
            problems.append("TRADE_AMOUNT must be positive")
        if self.max_positions < 1:
            problems.append("MAX_POSITIONS must be at least 1")
        if self.take_profit_pct <= 0 or self.stop_loss_pct <= 0:
            problems.append("TAKE_PROFIT_PCT and STOP_LOSS_PCT must be positive")
        if self.stop_loss_pct >= 100:
            problems.append("STOP_LOSS_PCT must be below 100")
        if self.min_gain_pct >= self.max_gain_pct:
            problems.append("MIN_GAIN_PCT must be below MAX_GAIN_PCT")
        if self.min_price >= self.max_price:
            problems.append("MIN_PRICE must be below MAX_PRICE")
        return problems
