"""
The autonomous trading loop.

Every tick:
  1. Skip if the market is closed.
  2. Near the close, flatten all positions (optional) and stop buying.
  3. Refuse new trades if the daily loss limit or day-trade limit is hit.
  4. Scan top % gainers, score their intraday momentum, and buy the best
     ones with a take-profit / stop-loss bracket.
"""

import csv
import json
import logging
import os
import time
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from .broker import AlpacaBroker, BrokerError
from .config import Config
from . import strategy


log = logging.getLogger("autotrader")
NY = ZoneInfo("America/New_York")
PDT_EQUITY = 25_000.0
PDT_MAX_DAY_TRADES = 3


def _parse_time(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


class AutoTrader:
    def __init__(self, cfg: Config, broker: AlpacaBroker, dry_run: bool = False,
                 data_dir: str = "logs"):
        self.cfg = cfg
        self.broker = broker
        self.dry_run = dry_run
        self.data_dir = data_dir
        os.makedirs(data_dir, exist_ok=True)
        self.state_path = os.path.join(data_dir, "state.json")
        self.journal_path = os.path.join(data_dir, "trades.csv")
        self.state = self._load_state()
        self.halted_reason: str | None = None

    # ── Persistent state (symbols already traded today) ───────────

    def _load_state(self) -> dict:
        try:
            with open(self.state_path) as f:
                return json.load(f)
        except (FileNotFoundError, json.JSONDecodeError):
            return {"date": "", "traded": []}

    def _save_state(self) -> None:
        with open(self.state_path, "w") as f:
            json.dump(self.state, f)

    def _roll_day(self, today: str) -> None:
        if self.state.get("date") != today:
            self.state = {"date": today, "traded": []}
            self._save_state()
            self.halted_reason = None

    def _journal(self, row: dict) -> None:
        new_file = not os.path.exists(self.journal_path)
        with open(self.journal_path, "a", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=list(row))
            if new_file:
                writer.writeheader()
            writer.writerow(row)

    # ── Main loop ────────────────────────────────────────────────

    def run_forever(self) -> None:
        log.info("Auto-trader started (%s%s). Ctrl+C to stop.",
                 "PAPER" if self.cfg.paper else "LIVE MONEY",
                 ", DRY RUN" if self.dry_run else "")
        while True:
            try:
                wait = self.tick()
            except BrokerError as e:
                log.error("Broker error: %s", e)
                wait = self.cfg.scan_interval_seconds
            except Exception:
                log.exception("Unexpected error")
                wait = self.cfg.scan_interval_seconds
            time.sleep(wait)

    def tick(self) -> int:
        """Run one cycle. Returns seconds to wait before the next one."""
        clock = self.broker.get_clock()
        now = _parse_time(clock["timestamp"])
        if not clock["is_open"]:
            next_open = _parse_time(clock["next_open"])
            log.info("Market closed. Next open %s.", next_open.astimezone(NY).strftime("%a %H:%M ET"))
            # Wake up shortly after the open, but re-check at least hourly.
            return int(min(max((next_open - now).total_seconds() + 5, 30), 3600))

        today = now.astimezone(NY).strftime("%Y-%m-%d")
        self._roll_day(today)

        session = self.broker.get_calendar_day(today)
        open_time = datetime.fromisoformat(f"{session['date']}T{session['open']}").replace(tzinfo=NY)
        close_time = _parse_time(clock["next_close"])
        mins_since_open = (now - open_time).total_seconds() / 60
        mins_to_close = (close_time - now).total_seconds() / 60

        positions = self.broker.get_positions()
        self._log_positions(positions)

        flatten = self.cfg.flatten_minutes_before_close
        if flatten > 0 and mins_to_close <= flatten:
            if positions:
                log.info("%.0f min to close: closing all %d positions.", mins_to_close, len(positions))
                if not self.dry_run:
                    self.broker.close_all_positions()
            return self.cfg.scan_interval_seconds

        if mins_since_open < self.cfg.skip_first_minutes:
            log.info("Waiting out the opening volatility (%.0f min in).", mins_since_open)
            return self.cfg.scan_interval_seconds
        if mins_to_close < self.cfg.skip_last_minutes:
            log.info("Too close to the close for new trades.")
            return self.cfg.scan_interval_seconds

        account = self.broker.get_account()
        reason = self.check_risk(account)
        if reason:
            if reason != self.halted_reason:
                log.warning("No new trades today: %s", reason)
                self.halted_reason = reason
            return self.cfg.scan_interval_seconds

        open_orders = self.broker.get_open_orders()
        pending_buys = {o["symbol"] for o in open_orders if o["side"] == "buy"}
        held = {p["symbol"] for p in positions}
        slots = self.cfg.max_positions - len(held | pending_buys)
        if slots <= 0:
            return self.cfg.scan_interval_seconds

        exclude = held | pending_buys | set(self.state["traded"])
        candidates = self.find_candidates(exclude)
        buying_power = float(account["buying_power"])
        for cand in candidates[:slots]:
            buying_power -= self.enter(cand, buying_power)
        return self.cfg.scan_interval_seconds

    # ── Risk ─────────────────────────────────────────────────────

    def check_risk(self, account: dict) -> str | None:
        """Return why trading must pause, or None if it's OK to buy."""
        if account.get("trading_blocked") or account.get("account_blocked"):
            return "account is blocked from trading"
        equity = float(account["equity"])
        last_equity = float(account["last_equity"])
        if last_equity > 0:
            change_pct = (equity - last_equity) / last_equity * 100
            if change_pct <= -self.cfg.max_daily_loss_pct:
                return f"daily loss limit hit ({change_pct:.2f}% today)"
        if equity < PDT_EQUITY and int(account.get("daytrade_count", 0)) >= PDT_MAX_DAY_TRADES:
            return ("pattern day trader limit: accounts under $25k get 3 day trades "
                    "per 5 business days")
        return None

    # ── Scanning ─────────────────────────────────────────────────

    def find_candidates(self, exclude: set[str]) -> list[strategy.Candidate]:
        """Top gainers that pass the filters, best momentum score first."""
        cfg = self.cfg
        gainers = self.broker.get_top_gainers(top=50)
        picks = []
        for g in gainers:
            symbol, price, pct = g["symbol"], float(g["price"]), float(g["percent_change"])
            if symbol in exclude:
                continue
            if not (cfg.min_gain_pct <= pct <= cfg.max_gain_pct):
                continue
            if not (cfg.min_price <= price <= cfg.max_price):
                continue
            if strategy.position_size(cfg.trade_amount, price) < 1:
                continue
            try:
                asset = self.broker.get_asset(symbol)
                if not asset.get("tradable") or asset.get("exchange") == "OTC":
                    continue
                if self.broker.get_daily_volume(symbol) < cfg.min_volume:
                    continue
                bars = self.broker.get_bars(symbol)
            except BrokerError as e:
                log.debug("Skipping %s: %s", symbol, e)
                continue
            score, reasons = strategy.score_momentum(bars)
            log.info("  %-6s +%5.1f%%  $%8.2f  score %3.0f  | %s",
                     symbol, pct, price, score, "; ".join(reasons))
            if score >= cfg.min_score:
                last = float(bars["close"].iloc[-1])
                picks.append(strategy.Candidate(symbol, last, pct, score, reasons))
        picks.sort(key=lambda c: c.score, reverse=True)
        return picks

    # ── Orders ───────────────────────────────────────────────────

    def enter(self, cand: strategy.Candidate, buying_power: float) -> float:
        """Buy a candidate with a bracket exit. Returns dollars committed."""
        qty = strategy.position_size(self.cfg.trade_amount, cand.price)
        cost = qty * cand.price
        if qty < 1 or cost > buying_power:
            log.info("Skipping %s: not enough buying power.", cand.symbol)
            return 0.0
        tp, sl = strategy.exit_prices(cand.price, self.cfg.take_profit_pct, self.cfg.stop_loss_pct)
        log.info("BUY %d %s @ ~$%.2f (score %.0f) | take profit $%.2f, stop loss $%.2f",
                 qty, cand.symbol, cand.price, cand.score, tp, sl)

        order_id = "dry-run"
        if not self.dry_run:
            order = self.broker.buy_bracket(cand.symbol, qty, tp, sl)
            order_id = order.get("id", "")

        self.state["traded"].append(cand.symbol)
        self._save_state()
        self._journal({
            "time": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "symbol": cand.symbol, "qty": qty, "entry_estimate": f"{cand.price:.2f}",
            "take_profit": f"{tp:.2f}", "stop_loss": f"{sl:.2f}",
            "gain_today_pct": f"{cand.gain_pct:.1f}", "score": f"{cand.score:.0f}",
            "dry_run": self.dry_run, "order_id": order_id,
        })
        return cost

    def _log_positions(self, positions: list[dict]) -> None:
        for p in positions:
            log.info("  holding %s x%s  entry $%.2f  now $%.2f  P/L $%.2f (%.2f%%)",
                     p["symbol"], p["qty"], float(p["avg_entry_price"]),
                     float(p["current_price"]), float(p["unrealized_pl"]),
                     float(p["unrealized_plpc"]) * 100)
