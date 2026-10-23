#!/usr/bin/env python3
"""
Autonomous stock trader.

Finds stocks up a high % today, buys a fixed dollar amount of the ones whose
intraday momentum still looks healthy, and sells automatically at a profit
target (or a stop loss) using bracket orders held at the broker.

Uses an Alpaca PAPER account (fake money) unless you explicitly opt in to live.

Usage:
    python autotrade.py              # run on its own, all day, every day
    python autotrade.py --dry-run    # scan and log decisions, place no orders
    python autotrade.py --once       # run a single cycle and exit
    python autotrade.py --live       # real money (also needs ALPACA_PAPER=false)
"""

import argparse
import logging
import os
import sys

from dotenv import load_dotenv

from autotrader.broker import AlpacaBroker
from autotrader.config import Config
from autotrader.trader import AutoTrader


def setup_logging(log_dir: str) -> None:
    os.makedirs(log_dir, exist_ok=True)
    fmt = logging.Formatter("%(asctime)s %(levelname)-7s %(message)s", "%Y-%m-%d %H:%M:%S")
    root = logging.getLogger()
    root.setLevel(logging.INFO)
    for handler in (logging.StreamHandler(sys.stdout),
                    logging.FileHandler(os.path.join(log_dir, "autotrader.log"))):
        handler.setFormatter(fmt)
        root.addHandler(handler)
    logging.getLogger("urllib3").setLevel(logging.WARNING)


def main() -> None:
    load_dotenv()
    parser = argparse.ArgumentParser(description="Autonomous momentum stock trader (Alpaca)")
    parser.add_argument("--dry-run", action="store_true", help="Log decisions but place no orders")
    parser.add_argument("--once", action="store_true", help="Run one cycle and exit")
    parser.add_argument("--live", action="store_true",
                        help="Allow trading real money (requires ALPACA_PAPER=false too)")
    parser.add_argument("--log-dir", default="logs", help="Where to write logs and the trade journal")
    args = parser.parse_args()

    setup_logging(args.log_dir)
    log = logging.getLogger("autotrader")

    cfg = Config.from_env()
    problems = cfg.validate()
    if problems:
        for p in problems:
            log.error("Config: %s", p)
        sys.exit(1)

    if not cfg.paper and not args.live:
        log.error("ALPACA_PAPER=false but --live was not passed. Refusing to trade real money.")
        sys.exit(1)
    if cfg.paper and args.live:
        log.error("--live passed but ALPACA_PAPER is not false. Set both to trade real money.")
        sys.exit(1)

    if not cfg.paper:
        log.warning("*" * 64)
        log.warning("LIVE TRADING: real money will be spent without asking you first.")
        log.warning("Up to $%.0f per trade, %d positions at once.", cfg.trade_amount, cfg.max_positions)
        log.warning("*" * 64)

    log.info("Settings: $%.0f per trade, max %d positions, take profit +%.1f%%, stop loss -%.1f%%, "
             "gainers %.0f-%.0f%%, price $%.0f-$%.0f, daily loss limit %.1f%%",
             cfg.trade_amount, cfg.max_positions, cfg.take_profit_pct, cfg.stop_loss_pct,
             cfg.min_gain_pct, cfg.max_gain_pct, cfg.min_price, cfg.max_price, cfg.max_daily_loss_pct)

    broker = AlpacaBroker(cfg.api_key, cfg.api_secret, paper=cfg.paper)
    trader = AutoTrader(cfg, broker, dry_run=args.dry_run, data_dir=args.log_dir)

    try:
        if args.once:
            trader.tick()
        else:
            trader.run_forever()
    except KeyboardInterrupt:
        log.info("Stopped. Open positions keep their take-profit/stop-loss orders at the broker.")


if __name__ == "__main__":
    main()
