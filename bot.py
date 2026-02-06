#!/usr/bin/env python3
"""
Bitcoin Trading Bot - Fetches Robinhood data and generates buy/sell signals.

This bot does NOT execute trades automatically. It provides recommendations
based on technical analysis so you can make informed decisions.

Usage:
    python bot.py              # Run once and print signal
    python bot.py --loop       # Run continuously on a schedule
    python bot.py --backtest   # Backtest strategy on historical data
"""

import os
import sys
import argparse
from datetime import datetime

from dotenv import load_dotenv

import data_fetcher
import signals


BANNER = """
╔══════════════════════════════════════════════╗
║         Bitcoin Trading Signal Bot           ║
║       Powered by Robinhood Market Data       ║
╚══════════════════════════════════════════════╝
"""


def format_result(result: signals.AnalysisResult) -> str:
    """Format the analysis result for display."""
    signal_colors = {
        signals.Signal.STRONG_BUY: "🟢🟢",
        signals.Signal.BUY: "🟢",
        signals.Signal.HOLD: "🟡",
        signals.Signal.SELL: "🔴",
        signals.Signal.STRONG_SELL: "🔴🔴",
    }

    icon = signal_colors[result.signal]
    lines = [
        "",
        f"  Time:       {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
        f"  BTC Price:  ${result.price:,.2f}",
        f"  Signal:     {icon} {result.signal.value}",
        f"  Score:      {result.score:+.1f} / 100",
        "",
        "  Indicators:",
        f"    RSI (14):        {result.rsi:.1f}",
        f"    MACD:            {result.macd_signal}",
        f"    Bollinger Bands: {result.bb_signal}",
        f"    EMA 9/21:        {result.ema_signal}",
        "",
        "  Reasoning:",
    ]
    for reason in result.reasons:
        lines.append(f"    - {reason}")

    lines.append("")
    lines.append("  " + "=" * 44)
    return "\n".join(lines)


def run_once():
    """Fetch data, analyze, and print the signal."""
    print("  Fetching Bitcoin data from Robinhood...")

    # Get hourly data for the past month (gives ~720 data points)
    df = data_fetcher.get_bitcoin_historicals(interval="hour", span="month")
    print(f"  Loaded {len(df)} hourly candles.")

    result = signals.analyze(df)
    print(format_result(result))

    return result


def run_loop(interval_minutes: int):
    """Run the bot continuously on a schedule."""
    import schedule
    import time

    print(f"  Running every {interval_minutes} minutes. Press Ctrl+C to stop.\n")

    def job():
        try:
            run_once()
        except Exception as e:
            print(f"  Error: {e}")

    job()  # Run immediately
    schedule.every(interval_minutes).minutes.do(job)

    try:
        while True:
            schedule.run_pending()
            time.sleep(1)
    except KeyboardInterrupt:
        print("\n  Bot stopped.")


def run_backtest():
    """
    Simple backtest: replay historical data and simulate signals.
    Uses daily data over the past year.
    """
    print("  Running backtest on daily data (past year)...\n")

    df = data_fetcher.get_bitcoin_historicals(interval="day", span="year")
    print(f"  Loaded {len(df)} daily candles.\n")

    # We need at least 30 rows for indicators to stabilize
    if len(df) < 30:
        print("  Not enough data for backtest.")
        return

    buy_signals = []
    sell_signals = []

    # Slide a window through the data
    for i in range(30, len(df)):
        window = df.iloc[:i + 1].copy().reset_index(drop=True)
        try:
            result = signals.analyze(window)
        except Exception:
            continue

        row = df.iloc[i]
        if result.signal in (signals.Signal.STRONG_BUY, signals.Signal.BUY):
            buy_signals.append((row["timestamp"], row["close"], result.signal.value, result.score))
        elif result.signal in (signals.Signal.STRONG_SELL, signals.Signal.SELL):
            sell_signals.append((row["timestamp"], row["close"], result.signal.value, result.score))

    print(f"  Buy signals:  {len(buy_signals)}")
    print(f"  Sell signals: {len(sell_signals)}")
    print(f"  Hold periods: {len(df) - 30 - len(buy_signals) - len(sell_signals)}")
    print()

    # Show simulated P&L for paired trades
    if buy_signals and sell_signals:
        print("  Recent signals:")
        print(f"  {'Date':<22} {'Signal':<14} {'Price':<14} {'Score':>6}")
        print("  " + "-" * 58)
        all_signals = [(t, p, s, sc, "BUY") for t, p, s, sc in buy_signals] + \
                      [(t, p, s, sc, "SELL") for t, p, s, sc in sell_signals]
        all_signals.sort(key=lambda x: x[0])
        for ts, price, sig, sc, _ in all_signals[-20:]:
            print(f"  {str(ts):<22} {sig:<14} ${price:>10,.2f} {sc:>+6.0f}")
    print()


def main():
    load_dotenv()

    parser = argparse.ArgumentParser(description="Bitcoin Trading Signal Bot")
    parser.add_argument("--loop", action="store_true", help="Run continuously")
    parser.add_argument("--backtest", action="store_true", help="Run backtest on historical data")
    parser.add_argument("--interval", type=int, default=None,
                        help="Check interval in minutes (default: from .env or 15)")
    args = parser.parse_args()

    print(BANNER)

    # Login to Robinhood
    username = os.getenv("ROBINHOOD_USERNAME")
    password = os.getenv("ROBINHOOD_PASSWORD")
    totp = os.getenv("ROBINHOOD_TOTP") or None

    if not username or not password:
        print("  ERROR: Set ROBINHOOD_USERNAME and ROBINHOOD_PASSWORD in .env file.")
        print("  Copy .env.example to .env and fill in your credentials.")
        sys.exit(1)

    print("  Logging in to Robinhood...")
    try:
        data_fetcher.login(username, password, totp)
    except Exception as e:
        print(f"  Login failed: {e}")
        sys.exit(1)
    print("  Logged in successfully.\n")

    try:
        if args.backtest:
            run_backtest()
        elif args.loop:
            interval = args.interval or int(os.getenv("CHECK_INTERVAL_MINUTES", "15"))
            run_loop(interval)
        else:
            run_once()
    finally:
        data_fetcher.logout()
        print("  Logged out of Robinhood.")


if __name__ == "__main__":
    main()
