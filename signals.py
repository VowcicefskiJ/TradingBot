"""
Technical analysis signal engine for Bitcoin trading.

Uses multiple indicators to generate buy/sell signals:
- RSI (Relative Strength Index)
- MACD (Moving Average Convergence Divergence)
- Bollinger Bands
- EMA Crossover (9/21)

Plus a fast-mover gate (ATR%, ROC, volume surge, range expansion) so
slow / sideways tape is filtered out — we only want directional moves
worth a day trade.
"""

import os
import pandas as pd
import numpy as np
from ta.momentum import RSIIndicator, ROCIndicator
from ta.trend import MACD, EMAIndicator
from ta.volatility import BollingerBands, AverageTrueRange
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
    velocity_score: float  # 0 to 100 — how "fast" the tape is moving
    fast_mover: bool
    atr_pct: float
    roc_pct: float
    volume_surge: float
    reasons: list[str]


# Fast-mover thresholds. Tuned for hourly BTC data; override via env.
ATR_PCT_MIN = float(os.getenv("FAST_MOVER_ATR_PCT", "0.5"))      # ATR as % of price
ROC_PCT_MIN = float(os.getenv("FAST_MOVER_ROC_PCT", "0.8"))      # |ROC over 6 bars|
VOL_SURGE_MIN = float(os.getenv("FAST_MOVER_VOL_SURGE", "1.2"))  # vol vs 20-bar avg
VELOCITY_MIN = float(os.getenv("FAST_MOVER_VELOCITY_MIN", "55")) # composite cutoff


def compute_indicators(df: pd.DataFrame) -> pd.DataFrame:
    """Add all technical indicators to the dataframe."""
    close = df["close"]
    high = df["high"]
    low = df["low"]
    volume = df["volume"]

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

    # --- Fast-mover indicators ---
    # ATR (14) and ATR as % of price — measures realized volatility
    atr = AverageTrueRange(high=high, low=low, close=close, window=14)
    df["atr"] = atr.average_true_range()
    df["atr_pct"] = (df["atr"] / close) * 100

    # Rate of Change over the last 6 bars — short-term momentum %
    df["roc_6"] = ROCIndicator(close=close, window=6).roc()

    # Volume surge: current vs 20-bar simple moving average
    df["vol_sma_20"] = volume.rolling(window=20, min_periods=5).mean()
    df["vol_surge"] = volume / df["vol_sma_20"].replace(0, np.nan)

    # Range expansion: current bar range vs avg of prior 20 bars
    df["bar_range"] = high - low
    df["range_avg_20"] = df["bar_range"].rolling(window=20, min_periods=5).mean()
    df["range_expansion"] = df["bar_range"] / df["range_avg_20"].replace(0, np.nan)

    return df


def _velocity_score(atr_pct: float, roc_abs: float, vol_surge: float,
                    range_exp: float) -> float:
    """
    Composite 0–100 score representing how fast the tape is moving.
    Each component is scaled relative to its threshold and capped.
    """
    # Each component contributes up to 25 points.
    atr_pts = min(25.0, (atr_pct / ATR_PCT_MIN) * 15) if ATR_PCT_MIN > 0 else 0
    roc_pts = min(25.0, (roc_abs / ROC_PCT_MIN) * 15) if ROC_PCT_MIN > 0 else 0
    vol_pts = min(25.0, (vol_surge / VOL_SURGE_MIN) * 15) if VOL_SURGE_MIN > 0 else 0
    rng_pts = min(25.0, range_exp * 12.5)  # 2x avg range = full 25 pts
    return max(0.0, atr_pts + roc_pts + vol_pts + rng_pts)


def analyze(df: pd.DataFrame) -> AnalysisResult:
    """
    Analyze the latest data point and generate a trading signal.

    Scoring system (-100 to +100):
      RSI:       up to +/- 30 points
      MACD:      up to +/- 25 points
      Bollinger: up to +/- 25 points
      EMA cross: up to +/- 20 points

    The fast-mover gate then checks whether the tape is moving fast
    enough to be worth a day trade. If not, BUY/SELL signals are
    downgraded to HOLD with a "slow tape" reason.
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
    elif rsi > 70:
        pts = 30 * (rsi - 70) / 30
        score -= pts
        reasons.append(f"RSI overbought at {rsi:.1f} (-{pts:.0f})")
    else:
        reasons.append(f"RSI neutral at {rsi:.1f}")

    rsi_label = "OVERSOLD" if rsi < 30 else "OVERBOUGHT" if rsi > 70 else "NEUTRAL"

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

    # --- Fast-mover gate -------------------------------------------------
    atr_pct = float(latest.get("atr_pct") or 0.0)
    roc_pct = float(latest.get("roc_6") or 0.0)
    vol_surge = float(latest.get("vol_surge") or 0.0)
    range_exp = float(latest.get("range_expansion") or 0.0)
    if np.isnan(atr_pct):
        atr_pct = 0.0
    if np.isnan(roc_pct):
        roc_pct = 0.0
    if np.isnan(vol_surge):
        vol_surge = 0.0
    if np.isnan(range_exp):
        range_exp = 0.0

    velocity = _velocity_score(atr_pct, abs(roc_pct), vol_surge, range_exp)
    fast_mover = (
        velocity >= VELOCITY_MIN
        and atr_pct >= ATR_PCT_MIN
        and abs(roc_pct) >= ROC_PCT_MIN
    )

    reasons.append(
        f"Velocity {velocity:.0f}/100 (ATR%={atr_pct:.2f}, "
        f"ROC6={roc_pct:+.2f}%, vol×{vol_surge:.2f})"
    )

    # Determine raw directional signal first
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

    # Slow tape gate: don't day-trade chop. Downgrade actionable signals.
    if not fast_mover and signal != Signal.HOLD:
        reasons.append(
            f"SLOW TAPE — signal {signal.value} suppressed "
            f"(need ATR%>={ATR_PCT_MIN}, |ROC6|>={ROC_PCT_MIN}%, "
            f"velocity>={VELOCITY_MIN:.0f})"
        )
        signal = Signal.HOLD

    return AnalysisResult(
        signal=signal,
        score=score,
        price=price,
        rsi=rsi,
        macd_signal=macd_label,
        bb_signal=bb_label,
        ema_signal=ema_label,
        velocity_score=velocity,
        fast_mover=fast_mover,
        atr_pct=atr_pct,
        roc_pct=roc_pct,
        volume_surge=vol_surge,
        reasons=reasons,
    )
