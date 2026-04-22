"""
Technical analysis signal engine for day-trading stocks.

Indicators:
  - RSI (Relative Strength Index)
  - MACD (Moving Average Convergence Divergence)
  - Bollinger Bands
  - EMA 9/21 crossover
  - VWAP (Volume Weighted Average Price, intraday)
  - Volume surge vs. recent average

Outputs a BUY / HOLD / SELL signal with a score and plain-English reasoning.
This module does NOT place trades.
"""

from __future__ import annotations

import pandas as pd
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
    ticker: str
    signal: Signal
    score: float
    price: float
    rsi: float
    macd_signal: str
    bb_signal: str
    ema_signal: str
    vwap_signal: str
    volume_signal: str
    reasons: list[str]


def compute_indicators(df: pd.DataFrame) -> pd.DataFrame:
    """Add RSI, MACD, Bollinger, EMA, VWAP, and avg-volume columns."""
    close = df["close"]

    df["rsi"] = RSIIndicator(close=close, window=14).rsi()

    macd = MACD(close=close, window_slow=26, window_fast=12, window_sign=9)
    df["macd"] = macd.macd()
    df["macd_signal"] = macd.macd_signal()

    bb = BollingerBands(close=close, window=20, window_dev=2)
    df["bb_upper"] = bb.bollinger_hband()
    df["bb_middle"] = bb.bollinger_mavg()
    df["bb_lower"] = bb.bollinger_lband()

    df["ema_9"] = EMAIndicator(close=close, window=9).ema_indicator()
    df["ema_21"] = EMAIndicator(close=close, window=21).ema_indicator()

    typical = (df["high"] + df["low"] + df["close"]) / 3
    cum_vp = (typical * df["volume"]).cumsum()
    cum_vol = df["volume"].cumsum().replace(0, pd.NA)
    df["vwap"] = cum_vp / cum_vol

    df["vol_avg_20"] = df["volume"].rolling(window=20).mean()

    return df


def analyze(df: pd.DataFrame, ticker: str = "") -> AnalysisResult:
    """
    Score the latest bar from -100 (strong sell) to +100 (strong buy).

    Weights:
      RSI:         +/- 25
      MACD:        +/- 20
      Bollinger:   +/- 20
      EMA 9/21:    +/- 15
      VWAP:        +/- 10
      Vol surge:   +/- 10
    """
    df = compute_indicators(df).copy()
    latest = df.iloc[-1]
    prev = df.iloc[-2]

    score = 0.0
    reasons: list[str] = []

    # RSI
    rsi = latest["rsi"]
    if pd.isna(rsi):
        rsi_label = "N/A"
    elif rsi < 30:
        pts = 25 * (30 - rsi) / 30
        score += pts
        reasons.append(f"RSI oversold at {rsi:.1f} (+{pts:.0f})")
        rsi_label = "OVERSOLD"
    elif rsi > 70:
        pts = 25 * (rsi - 70) / 30
        score -= pts
        reasons.append(f"RSI overbought at {rsi:.1f} (-{pts:.0f})")
        rsi_label = "OVERBOUGHT"
    else:
        rsi_label = "NEUTRAL"
        reasons.append(f"RSI neutral at {rsi:.1f}")

    # MACD
    macd_val = latest["macd"]
    macd_sig = latest["macd_signal"]
    prev_macd = prev["macd"]
    prev_sig = prev["macd_signal"]

    if pd.notna(prev_macd) and pd.notna(macd_val):
        if prev_macd <= prev_sig and macd_val > macd_sig:
            score += 20
            reasons.append("MACD bullish crossover (+20)")
            macd_label = "BULLISH CROSS"
        elif prev_macd >= prev_sig and macd_val < macd_sig:
            score -= 20
            reasons.append("MACD bearish crossover (-20)")
            macd_label = "BEARISH CROSS"
        elif macd_val > macd_sig:
            score += 8
            reasons.append("MACD above signal (+8)")
            macd_label = "BULLISH"
        else:
            score -= 8
            reasons.append("MACD below signal (-8)")
            macd_label = "BEARISH"
    else:
        macd_label = "N/A"

    # Bollinger Bands
    price = latest["close"]
    bb_upper = latest["bb_upper"]
    bb_lower = latest["bb_lower"]
    bb_mid = latest["bb_middle"]

    if pd.isna(bb_upper):
        bb_label = "N/A"
    elif price <= bb_lower:
        score += 20
        reasons.append("Price at/below lower Bollinger Band (+20)")
        bb_label = "OVERSOLD"
    elif price >= bb_upper:
        score -= 20
        reasons.append("Price at/above upper Bollinger Band (-20)")
        bb_label = "OVERBOUGHT"
    elif price < bb_mid:
        pts = 8 * (bb_mid - price) / max(bb_mid - bb_lower, 1e-9)
        score += pts
        reasons.append(f"Price below BB midline (+{pts:.0f})")
        bb_label = "LOWER HALF"
    else:
        pts = 8 * (price - bb_mid) / max(bb_upper - bb_mid, 1e-9)
        score -= pts
        reasons.append(f"Price above BB midline (-{pts:.0f})")
        bb_label = "UPPER HALF"

    # EMA 9/21
    ema9 = latest["ema_9"]
    ema21 = latest["ema_21"]
    prev_ema9 = prev["ema_9"]
    prev_ema21 = prev["ema_21"]

    if pd.notna(ema9) and pd.notna(ema21) and pd.notna(prev_ema9) and pd.notna(prev_ema21):
        if prev_ema9 <= prev_ema21 and ema9 > ema21:
            score += 15
            reasons.append("EMA 9/21 bullish crossover (+15)")
            ema_label = "BULLISH CROSS"
        elif prev_ema9 >= prev_ema21 and ema9 < ema21:
            score -= 15
            reasons.append("EMA 9/21 bearish crossover (-15)")
            ema_label = "BEARISH CROSS"
        elif ema9 > ema21:
            score += 6
            reasons.append("EMA 9 above EMA 21 (+6)")
            ema_label = "BULLISH"
        else:
            score -= 6
            reasons.append("EMA 9 below EMA 21 (-6)")
            ema_label = "BEARISH"
    else:
        ema_label = "N/A"

    # VWAP — price above VWAP is bullish intraday, below is bearish
    vwap = latest["vwap"]
    if pd.isna(vwap):
        vwap_label = "N/A"
    elif price > vwap * 1.005:
        score += 10
        reasons.append(f"Price above VWAP ${vwap:.2f} (+10)")
        vwap_label = "ABOVE"
    elif price < vwap * 0.995:
        score -= 10
        reasons.append(f"Price below VWAP ${vwap:.2f} (-10)")
        vwap_label = "BELOW"
    else:
        vwap_label = "AT VWAP"
        reasons.append(f"Price near VWAP ${vwap:.2f}")

    # Volume surge — current bar vs 20-bar average
    vol = latest["volume"]
    vol_avg = latest["vol_avg_20"]
    if pd.isna(vol_avg) or vol_avg == 0:
        volume_label = "N/A"
    else:
        ratio = vol / vol_avg
        if ratio >= 2.0:
            score += 10
            reasons.append(f"Volume surge {ratio:.1f}x avg (+10)")
            volume_label = "SURGE"
        elif ratio >= 1.3:
            score += 4
            reasons.append(f"Elevated volume {ratio:.1f}x avg (+4)")
            volume_label = "ELEVATED"
        elif ratio <= 0.5:
            score -= 4
            reasons.append(f"Weak volume {ratio:.1f}x avg (-4)")
            volume_label = "WEAK"
        else:
            volume_label = "NORMAL"

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
        ticker=ticker.upper(),
        signal=signal,
        score=score,
        price=float(price),
        rsi=float(rsi) if pd.notna(rsi) else float("nan"),
        macd_signal=macd_label,
        bb_signal=bb_label,
        ema_signal=ema_label,
        vwap_signal=vwap_label,
        volume_signal=volume_label,
        reasons=reasons,
    )
