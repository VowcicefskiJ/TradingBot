"""
Entry and exit logic.

Entry: a stock that is already up a lot today is only bought if the intraday
5-minute chart says the move is still going (not fading or overextended).

Momentum score (0-100):
  Price above today's VWAP          +25
  EMA 9 above EMA 21                +20
  RSI(14) in the 50-75 "strong but not stretched" zone  +20
  Higher than 3 bars ago            +15
  Within 3% of today's high         +20 (less the further below the high)

RSI above 80 vetoes the trade outright: chasing a blow-off top is how momentum
traders give back their gains.
"""

import math
from dataclasses import dataclass, field

import pandas as pd


MIN_BARS = 30
RSI_VETO = 80.0
MARKET_TZ = "America/New_York"


@dataclass
class Candidate:
    symbol: str
    price: float
    gain_pct: float
    score: float = 0.0
    reasons: list[str] = field(default_factory=list)


def ema(series: pd.Series, window: int) -> pd.Series:
    return series.ewm(span=window, adjust=False).mean()


def rsi(series: pd.Series, window: int = 14) -> pd.Series:
    """Wilder's RSI."""
    delta = series.diff()
    gain = delta.clip(lower=0).ewm(alpha=1 / window, adjust=False).mean()
    loss = (-delta.clip(upper=0)).ewm(alpha=1 / window, adjust=False).mean()
    rs = gain / loss.replace(0, math.nan)
    return (100 - 100 / (1 + rs)).fillna(100.0)


def todays_bars(bars: pd.DataFrame) -> pd.DataFrame:
    """Bars from the most recent trading day (in New York time)."""
    local_dates = bars["timestamp"].dt.tz_convert(MARKET_TZ).dt.date
    return bars[local_dates == local_dates.iloc[-1]]


def score_momentum(bars: pd.DataFrame) -> tuple[float, list[str]]:
    """Score how healthy the intraday uptrend looks. Returns (score, reasons)."""
    if len(bars) < MIN_BARS:
        return 0.0, [f"only {len(bars)} bars, need {MIN_BARS}"]

    close = bars["close"]
    price = close.iloc[-1]
    today = todays_bars(bars)
    score = 0.0
    reasons = []

    r = rsi(close).iloc[-1]
    if r >= RSI_VETO:
        return 0.0, [f"RSI {r:.0f} overextended (veto)"]

    typical = (today["high"] + today["low"] + today["close"]) / 3
    vwap = (typical * today["volume"]).sum() / max(today["volume"].sum(), 1)
    if price > vwap:
        score += 25
        reasons.append(f"above VWAP {vwap:.2f} (+25)")
    else:
        reasons.append(f"below VWAP {vwap:.2f}")

    e9, e21 = ema(close, 9).iloc[-1], ema(close, 21).iloc[-1]
    if e9 > e21:
        score += 20
        reasons.append("EMA9 > EMA21 (+20)")
    else:
        reasons.append("EMA9 < EMA21")

    if 50 <= r <= 75:
        score += 20
        reasons.append(f"RSI {r:.0f} in trend zone (+20)")
    else:
        reasons.append(f"RSI {r:.0f} outside 50-75")

    if price > close.iloc[-4]:
        score += 15
        reasons.append("rising over last 3 bars (+15)")
    else:
        reasons.append("falling over last 3 bars")

    day_high = today["high"].max()
    below_high_pct = (day_high - price) / day_high * 100
    pts = max(0.0, 20 * (1 - max(0.0, below_high_pct - 3) / 3))
    score += pts
    reasons.append(f"{below_high_pct:.1f}% below day high (+{pts:.0f})")

    return score, reasons


def round_price(price: float) -> float:
    """Alpaca accepts penny increments for stocks at or above $1."""
    return round(price + 1e-9, 2)


def position_size(trade_amount: float, price: float) -> int:
    """Whole shares affordable with trade_amount (bracket orders need whole shares)."""
    if price <= 0:
        return 0
    return int(trade_amount // price)


def exit_prices(entry: float, take_profit_pct: float, stop_loss_pct: float) -> tuple[float, float]:
    """(take_profit_limit, stop_loss_stop) around an entry price."""
    tp = round_price(entry * (1 + take_profit_pct / 100))
    sl = round_price(entry * (1 - stop_loss_pct / 100))
    # Alpaca requires TP >= entry + $0.01 and SL <= entry - $0.01
    tp = max(tp, round_price(entry + 0.01))
    sl = min(sl, round_price(entry - 0.01))
    return tp, sl
