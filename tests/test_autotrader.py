from datetime import datetime, timedelta, timezone

import numpy as np
import pandas as pd
import pytest

from autotrader import strategy
from autotrader.config import Config
from autotrader.trader import AutoTrader


def make_config(**overrides) -> Config:
    base = dict(
        broker="alpaca", api_key="k", api_secret="s", paper=True, trade_amount=1000.0, max_positions=2,
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


def position(symbol, qty=1, entry=1.0, price=1.0):
    return {"symbol": symbol, "qty": str(qty), "avg_entry_price": str(entry),
            "current_price": str(price), "unrealized_pl": str((price - entry) * qty),
            "unrealized_plpc": str((price - entry) / entry)}


class FakeBroker:
    supports_brackets = True

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
        self.open_orders = []
        self.cancelled = []
        self.sells = []
        self.closed = []

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
        return self.open_orders

    def get_asset(self, symbol):
        return {"tradable": True, "exchange": "NASDAQ"}

    def get_daily_volume(self, symbol):
        return 2_000_000

    def get_top_gainers(self, top=50):
        return self.gainers

    def get_bars(self, symbol, **kwargs):
        return self.bars[symbol]

    def _new_order(self, symbol, side, kind):
        order = {"id": f"order-{len(self.orders) + 1}", "symbol": symbol, "side": side, "type": kind}
        self.orders.append(order)
        self.open_orders.append(order)
        return order

    def buy_bracket(self, symbol, qty, take_profit, stop_loss):
        order = self._new_order(symbol, "buy", "market")
        order.update(qty=qty, take_profit=take_profit, stop_loss=stop_loss)
        return order

    def buy_market(self, symbol, qty):
        order = self._new_order(symbol, "buy", "market")
        order["qty"] = qty
        return order

    def sell_stop(self, symbol, qty, stop_price):
        order = self._new_order(symbol, "sell", "stop")
        order.update(qty=qty, stop=stop_price)
        return order

    def sell_market(self, symbol, qty):
        self.sells.append((symbol, qty))
        return self._new_order(symbol, "sell", "market")

    def cancel_order(self, order_id):
        self.cancelled.append(order_id)
        self.open_orders = [o for o in self.open_orders if o["id"] != order_id]

    def close_position(self, symbol, qty):
        self.closed.append((symbol, qty))

    def fill(self, symbol, qty, entry, price):
        """Simulate the buy filling."""
        self.open_orders = [o for o in self.open_orders
                            if not (o["symbol"] == symbol and o["side"] == "buy")]
        self.positions = [p for p in self.positions if p["symbol"] != symbol]
        self.positions.append(position(symbol, qty, entry, price))


class FakeRobinhood(FakeBroker):
    supports_brackets = False


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
    order = broker.orders[0]
    assert order["symbol"] == "GOOD"
    assert order["qty"] == int(1000 // good[-1])
    assert order["take_profit"] == pytest.approx(good[-1] * 1.04, abs=0.01)
    assert order["stop_loss"] == pytest.approx(good[-1] * 0.98, abs=0.01)
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
    broker = FakeBroker(gainers=[gainer("GOOD", 12, good[-1]), gainer("NEXT", 10, good[-1])],
                        bars={"GOOD": make_bars(good), "NEXT": make_bars(good)})
    trader = AutoTrader(make_config(max_positions=1), broker, data_dir=str(tmp_path))
    trader.tick()
    trader.tick()
    assert len(broker.orders) == 1


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


def test_flattens_only_the_bots_positions(tmp_path):
    good = uptrend()
    broker = FakeBroker(gainers=[gainer("GOOD", 12, good[-1])], bars={"GOOD": make_bars(good)})
    trader = AutoTrader(make_config(), broker, data_dir=str(tmp_path))
    trader.tick()
    qty = broker.orders[0]["qty"]
    # The bot's buy fills; the user also owns an unrelated stock and extra GOOD shares.
    broker.fill("GOOD", qty + 5, good[-1], good[-1])
    broker.positions.append(position("MINE", 10, 50, 55))

    broker.now = datetime(2026, 10, 22, 19, 55, tzinfo=timezone.utc)
    trader.tick()
    assert broker.closed == [("GOOD", qty)]


def test_does_not_buy_stocks_you_already_own(tmp_path):
    good = uptrend()
    broker = FakeBroker(gainers=[gainer("GOOD", 12, good[-1])], bars={"GOOD": make_bars(good)},
                        positions=[position("GOOD", 3, 9, 10)])
    AutoTrader(make_config(), broker, data_dir=str(tmp_path)).tick()
    assert broker.orders == []


def test_market_closed_does_nothing(tmp_path):
    broker = FakeBroker(is_open=False, gainers=[gainer("X", 10, 10)])
    wait = AutoTrader(make_config(), broker, data_dir=str(tmp_path)).tick()
    assert broker.orders == [] and 30 <= wait <= 3600


def test_config_validation():
    assert make_config().validate() == []
    assert make_config(api_key="").validate()
    assert make_config(min_gain_pct=40).validate()


# ── exits managed by the bot (Robinhood: no bracket orders) ───────

def start_rh_trade(tmp_path):
    good = uptrend()
    broker = FakeRobinhood(gainers=[gainer("GOOD", 12, good[-1])], bars={"GOOD": make_bars(good)})
    trader = AutoTrader(make_config(), broker, data_dir=str(tmp_path))
    trader.tick()
    assert [o["type"] for o in broker.orders] == ["market"]
    return broker, trader, broker.orders[0]["qty"]


def test_rh_places_stop_after_fill_anchored_to_fill_price(tmp_path):
    broker, trader, qty = start_rh_trade(tmp_path)
    broker.fill("GOOD", qty, entry=10.00, price=10.05)
    trader.tick()
    stop = broker.orders[-1]
    assert stop["type"] == "stop" and stop["qty"] == qty and stop["stop"] == 9.80
    assert trader.state["managed"]["GOOD"]["take_profit"] == 10.40

    # Next tick doesn't place a second stop.
    trader.tick()
    assert sum(o["type"] == "stop" for o in broker.orders) == 1


def test_rh_takes_profit(tmp_path):
    broker, trader, qty = start_rh_trade(tmp_path)
    broker.fill("GOOD", qty, entry=10.00, price=10.05)
    trader.tick()
    stop_id = broker.orders[-1]["id"]

    broker.fill("GOOD", qty, entry=10.00, price=10.45)
    trader.tick()
    assert stop_id in broker.cancelled
    assert broker.sells == [("GOOD", qty)]

    # Sell pending: nothing more happens.
    trader.tick()
    assert broker.sells == [("GOOD", qty)]

    # Sell filled: position gone, bot forgets it.
    broker.positions, broker.open_orders = [], []
    trader.tick()
    assert trader.state["managed"] == {}


def test_rh_stop_fills_at_broker(tmp_path):
    broker, trader, qty = start_rh_trade(tmp_path)
    broker.fill("GOOD", qty, entry=10.00, price=10.05)
    trader.tick()
    broker.positions, broker.open_orders = [], []
    trader.tick()
    assert trader.state["managed"] == {} and broker.sells == []
    assert "CLOSED AT BROKER" in (tmp_path / "trades.csv").read_text()


def test_rh_sells_if_price_below_stop_without_stop_order(tmp_path):
    broker, trader, qty = start_rh_trade(tmp_path)
    broker.fill("GOOD", qty, entry=10.00, price=9.70)
    trader.tick()
    assert broker.sells == [("GOOD", qty)]


def test_rh_retries_rejected_sell(tmp_path):
    broker, trader, qty = start_rh_trade(tmp_path)
    broker.fill("GOOD", qty, entry=10.00, price=10.05)
    trader.tick()
    broker.fill("GOOD", qty, entry=10.00, price=10.45)
    trader.tick()
    broker.open_orders = []  # the sell was rejected; shares still held
    trader.tick()
    assert broker.sells == [("GOOD", qty), ("GOOD", qty)]


def test_rh_cancels_stale_buy(tmp_path):
    broker, trader, _ = start_rh_trade(tmp_path)
    buy_id = broker.orders[0]["id"]
    broker.now += timedelta(minutes=6)
    trader.tick()
    assert buy_id in broker.cancelled
    assert "GOOD" not in trader.state["managed"]


def test_state_survives_restart(tmp_path):
    broker, trader, qty = start_rh_trade(tmp_path)
    broker.fill("GOOD", qty, entry=10.00, price=10.05)
    restarted = AutoTrader(make_config(), broker, data_dir=str(tmp_path))
    restarted.tick()
    assert broker.orders[-1]["type"] == "stop"
    assert sum(o["side"] == "buy" for o in broker.orders) == 1
