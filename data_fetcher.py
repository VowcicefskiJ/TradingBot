"""
Market data + screener for penny-stock day trading.

READ-ONLY. This module only fetches public market data. It does not place,
modify, or cancel trades, and does not require any login or credentials.

Data source: yfinance (free, no API key).
"""

from __future__ import annotations

import pandas as pd
import yfinance as yf


def get_intraday(ticker: str, period: str = "5d", interval: str = "5m") -> pd.DataFrame:
    """
    Fetch intraday OHLCV bars for a single ticker.

    Returns columns: timestamp, open, high, low, close, volume.
    """
    t = yf.Ticker(ticker)
    hist = t.history(period=period, interval=interval, auto_adjust=False)
    if hist.empty:
        raise ValueError(f"No historical data for {ticker}")

    hist = hist.reset_index().rename(columns={
        "Datetime": "timestamp",
        "Date": "timestamp",
        "Open": "open",
        "High": "high",
        "Low": "low",
        "Close": "close",
        "Volume": "volume",
    })
    return hist[["timestamp", "open", "high", "low", "close", "volume"]].sort_values(
        "timestamp"
    ).reset_index(drop=True)


def get_quote(ticker: str) -> dict:
    """Fetch a lightweight current quote for a ticker."""
    t = yf.Ticker(ticker)
    info = t.fast_info
    return {
        "ticker": ticker.upper(),
        "price": float(info.get("last_price") or info.get("lastPrice") or 0.0),
        "prev_close": float(info.get("previous_close") or info.get("previousClose") or 0.0),
        "day_high": float(info.get("day_high") or info.get("dayHigh") or 0.0),
        "day_low": float(info.get("day_low") or info.get("dayLow") or 0.0),
        "volume": float(info.get("last_volume") or info.get("lastVolume") or 0.0),
    }


def screen_penny_gainers(
    min_price: float = 0.50,
    max_price: float = 2.00,
    min_volume: float = 1_000_000,
    min_pct_change: float = 3.0,
    limit: int = 25,
) -> pd.DataFrame:
    """
    Find US stocks priced in [min_price, max_price] with strong intraday gains
    and enough volume to realistically enter and exit.

    Returns a DataFrame sorted by % change descending with columns:
        ticker, price, pct_change, volume, day_high, day_low.
    """
    query = yf.EquityQuery("and", [
        yf.EquityQuery("gt", ["percentchange", min_pct_change]),
        yf.EquityQuery("eq", ["region", "us"]),
        yf.EquityQuery("gt", ["intradayprice", min_price]),
        yf.EquityQuery("lt", ["intradayprice", max_price]),
        yf.EquityQuery("gt", ["dayvolume", min_volume]),
    ])

    results = yf.screen(query, sortField="percentchange", sortAsc=False, size=limit)
    quotes = results.get("quotes", []) if isinstance(results, dict) else []

    rows = []
    for q in quotes:
        price = q.get("regularMarketPrice")
        pct = q.get("regularMarketChangePercent")
        vol = q.get("regularMarketVolume")
        if price is None or pct is None:
            continue
        rows.append({
            "ticker": q.get("symbol", ""),
            "price": float(price),
            "pct_change": float(pct),
            "volume": float(vol or 0),
            "day_high": float(q.get("regularMarketDayHigh") or 0),
            "day_low": float(q.get("regularMarketDayLow") or 0),
        })

    df = pd.DataFrame(rows)
    if df.empty:
        return df
    return df.sort_values("pct_change", ascending=False).reset_index(drop=True)
