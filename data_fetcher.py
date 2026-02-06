"""
Fetches Bitcoin price data from Robinhood using robin_stocks.

IMPORTANT: This module is READ-ONLY. It fetches market data only.
It must NEVER import or call any order/trading functions such as:
  - rh.orders.*
  - rh.crypto.order_*
  - Any function that places, modifies, or cancels trades.
"""

import robin_stocks.robinhood as rh
import pandas as pd
from datetime import datetime


def login(username: str, password: str, totp: str | None = None):
    """Log in to Robinhood."""
    kwargs = {"username": username, "password": password, "store_session": True}
    if totp:
        import pyotp
        kwargs["mfa_code"] = pyotp.TOTP(totp).now()
    rh.login(**kwargs)


def logout():
    """Log out of Robinhood."""
    rh.logout()


def get_bitcoin_price() -> float:
    """Get the current Bitcoin price from Robinhood."""
    quote = rh.crypto.get_crypto_quote("BTC")
    return float(quote["mark_price"])


def get_bitcoin_historicals(interval: str = "hour", span: str = "week") -> pd.DataFrame:
    """
    Fetch historical Bitcoin price data from Robinhood.

    Args:
        interval: "15second", "5minute", "10minute", "hour", "day", "week"
        span: "hour", "day", "week", "month", "3month", "year", "5year"

    Returns:
        DataFrame with columns: timestamp, open, close, high, low, volume
    """
    historicals = rh.crypto.get_crypto_historicals(
        "BTC", interval=interval, span=span, info=None
    )

    if not historicals:
        raise ValueError("No historical data returned from Robinhood")

    df = pd.DataFrame(historicals)
    df["timestamp"] = pd.to_datetime(df["begins_at"])
    for col in ["open_price", "close_price", "high_price", "low_price"]:
        df[col] = df[col].astype(float)
    df["volume"] = df["volume"].astype(float)

    df = df.rename(columns={
        "open_price": "open",
        "close_price": "close",
        "high_price": "high",
        "low_price": "low",
    })

    return df[["timestamp", "open", "close", "high", "low", "volume"]].sort_values(
        "timestamp"
    ).reset_index(drop=True)
