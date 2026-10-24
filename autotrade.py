#!/usr/bin/env python3
"""
Autonomous stock trader.

Finds stocks up a high % today, buys a fixed dollar amount of the ones whose
intraday momentum still looks healthy, and sells automatically at a profit
target (or a stop loss).

Brokers (set BROKER in .env):
  alpaca     Paper account (fake money) by default.
  robinhood  Your real Robinhood account: REAL MONEY, always.

Usage:
    python autotrade.py --dry-run    # scan and log decisions, place no orders
    python autotrade.py              # Alpaca paper trading on its own
    python autotrade.py --live       # real money (Robinhood, or Alpaca with ALPACA_PAPER=false)
    python autotrade.py --once       # run a single cycle and exit
    python autotrade.py --check      # log in and show the account, no trading
"""

import argparse
import getpass
import logging
import os
import sys

from dotenv import load_dotenv

from autotrader.broker import AlpacaBroker, BrokerError
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


def robinhood_credentials() -> tuple[str, str, str | None]:
    """From .env if set, otherwise asked for here. The password is never written to disk."""
    username = os.getenv("ROBINHOOD_USERNAME", "").strip()
    password = os.getenv("ROBINHOOD_PASSWORD", "").strip()
    totp = os.getenv("ROBINHOOD_TOTP", "").strip() or None
    if not username or not password or username == "your_email@example.com":
        print("  ── Robinhood Login ──────────────────────────")
        username = input("  Email:    ").strip()
        password = getpass.getpass("  Password: ")
        totp = input("  2FA TOTP secret (press Enter to skip): ").strip() or None
    return username, password, totp


def make_broker(cfg: Config):
    if cfg.broker == "alpaca":
        return AlpacaBroker(cfg.api_key, cfg.api_secret, paper=cfg.paper)
    from autotrader.robinhood_broker import RobinhoodBroker
    broker = RobinhoodBroker(*robinhood_credentials())
    broker.login()
    return broker


def check(broker) -> None:
    """Show what the bot sees in the account, without trading."""
    log = logging.getLogger("autotrader")
    clock = broker.get_clock()
    acct = broker.get_account()
    log.info("Connected. Market is %s.", "OPEN" if clock["is_open"] else "closed")
    log.info("Equity $%.2f (yesterday $%.2f), buying power $%.2f, day trades in last 5 days: %d",
             float(acct["equity"]), float(acct["last_equity"]), float(acct["buying_power"]),
             acct["daytrade_count"])
    for p in broker.get_positions():
        log.info("  you hold %s x%s @ $%.2f (the bot will never sell these)",
                 p["symbol"], p["qty"], float(p["avg_entry_price"]))
    gainers = broker.get_top_gainers(top=10)
    log.info("Top gainers it can see: %s",
             ", ".join(f"{g['symbol']} +{g['percent_change']:.1f}%" for g in gainers) or "none")


def main() -> None:
    load_dotenv()
    parser = argparse.ArgumentParser(description="Autonomous momentum stock trader")
    parser.add_argument("--dry-run", action="store_true", help="Log decisions but place no orders")
    parser.add_argument("--once", action="store_true", help="Run one cycle and exit")
    parser.add_argument("--live", action="store_true", help="Allow trading real money")
    parser.add_argument("--check", action="store_true",
                        help="Log in, show the account and top gainers, then exit (no trading)")
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

    real_money = not cfg.paper and not args.dry_run and not args.check
    if real_money and not args.live:
        if cfg.broker == "robinhood":
            log.error("Robinhood trades REAL money. Run with --dry-run first, then add --live "
                      "when you're ready.")
        else:
            log.error("ALPACA_PAPER=false but --live was not passed. Refusing to trade real money.")
        sys.exit(1)
    if cfg.broker == "alpaca" and cfg.paper and args.live:
        log.error("--live passed but ALPACA_PAPER is not false. Set both to trade real money.")
        sys.exit(1)

    if real_money:
        log.warning("*" * 64)
        log.warning("LIVE TRADING on %s: real money will be spent without asking you first.",
                    cfg.broker.capitalize())
        log.warning("Up to $%.0f per trade, %d positions at once.", cfg.trade_amount, cfg.max_positions)
        log.warning("*" * 64)

    log.info("Broker: %s%s. $%.0f per trade, max %d positions, take profit +%.1f%%, "
             "stop loss -%.1f%%, gainers %.0f-%.0f%%, price $%.0f-$%.0f, daily loss limit %.1f%%",
             cfg.broker, " (paper)" if cfg.paper else "", cfg.trade_amount, cfg.max_positions,
             cfg.take_profit_pct, cfg.stop_loss_pct, cfg.min_gain_pct, cfg.max_gain_pct,
             cfg.min_price, cfg.max_price, cfg.max_daily_loss_pct)

    try:
        broker = make_broker(cfg)
    except BrokerError as e:
        log.error("%s", e)
        sys.exit(1)
    if args.check:
        try:
            check(broker)
        except BrokerError as e:
            log.error("%s", e)
            sys.exit(1)
        return

    trader = AutoTrader(cfg, broker, dry_run=args.dry_run, data_dir=args.log_dir)

    try:
        if args.once:
            trader.tick()
        else:
            trader.run_forever()
    except KeyboardInterrupt:
        if broker.supports_brackets:
            log.info("Stopped. Open positions keep their take-profit/stop-loss orders at the broker.")
        else:
            log.info("Stopped. Open positions keep their stop-loss at Robinhood, but the "
                     "take-profit is only watched while the bot runs.")


if __name__ == "__main__":
    main()
