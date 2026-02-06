"""
Technical analysis signal engine for Bitcoin trading.

Uses multiple indicators to generate buy/sell signals:
- RSI (Relative Strength Index)
- MACD (Moving Average Convergence Divergence)
- Bollinger Bands
- EMA Crossover (9/21)
"""

import pandas as pd
import numpy as np
from ta.momentum import RSIIndicator
from ta.trend import MACD, EMAIndicator
from ta.volatility import BollingerBands
from dataclasses import dataclass
from enum import Enum


class Signal(Enum):
    STRONG_BUY = "STRONG BUY"
    BUY = "BUY"
    HOLD = "HOLD"
    SELL = "SELL"
    STRONG_SELL = "STRONG SELL"


@dataclass
class AnalysisResult:
    signal: Signal
    score: float  # -100 to +100
    price: float
    rsi: float
    macd_signal: str
    bb_signal: str
    ema_signal: str
    reasons: list[str]


def compute_indicators(df: pd.DataFrame) -> pd.DataFrame:
    """Add all technical indicators to the dataframe."""
    close = df["close"]

    # RSI (14-period)
    df["rsi"] = RSIIndicator(close=close, window=14).rsi()

    # MACD
    macd = MACD(close=close, window_slow=26, window_fast=12, window_sign=9)
    df["macd"] = macd.macd()
    df["macd_signal"] = macd.macd_signal()
    df["macd_hist"] = macd.macd_diff()

    # Bollinger Bands (20-period, 2 std)
    bb = BollingerBands(close=close, window=20, window_dev=2)
    df["bb_upper"] = bb.bollinger_hband()
    df["bb_middle"] = bb.bollinger_mavg()
    df["bb_lower"] = bb.bollinger_lband()

    # EMA 9 and 21
    df["ema_9"] = EMAIndicator(close=close, window=9).ema_indicator()
    df["ema_21"] = EMAIndicator(close=close, window=21).ema_indicator()

    return df


def analyze(df: pd.DataFrame) -> AnalysisResult:
    """
    Analyze the latest data point and generate a trading signal.

    Scoring system (-100 to +100):
      RSI:       up to +/- 30 points
      MACD:      up to +/- 25 points
      Bollinger: up to +/- 25 points
      EMA cross: up to +/- 20 points
    """
    df = compute_indicators(df)
    latest = df.iloc[-1]
    prev = df.iloc[-2]

    score = 0.0
    reasons = []

    # --- RSI Analysis (max +/- 30) ---
    rsi = latest["rsi"]
    if rsi < 30:
        pts = 30 * (30 - rsi) / 30  # stronger signal the lower RSI goes
        score += pts
        reasons.append(f"RSI oversold at {rsi:.1f} (+{pts:.0f})")
        rsi_label = "OVERSOLD"
    elif rsi > 70:
        pts = 30 * (rsi - 70) / 30
        score -= pts
        reasons.append(f"RSI overbought at {rsi:.1f} (-{pts:.0f})")
        rsi_label = "OVERBOUGHT"
    else:
        rsi_label = "NEUTRAL"
        reasons.append(f"RSI neutral at {rsi:.1f}")

    # --- MACD Analysis (max +/- 25) ---
    macd_val = latest["macd"]
    macd_sig = latest["macd_signal"]
    prev_macd = prev["macd"]
    prev_sig = prev["macd_signal"]

    # Crossover detection
    if prev_macd <= prev_sig and macd_val > macd_sig:
        score += 25
        reasons.append("MACD bullish crossover (+25)")
        macd_label = "BULLISH CROSS"
    elif prev_macd >= prev_sig and macd_val < macd_sig:
        score -= 25
        reasons.append("MACD bearish crossover (-25)")
        macd_label = "BEARISH CROSS"
    elif macd_val > macd_sig:
        score += 10
        reasons.append("MACD above signal line (+10)")
        macd_label = "BULLISH"
    else:
        score -= 10
        reasons.append("MACD below signal line (-10)")
        macd_label = "BEARISH"

    # --- Bollinger Bands Analysis (max +/- 25) ---
    price = latest["close"]
    bb_upper = latest["bb_upper"]
    bb_lower = latest["bb_lower"]
    bb_mid = latest["bb_middle"]

    if price <= bb_lower:
        score += 25
        reasons.append(f"Price at/below lower Bollinger Band (+25)")
        bb_label = "OVERSOLD"
    elif price >= bb_upper:
        score -= 25
        reasons.append(f"Price at/above upper Bollinger Band (-25)")
        bb_label = "OVERBOUGHT"
    elif price < bb_mid:
        pts = 10 * (bb_mid - price) / (bb_mid - bb_lower)
        score += pts
        reasons.append(f"Price below BB midline (+{pts:.0f})")
        bb_label = "LOWER HALF"
    else:
        pts = 10 * (price - bb_mid) / (bb_upper - bb_mid)
        score -= pts
        reasons.append(f"Price above BB midline (-{pts:.0f})")
        bb_label = "UPPER HALF"

    # --- EMA Crossover Analysis (max +/- 20) ---
    ema9 = latest["ema_9"]
    ema21 = latest["ema_21"]
    prev_ema9 = prev["ema_9"]
    prev_ema21 = prev["ema_21"]

    if prev_ema9 <= prev_ema21 and ema9 > ema21:
        score += 20
        reasons.append("EMA 9/21 bullish crossover (+20)")
        ema_label = "BULLISH CROSS"
    elif prev_ema9 >= prev_ema21 and ema9 < ema21:
        score -= 20
        reasons.append("EMA 9/21 bearish crossover (-20)")
        ema_label = "BEARISH CROSS"
    elif ema9 > ema21:
        score += 8
        reasons.append("EMA 9 above EMA 21 (+8)")
        ema_label = "BULLISH"
    else:
        score -= 8
        reasons.append("EMA 9 below EMA 21 (-8)")
        ema_label = "BEARISH"

    # --- Determine overall signal ---
    score = max(-100, min(100, score))
    if score >= 50:
        signal = Signal.STRONG_BUY
    elif score >= 20:
        signal = Signal.BUY
    elif score <= -50:
        signal = Signal.STRONG_SELL
    elif score <= -20:
        signal = Signal.SELL
    else:
        signal = Signal.HOLD

    return AnalysisResult(
        signal=signal,
        score=score,
        price=price,
        rsi=rsi,
        macd_signal=macd_label,
        bb_signal=bb_label,
        ema_signal=ema_label,
        reasons=reasons,
    )
