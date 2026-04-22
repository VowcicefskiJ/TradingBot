#!/usr/bin/env python3
"""
Penny-Stock Day-Trading Signal Bot — FLAG ONLY.

Reads public market data and prints trade ideas. Will NEVER place, modify,
or cancel any order. You make every trade manually.

Each run produces:
  1. BUY CANDIDATES: up to 5 US stocks priced $0.50-$2.00, up strongly today,
     ranked by technical score (RSI, MACD, Bollinger, EMA 9/21, VWAP,
     volume surge).
  2. TOP PICK: a single actionable suggestion from the 5 with share count,
     take-profit target, stop-loss, and force-sell time. Recalculated every
     run. If nothing is clean, says "sit out."
  3. YOUR WATCHLIST: SELL / HOLD signals on tickers from watchlist.txt.
     Supports optional entry price for P&L-based exits.

Usage:
    python bot.py                    # One scan, then exit
    python bot.py --loop             # Scan every 5 min
    python bot.py --loop -i 10       # Scan every 10 min
    python bot.py --size 200         # Use $200 position size (default $150)
    python bot.py --target 7         # +7% take-profit (default 5)
    python bot.py --stop 4           # -4% stop-loss (default 3)
    python bot.py --scan-only        # Only show buy candidates + top pick
    python bot.py --watch-only       # Only check watchlist.txt
"""

from __future__ import annotations

import argparse
import sys
from dataclasses import dataclass
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

DEFAULT_POSITION_USD = 150.0
DEFAULT_TAKE_PROFIT_PCT = 5.0
DEFAULT_STOP_LOSS_PCT = 3.0

MAX_PCT_CHANGE_FOR_PICK = 15.0
MAX_RSI_FOR_PICK = 65.0
MIN_SCORE_FOR_PICK = 25.0


@dataclass
class WatchlistEntry:
    ticker: str
    entry_price: float | None


@dataclass
class TradeConfig:
    position_usd: float
    take_profit_pct: float
    stop_loss_pct: float


def signal_icon(sig: signals.Signal) -> str:
    return {
        signals.Signal.STRONG_BUY:  "[++]",
        signals.Signal.BUY:         "[+ ]",
        signals.Signal.HOLD:        "[ =]",
        signals.Signal.SELL:        "[- ]",
        signals.Signal.STRONG_SELL: "[--]",
    }[sig]


def in_closing_window(now_et: datetime) -> bool:
    """True between 15:55 and 16:00 ET."""
    t = now_et.time()
    return dtime(15, 55) <= t <= dtime(16, 0)


def read_watchlist() -> list[WatchlistEntry]:
    """Parse watchlist.txt. Lines can be 'TICKER' or 'TICKER ENTRY_PRICE'."""
    if not WATCHLIST_PATH.exists():
        return []
    entries: list[WatchlistEntry] = []
    for line in WATCHLIST_PATH.read_text().splitlines():
        s = line.strip()
        if not s or s.startswith("#"):
            continue
        parts = s.replace(",", " ").split()
        ticker = parts[0].upper()
        entry_price = None
        if len(parts) >= 2:
            try:
                entry_price = float(parts[1])
            except ValueError:
                pass
        entries.append(WatchlistEntry(ticker=ticker, entry_price=entry_price))
    return entries


def print_candidate(res: signals.AnalysisResult, pct_change: float | None = None):
    pct_str = f"  +{pct_change:.2f}% today" if pct_change is not None else ""
    print(f"  {signal_icon(res.signal)} {res.ticker:<6} ${res.price:>6.2f}{pct_str}"
          f"   score {res.score:+.0f}   {res.signal.value}")
    print(f"        RSI {res.rsi:>5.1f} | MACD {res.macd_signal} | "
          f"BB {res.bb_signal} | EMA {res.ema_signal} | "
          f"VWAP {res.vwap_signal} | Vol {res.volume_signal}")
    for r in res.reasons[:3]:
        print(f"        - {r}")


def passes_quality_gate(res: signals.AnalysisResult, pct_change: float) -> tuple[bool, str]:
    """Decide if a candidate is clean enough to be the top pick."""
    if res.score < MIN_SCORE_FOR_PICK:
        return False, f"score {res.score:+.0f} below +{MIN_SCORE_FOR_PICK:.0f}"
    if pct_change > MAX_PCT_CHANGE_FOR_PICK:
        return False, f"already up {pct_change:.1f}% today (chasing)"
    if res.rsi > MAX_RSI_FOR_PICK:
        return False, f"RSI {res.rsi:.1f} near overbought"
    if res.bb_signal == "OVERBOUGHT":
        return False, "price at upper Bollinger Band (overbought)"
    if res.volume_signal == "WEAK":
        return False, "volume too weak to trust the move"
    if res.vwap_signal == "BELOW":
        return False, "price below VWAP (intraday downtrend)"
    return True, ""


def confidence_tier(score: float) -> str:
    if score >= 45:
        return "HIGH"
    if score >= 30:
        return "MEDIUM"
    return "LOW"


def print_top_pick(
    scored: list[tuple[signals.AnalysisResult, float]],
    cfg: TradeConfig,
):
    """Pick the single best candidate from the list and print a trade plan."""
    print("\n  == TOP PICK ==")

    if not scored:
        print("  Sit out. No candidates matched the screen.")
        return

    # scored is already sorted high-to-low by score from the caller
    chosen: tuple[signals.AnalysisResult, float] | None = None
    rejections: list[tuple[str, str]] = []
    for res, pct in scored:
        ok, why = passes_quality_gate(res, pct)
        if ok:
            chosen = (res, pct)
            break
        rejections.append((res.ticker, why))

    if chosen is None:
        top_rej = rejections[:3]
        rej_str = "; ".join(f"{t} ({w})" for t, w in top_rej) if top_rej else "no data"
        print(f"  Sit out. No candidate passed the quality filter.")
        print(f"  Top rejections: {rej_str}")
        return

    res, pct = chosen
    shares = int(cfg.position_usd // res.price)
    actual_cost = shares * res.price
    target = res.price * (1 + cfg.take_profit_pct / 100)
    stop = res.price * (1 - cfg.stop_loss_pct / 100)

    print(f"  BUY:         {res.ticker} @ ${res.price:.2f}  (up {pct:.1f}% today)")
    print(f"  Shares:      {shares}  (~${actual_cost:.2f} position)")
    print(f"  Sell target: ${target:.2f}  (+{cfg.take_profit_pct:.1f}%)  -> sell when hit")
    print(f"  Stop loss:   ${stop:.2f}  (-{cfg.stop_loss_pct:.1f}%)  -> sell if hit")
    print(f"  Force sell:  15:55 ET  (do not hold overnight)")
    print(f"  Confidence:  {confidence_tier(res.score)}  (score {res.score:+.0f})")
    top_reasons = "; ".join(res.reasons[:3])
    print(f"  Why:         {top_reasons}")
    print(f"  [!] Also SELL early if indicators flip bearish on the next scan.")


def scan_buy_candidates(cfg: TradeConfig, limit: int = 5):
    """Flag up to `limit` buy candidates, then print the single top pick."""
    print("\n  == BUY CANDIDATES (penny stocks, $0.50-$2.00, strong momentum) ==")
    try:
        df = data_fetcher.screen_penny_gainers(limit=25)
    except Exception as e:
        print(f"  Screener error: {e}")
        return

    if df.empty:
        print("  No candidates matched the screen right now.")
        print_top_pick([], cfg)
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
    buy_grade = [s for s in scored
                 if s[0].signal in (signals.Signal.BUY, signals.Signal.STRONG_BUY)]

    shown = buy_grade[:limit] if buy_grade else scored[:limit]
    if not buy_grade:
        print("  No candidate has a BUY-grade technical score right now.")
        print("  (Matches are up on momentum, but indicators aren't aligned.)")

    for res, pct in shown:
        print_candidate(res, pct)

    print_top_pick(scored, cfg)


def watchlist_decision(
    res: signals.AnalysisResult,
    entry_price: float | None,
    cfg: TradeConfig,
    force_sell: bool,
) -> tuple[signals.Signal, list[str]]:
    """
    Decide SELL vs HOLD for a ticker you own. Returns (signal, extra_reasons).

    Rules:
      1. Force SELL at 15:55 ET (no overnight holds)
      2. If entry price known and up >= take-profit: SELL (target hit)
      3. If entry price known and down >= stop-loss: SELL (stop hit)
      4. If score <= -20: SELL (tech flipped bearish)
      5. Otherwise: HOLD
    """
    extras: list[str] = []

    if force_sell:
        return signals.Signal.SELL, ["End-of-day force-sell (no overnight hold)"]

    if entry_price is not None and entry_price > 0:
        pnl_pct = (res.price - entry_price) / entry_price * 100
        extras.append(f"Entry ${entry_price:.2f}, now ${res.price:.2f} ({pnl_pct:+.2f}%)")
        if pnl_pct >= cfg.take_profit_pct:
            extras.insert(0, f"Target hit (+{pnl_pct:.1f}%) — take profit")
            return signals.Signal.SELL, extras
        if pnl_pct <= -cfg.stop_loss_pct:
            extras.insert(0, f"Stop hit ({pnl_pct:.1f}%) — cut losses")
            return signals.Signal.SELL, extras

    if res.score <= -20:
        extras.insert(0, "Technicals turned bearish — consider selling")
        return signals.Signal.SELL, extras

    return signals.Signal.HOLD, extras


def check_watchlist(cfg: TradeConfig, force_sell: bool):
    """Flag SELL / HOLD on tickers the user already owns."""
    entries = read_watchlist()
    print("\n  == YOUR WATCHLIST (SELL / HOLD signals) ==")
    if not entries:
        print(f"  watchlist.txt is empty. Add tickers you own (one per line)")
        print(f"  at: {WATCHLIST_PATH}")
        print(f"  Format: TICKER [ENTRY_PRICE]   e.g.  SNDL 1.50")
        return

    if force_sell:
        print("  [!] Within 5 minutes of market close - flagging all as SELL")
        print("      (day-trading rule: do not hold overnight)")

    for entry in entries:
        try:
            bars = data_fetcher.get_intraday(entry.ticker, period="5d", interval="5m")
            if len(bars) < 30:
                print(f"  {entry.ticker}: not enough intraday data")
                continue
            res = signals.analyze(bars, ticker=entry.ticker)
            decision, extras = watchlist_decision(res, entry.entry_price, cfg, force_sell)
            res.signal = decision
            for extra in reversed(extras):
                res.reasons.insert(0, extra)
            print_candidate(res)
        except Exception as e:
            print(f"  {entry.ticker}: error ({e})")


def run_once(scan: bool, watch: bool, cfg: TradeConfig):
    now_et = datetime.now(ET)
    print(f"\n  Time (ET):  {now_et.strftime('%Y-%m-%d %H:%M:%S')}")

    if scan:
        scan_buy_candidates(cfg)
    if watch:
        check_watchlist(cfg, force_sell=in_closing_window(now_et))

    print()
    print(DISCLAIMER)
    print("  " + "=" * 50)


def run_loop(interval_minutes: int, scan: bool, watch: bool, cfg: TradeConfig):
    import time
    print(f"  Running every {interval_minutes} minutes. Ctrl+C to stop.")
    try:
        while True:
            try:
                run_once(scan, watch, cfg)
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
    parser.add_argument("--size", type=float, default=DEFAULT_POSITION_USD,
                        help=f"Position size in USD (default: {DEFAULT_POSITION_USD:.0f})")
    parser.add_argument("--target", type=float, default=DEFAULT_TAKE_PROFIT_PCT,
                        help=f"Take-profit %% (default: {DEFAULT_TAKE_PROFIT_PCT})")
    parser.add_argument("--stop", type=float, default=DEFAULT_STOP_LOSS_PCT,
                        help=f"Stop-loss %% (default: {DEFAULT_STOP_LOSS_PCT})")
    parser.add_argument("--scan-only", action="store_true",
                        help="Only show buy candidates + top pick, skip watchlist")
    parser.add_argument("--watch-only", action="store_true",
                        help="Only check watchlist.txt, skip the screener")
    args = parser.parse_args()

    if args.scan_only and args.watch_only:
        print("  Cannot combine --scan-only and --watch-only.")
        sys.exit(1)

    cfg = TradeConfig(
        position_usd=args.size,
        take_profit_pct=args.target,
        stop_loss_pct=args.stop,
    )
    scan = not args.watch_only
    watch = not args.scan_only

    print(BANNER)

    if args.loop:
        run_loop(args.interval, scan, watch, cfg)
    else:
        run_once(scan, watch, cfg)


if __name__ == "__main__":
    main()
