#!/usr/bin/env python3
"""
Penny-Stock Day-Trading Signal Bot — FLAG ONLY.

This bot reads public market data and prints trade ideas. It will NEVER
place, modify, or cancel any order. You make every trade manually.

Two things it flags each run:
  1. BUY candidates: top US stocks priced $0.50-$2.00 that are up strongly
     today, ranked by a technical score (RSI, MACD, Bollinger, EMA 9/21,
     VWAP, volume surge).
  2. SELL / HOLD on tickers you already own: tickers listed in watchlist.txt
     (one per line). Bot suggests SELL when the tech turns bearish, HOLD
     otherwise, and always force-flags SELL in the last 5 minutes of the
     trading day (15:55-16:00 ET) since this is a day-trading bot.

Usage:
    python bot.py                    # One scan, then exit
    python bot.py --loop             # Scan every 5 minutes
    python bot.py --loop -i 10       # Scan every 10 minutes
    python bot.py --scan-only        # Only show buy candidates
    python bot.py --watch-only       # Only check watchlist.txt
"""

from __future__ import annotations

import argparse
import sys
from datetime import datetime, time as dtime
from pathlib import Path
from zoneinfo import ZoneInfo

import data_fetcher
import signals


BANNER = """
+--------------------------------------------------+
|     Penny-Stock Day-Trading Signal Bot           |
|                                                  |
|  *** FLAG ONLY - NO TRADES ARE EXECUTED ***      |
|  This bot will NEVER buy or sell on your behalf. |
|  All trades must be placed by you manually.      |
+--------------------------------------------------+
"""

DISCLAIMER = (
    "  [!] REMINDER: These are signals only. No order has been placed.\n"
    "  [!] Penny stocks are volatile. Do your own research."
)

WATCHLIST_PATH = Path(__file__).parent / "watchlist.txt"
ET = ZoneInfo("America/New_York")


def signal_icon(sig: signals.Signal) -> str:
    return {
        signals.Signal.STRONG_BUY:  "[++]",
        signals.Signal.BUY:         "[+ ]",
        signals.Signal.HOLD:        "[ =]",
        signals.Signal.SELL:        "[- ]",
        signals.Signal.STRONG_SELL: "[--]",
    }[sig]


def in_closing_window(now_et: datetime) -> bool:
    """True between 15:55 and 16:00 ET — force-sell window for day trades."""
    t = now_et.time()
    return dtime(15, 55) <= t <= dtime(16, 0)


def read_watchlist() -> list[str]:
    if not WATCHLIST_PATH.exists():
        return []
    tickers = []
    for line in WATCHLIST_PATH.read_text().splitlines():
        s = line.strip().upper()
        if not s or s.startswith("#"):
            continue
        tickers.append(s)
    return tickers


def print_candidate(res: signals.AnalysisResult, pct_change: float | None = None):
    pct_str = f"  +{pct_change:.2f}% today" if pct_change is not None else ""
    print(f"  {signal_icon(res.signal)} {res.ticker:<6} ${res.price:>6.2f}{pct_str}"
          f"   score {res.score:+.0f}   {res.signal.value}")
    print(f"        RSI {res.rsi:>5.1f} | MACD {res.macd_signal} | "
          f"BB {res.bb_signal} | EMA {res.ema_signal} | "
          f"VWAP {res.vwap_signal} | Vol {res.volume_signal}")
    for r in res.reasons[:3]:
        print(f"        - {r}")


def scan_buy_candidates(limit: int = 5):
    """Flag penny stocks that look like fresh BUY candidates."""
    print("\n  == BUY CANDIDATES (penny stocks, $0.50-$2.00, strong momentum) ==")
    try:
        df = data_fetcher.screen_penny_gainers(limit=25)
    except Exception as e:
        print(f"  Screener error: {e}")
        return

    if df.empty:
        print("  No candidates matched the screen right now.")
        return

    scored: list[tuple[signals.AnalysisResult, float]] = []
    for _, row in df.iterrows():
        ticker = row["ticker"]
        try:
            bars = data_fetcher.get_intraday(ticker, period="5d", interval="5m")
            if len(bars) < 30:
                continue
            res = signals.analyze(bars, ticker=ticker)
            scored.append((res, float(row["pct_change"])))
        except Exception:
            continue

    scored.sort(key=lambda x: x[0].score, reverse=True)
    top = [s for s in scored if s[0].signal in (signals.Signal.BUY, signals.Signal.STRONG_BUY)]

    if not top:
        print("  No candidate has a BUY-grade technical score right now.")
        print("  (Matches are up on momentum, but indicators aren't aligned.)")
        for res, pct in scored[:3]:
            print_candidate(res, pct)
        return

    for res, pct in top[:limit]:
        print_candidate(res, pct)


def check_watchlist(force_sell: bool):
    """Flag SELL / HOLD on tickers the user already owns."""
    tickers = read_watchlist()
    print("\n  == YOUR WATCHLIST (SELL / HOLD signals) ==")
    if not tickers:
        print(f"  watchlist.txt is empty. Add tickers you own (one per line)")
        print(f"  at: {WATCHLIST_PATH}")
        return

    if force_sell:
        print("  [!] Within 5 minutes of market close - flagging all as SELL")
        print("      (day-trading rule: do not hold overnight)")

    for ticker in tickers:
        try:
            bars = data_fetcher.get_intraday(ticker, period="5d", interval="5m")
            if len(bars) < 30:
                print(f"  {ticker}: not enough intraday data")
                continue
            res = signals.analyze(bars, ticker=ticker)
            if force_sell:
                res.signal = signals.Signal.SELL
                res.reasons.insert(0, "End-of-day force-sell (no overnight hold)")
            print_candidate(res)
        except Exception as e:
            print(f"  {ticker}: error ({e})")


def run_once(scan: bool, watch: bool):
    now_et = datetime.now(ET)
    print(f"\n  Time (ET):  {now_et.strftime('%Y-%m-%d %H:%M:%S')}")

    if scan:
        scan_buy_candidates()
    if watch:
        check_watchlist(force_sell=in_closing_window(now_et))

    print()
    print(DISCLAIMER)
    print("  " + "=" * 50)


def run_loop(interval_minutes: int, scan: bool, watch: bool):
    import time
    print(f"  Running every {interval_minutes} minutes. Ctrl+C to stop.")
    try:
        while True:
            try:
                run_once(scan, watch)
            except Exception as e:
                print(f"  Scan error: {e}")
            time.sleep(interval_minutes * 60)
    except KeyboardInterrupt:
        print("\n  Stopped.")


def main():
    parser = argparse.ArgumentParser(
        description="Penny-stock day-trading signal bot (FLAG ONLY - never places trades)"
    )
    parser.add_argument("--loop", action="store_true", help="Run continuously")
    parser.add_argument("-i", "--interval", type=int, default=5,
                        help="Loop interval in minutes (default: 5)")
    parser.add_argument("--scan-only", action="store_true",
                        help="Only show buy candidates, skip watchlist")
    parser.add_argument("--watch-only", action="store_true",
                        help="Only check watchlist.txt, skip the screener")
    args = parser.parse_args()

    if args.scan_only and args.watch_only:
        print("  Cannot combine --scan-only and --watch-only.")
        sys.exit(1)

    scan = not args.watch_only
    watch = not args.scan_only

    print(BANNER)

    if args.loop:
        run_loop(args.interval, scan, watch)
    else:
        run_once(scan, watch)


if __name__ == "__main__":
    main()
