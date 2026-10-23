from datetime import datetime, timedelta, timezone

import numpy as np
import pandas as pd
import pytest

from autotrader import strategy
from autotrader.config import Config
from autotrader.trader import AutoTrader


def make_config(**overrides) -> Config:
    base = dict(
        api_key="k", api_secret="s", paper=True, trade_amount=1000.0, max_positions=2,
        take_profit_pct=4.0, stop_loss_pct=2.0, min_gain_pct=5.0, max_gain_pct=30.0,
        min_price=5.0, max_price=500.0, min_volume=500_000, min_score=60.0,
        max_daily_loss_pct=3.0, flatten_minutes_before_close=10, skip_first_minutes=15,
        skip_last_minutes=30, scan_interval_seconds=60,
    )
    base.update(overrides)
    return Config(**base)


def make_bars(closes, end=datetime(2026, 10, 22, 18, 0, tzinfo=timezone.utc)) -> pd.DataFrame:
    n = len(closes)
    closes = np.asarray(closes, dtype=float)
    return pd.DataFrame({
        "timestamp": [end - timedelta(minutes=5 * (n - 1 - i)) for i in range(n)],
        "open": closes, "high": closes * 1.002, "low": closes * 0.998,
        "close": closes, "volume": np.full(n, 10_000.0),
    })


def uptrend(n=60, start=10.0, step=0.02, seed=0):
    # Steady climb with pullbacks, so RSI lands in the trend zone rather than 100.
    rng = np.random.default_rng(seed)
    moves = step + rng.normal(0, abs(step) * 2.5, n)
    return start + np.cumsum(moves)


class FakeBroker:
    def __init__(self, now=datetime(2026, 10, 22, 17, 0, tzinfo=timezone.utc), is_open=True,
                 gainers=None, bars=None, positions=None, account=None):
        self.now = now
        self.is_open = is_open
        self.gainers = gainers or []
        self.bars = bars or {}
        self.positions = positions or []
        self.account = account or {"equity": "10000", "last_equity": "10000",
                                   "buying_power": "10000", "daytrade_count": 0}
        self.orders = []
        self.closed_all = False

    def get_clock(self):
        return {"timestamp": self.now.isoformat(), "is_open": self.is_open,
                "next_open": (self.now + timedelta(hours=16)).isoformat(),
                "next_close": datetime(2026, 10, 22, 20, 0, tzinfo=timezone.utc).isoformat()}

    def get_calendar_day(self, date):
        return {"date": date, "open": "09:30", "close": "16:00"}

    def get_account(self):
        return self.account

    def get_positions(self):
        return self.positions

    def get_open_orders(self):
        return []

    def get_asset(self, symbol):
        return {"tradable": True, "exchange": "NASDAQ"}

    def get_daily_volume(self, symbol):
        return 2_000_000

    def get_top_gainers(self, top=50):
        return self.gainers

    def get_bars(self, symbol, **kwargs):
        return self.bars[symbol]

    def buy_bracket(self, symbol, qty, take_profit, stop_loss):
        self.orders.append((symbol, qty, take_profit, stop_loss))
        return {"id": f"order-{len(self.orders)}"}

    def close_all_positions(self):
        self.closed_all = True


# ── strategy ──────────────────────────────────────────────────────

def test_uptrend_scores_high():
    score, reasons = strategy.score_momentum(make_bars(uptrend()))
    assert score >= 60, reasons


def test_downtrend_scores_low():
    score, _ = strategy.score_momentum(make_bars(uptrend(step=-0.02, start=15)))
    assert score < 60


def test_parabolic_move_is_vetoed():
    score, reasons = strategy.score_momentum(make_bars(10 * 1.01 ** np.arange(60)))
    assert score == 0 and "veto" in reasons[0]


def test_too_few_bars():
    score, _ = strategy.score_momentum(make_bars(uptrend(n=10)))
    assert score == 0


def test_exit_prices_and_size():
    tp, sl = strategy.exit_prices(50.0, 4.0, 2.0)
    assert (tp, sl) == (52.0, 49.0)
    tp, sl = strategy.exit_prices(5.0, 0.01, 0.01)
    assert tp >= 5.01 and sl <= 4.99
    assert strategy.position_size(1000, 33.0) == 30
    assert strategy.position_size(1000, 2000) == 0


# ── trader ────────────────────────────────────────────────────────

def gainer(symbol, pct, price):
    return {"symbol": symbol, "percent_change": pct, "price": price}


def test_buys_best_candidates_with_bracket(tmp_path):
    good = uptrend()
    broker = FakeBroker(
        gainers=[gainer("GOOD", 12, good[-1]), gainer("BAD", 10, 15),
                 gainer("TOOHOT", 80, 20), gainer("PENNY", 40, 1.2)],
        bars={"GOOD": make_bars(good), "BAD": make_bars(uptrend(step=-0.02, start=15))},
    )
    trader = AutoTrader(make_config(), broker, data_dir=str(tmp_path))
    trader.tick()

    assert len(broker.orders) == 1
    symbol, qty, tp, sl = broker.orders[0]
    assert symbol == "GOOD"
    assert qty == int(1000 // round(good[-1], 10))
    assert tp == pytest.approx(good[-1] * 1.04, abs=0.01)
    assert sl == pytest.approx(good[-1] * 0.98, abs=0.01)
    assert (tmp_path / "trades.csv").exists()

    # Same symbol is not bought twice in one day.
    trader.tick()
    assert len(broker.orders) == 1


def test_dry_run_places_no_orders(tmp_path):
    good = uptrend()
    broker = FakeBroker(gainers=[gainer("GOOD", 12, good[-1])], bars={"GOOD": make_bars(good)})
    AutoTrader(make_config(), broker, dry_run=True, data_dir=str(tmp_path)).tick()
    assert broker.orders == []


def test_respects_max_positions(tmp_path):
    good = uptrend()
    held = [{"symbol": s, "qty": "1", "avg_entry_price": "1", "current_price": "1",
             "unrealized_pl": "0", "unrealized_plpc": "0"} for s in ("A", "B")]
    broker = FakeBroker(gainers=[gainer("GOOD", 12, good[-1])],
                        bars={"GOOD": make_bars(good)}, positions=held)
    AutoTrader(make_config(max_positions=2), broker, data_dir=str(tmp_path)).tick()
    assert broker.orders == []


def test_daily_loss_limit_stops_buying(tmp_path):
    good = uptrend()
    broker = FakeBroker(gainers=[gainer("GOOD", 12, good[-1])], bars={"GOOD": make_bars(good)},
                        account={"equity": "9600", "last_equity": "10000",
                                 "buying_power": "9600", "daytrade_count": 0})
    AutoTrader(make_config(), broker, data_dir=str(tmp_path)).tick()
    assert broker.orders == []


def test_pdt_guard(tmp_path):
    good = uptrend()
    broker = FakeBroker(gainers=[gainer("GOOD", 12, good[-1])], bars={"GOOD": make_bars(good)},
                        account={"equity": "10000", "last_equity": "10000",
                                 "buying_power": "10000", "daytrade_count": 3})
    AutoTrader(make_config(), broker, data_dir=str(tmp_path)).tick()
    assert broker.orders == []


def test_no_buys_right_after_open(tmp_path):
    good = uptrend()
    broker = FakeBroker(now=datetime(2026, 10, 22, 13, 35, tzinfo=timezone.utc),
                        gainers=[gainer("GOOD", 12, good[-1])], bars={"GOOD": make_bars(good)})
    AutoTrader(make_config(), broker, data_dir=str(tmp_path)).tick()
    assert broker.orders == []


def test_flattens_before_close(tmp_path):
    held = [{"symbol": "A", "qty": "1", "avg_entry_price": "1", "current_price": "1",
             "unrealized_pl": "0", "unrealized_plpc": "0"}]
    broker = FakeBroker(now=datetime(2026, 10, 22, 19, 55, tzinfo=timezone.utc), positions=held)
    AutoTrader(make_config(), broker, data_dir=str(tmp_path)).tick()
    assert broker.closed_all


def test_market_closed_does_nothing(tmp_path):
    broker = FakeBroker(is_open=False, gainers=[gainer("X", 10, 10)])
    wait = AutoTrader(make_config(), broker, data_dir=str(tmp_path)).tick()
    assert broker.orders == [] and 30 <= wait <= 3600


def test_config_validation():
    assert make_config().validate() == []
    assert make_config(api_key="").validate()
    assert make_config(min_gain_pct=40).validate()
