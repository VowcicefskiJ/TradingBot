from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from autotrader.broker import BrokerError
from autotrader.robinhood_broker import RobinhoodBroker


def iso(dt):
    return dt.isoformat().replace("+00:00", "Z")


def fake_rh(**overrides):
    now = datetime.now(timezone.utc)
    hours = {"date": now.astimezone().strftime("%Y-%m-%d"), "is_open": True,
             "opens_at": iso(now - timedelta(hours=2)), "closes_at": iso(now + timedelta(hours=4)),
             "next_open_hours": "next-url"}
    calls = []
    rh = SimpleNamespace(
        calls=calls,
        login=lambda **kw: {"access_token": "t"},
        logout=lambda: None,
        helper=SimpleNamespace(request_get=lambda url: {"opens_at": iso(now + timedelta(days=1))}),
        markets=SimpleNamespace(
            get_market_today_hours=lambda mic: hours,
            get_top_movers_sp500=lambda d: [
                {"symbol": "SPX1", "price_movement": {"market_hours_last_movement_pct": "6.5",
                                                      "market_hours_last_price": "120.00"}}],
            get_top_movers=lambda: [
                {"symbol": "HOT", "last_trade_price": "11.00", "adjusted_previous_close": "10.00",
                 "previous_close": "10.00", "trading_halted": False},
                {"symbol": "DOWN", "last_trade_price": "9.00", "adjusted_previous_close": "10.00",
                 "previous_close": "10.00", "trading_halted": False},
                {"symbol": "HALT", "last_trade_price": "20.00", "adjusted_previous_close": "10.00",
                 "previous_close": "10.00", "trading_halted": True}],
            get_top_100=lambda: [None],
        ),
        stocks=SimpleNamespace(
            get_symbol_by_url=lambda url: url.rsplit("/", 1)[-1],
            get_latest_price=lambda symbols: ["12.00" for _ in symbols],
            get_instruments_by_symbols=lambda s: [{"tradeable": True, "state": "active",
                                                   "type": "stock", "market": "https://x/markets/XNAS/"}],
            get_fundamentals=lambda s: [{"volume": "1234567.000000"}],
            get_stock_historicals=lambda *a, **k: [
                {"begins_at": iso(now - timedelta(minutes=5 * i)), "open_price": "1",
                 "high_price": "2", "low_price": "0.5", "close_price": str(i), "volume": 100}
                for i in range(3)],
        ),
        profiles=SimpleNamespace(
            load_account_profile=lambda: {"buying_power": "5000.00", "deactivated": False},
            load_portfolio_profile=lambda: {"equity": "9800", "adjusted_equity_previous_close": "10000"},
        ),
        account=SimpleNamespace(
            get_day_trades=lambda: {"equity_day_transactions": [{}, {}]},
            get_open_stock_positions=lambda: [
                {"instrument": "https://x/instruments/ABC", "quantity": "10.00000",
                 "average_buy_price": "10.0000"}],
        ),
        orders=SimpleNamespace(
            get_all_open_stock_orders=lambda: [
                {"id": "o1", "side": "sell", "type": "market", "instrument": "https://x/instruments/ABC"}],
            order_buy_market=lambda s, q, timeInForce: calls.append(("buy", s, q, timeInForce)) or {"id": "b1"},
            order_sell_market=lambda s, q, timeInForce: calls.append(("sell", s, q)) or {"id": "s1"},
            order_sell_stop_loss=lambda s, q, p, timeInForce: {"detail": "Not enough shares"},
            cancel_stock_order=lambda oid: calls.append(("cancel", oid)) or {},
            get_stock_order_info=lambda oid: {"state": "cancelled"},
        ),
    )
    for path, value in overrides.items():
        ns, attr = path.split(".")
        setattr(getattr(rh, ns), attr, value)
    return rh


def make(rh=None):
    broker = RobinhoodBroker("u", "p", api=rh or fake_rh())
    broker.login()
    return broker


def test_clock_open():
    clock = make().get_clock()
    assert clock["is_open"] is True
    assert datetime.fromisoformat(clock["next_close"]) > datetime.now(timezone.utc)


def test_clock_closed_on_holiday():
    rh = fake_rh(**{"markets.get_market_today_hours":
                    lambda mic: {"date": "x", "is_open": False, "opens_at": None,
                                 "closes_at": None, "next_open_hours": "next-url"}})
    clock = make(rh).get_clock()
    assert clock["is_open"] is False
    assert datetime.fromisoformat(clock["next_open"]) > datetime.now(timezone.utc)


def test_account_mapping():
    acct = make().get_account()
    assert acct == {"equity": "9800", "last_equity": "10000", "buying_power": "5000.00",
                    "daytrade_count": 2, "trading_blocked": False}


def test_gainers_only_up_and_not_halted():
    gainers = make().get_top_gainers()
    assert [g["symbol"] for g in gainers] == ["HOT", "SPX1"]
    assert gainers[0]["percent_change"] == pytest.approx(10.0)


def test_positions_and_orders_resolve_symbols():
    broker = make()
    [p] = broker.get_positions()
    assert p["symbol"] == "ABC" and p["current_price"] == 12.0
    assert p["unrealized_pl"] == pytest.approx(20.0)
    assert broker.get_open_orders()[0]["symbol"] == "ABC"


def test_bars_and_volume():
    broker = make()
    bars = broker.get_bars("ABC")
    assert list(bars["close"]) == [2.0, 1.0, 0.0]  # sorted oldest first
    assert broker.get_daily_volume("ABC") == 1234567
    assert broker.get_asset("ABC") == {"tradable": True, "exchange": "https://x/markets/XNAS/"}


def test_rejected_order_raises():
    with pytest.raises(BrokerError):
        make().sell_stop("ABC", 10, 9.5)


def test_close_position_cancels_then_sells_only_given_qty():
    rh = fake_rh()
    make(rh).close_position("ABC", 4)
    assert rh.calls == [("cancel", "o1"), ("sell", "ABC", 4)]


def test_failed_login_raises():
    rh = fake_rh()
    rh.login = lambda **kw: None
    with pytest.raises(BrokerError):
        RobinhoodBroker("u", "p", api=rh).login()
