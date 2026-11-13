import json
import sys
from datetime import date, datetime
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import engine  # noqa: E402
import market  # noqa: E402
import options  # noqa: E402

IST = engine.IST


def ts(y, m, d, hh, mm):
    return int(datetime(y, m, d, hh, mm, tzinfo=IST).timestamp())


# ------------------------------------------------------------------ option maths
def test_put_call_parity():
    s, k, t, v = 25000, 25100, 5 / 252, 0.14
    c, p = options.bs_price(s, k, t, v, "CE"), options.bs_price(s, k, t, v, "PE")
    assert abs((c - p) - (s - k * np.exp(-options.RATE * t))) < 1e-6


def test_implied_vol_roundtrip():
    price = options.bs_price(25000, 25000, 3 / 252, 0.17, "CE")
    assert abs(options.implied_vol(price, 25000, 25000, 3 / 252, "CE") - 0.17) < 1e-4


def test_trading_clock():
    # Monday 15:30 → Tuesday 15:30 expiry = exactly one session
    assert abs(options.trading_years(ts(2026, 11, 16, 15, 30), ts(2026, 11, 17, 15, 30)) - 1 / 252) < 1e-9
    # Friday close → Monday close: the weekend adds no time decay
    assert abs(options.trading_years(ts(2026, 11, 13, 15, 30), ts(2026, 11, 16, 15, 30)) - 1 / 252) < 1e-9
    # expiry day 12:22 → half of the 375-minute session left (188 min)
    assert abs(options.trading_years(ts(2026, 11, 17, 12, 22), ts(2026, 11, 17, 15, 30)) - 188 / 375 / 252) < 1e-9
    assert options.trading_years(ts(2026, 11, 17, 15, 31), ts(2026, 11, 17, 15, 30)) == 0


def test_premium_decays_into_expiry():
    e = ts(2026, 11, 17, 15, 30)
    a = options.premium("NIFTY", 25000, 25000, "CE", e, ts(2026, 11, 17, 9, 20), 15)
    b = options.premium("NIFTY", 25000, 25000, "CE", e, ts(2026, 11, 17, 15, 15), 15)
    assert a > 3 * b > 0


def test_charges():
    buy, sell = options.charges(10000, "buy"), options.charges(10000, "sell")
    assert sell - buy > 9                      # STT 0.1% only on the sell side
    assert 20 < buy < 30                       # ₹20 brokerage + small charges + GST


def test_strikes():
    assert options.atm_strike(25037, 50) == 25050
    assert options.pick_strike(25037, 50, "CE", 1) == 25000      # 1 strike in the money for a call
    assert options.pick_strike(25037, 50, "PE", 1) == 25100


# ------------------------------------------------------------------ simulator on synthetic candles
def synthetic_day(day, path, vix=15.0):
    """5-minute candles for one session following `path` (list of closes, 75 values)."""
    t0 = ts(day.year, day.month, day.day, 9, 15)
    rows = []
    prev = path[0]
    for i, c in enumerate(path):
        rows.append(dict(time=t0 + 300 * i, open=prev, high=max(prev, c) + 2, low=min(prev, c) - 2, close=c))
        prev = c
    df = pd.DataFrame(rows)
    return engine.prepare(df, df[["time"]].assign(vix=vix))


@pytest.fixture
def expiry_day(monkeypatch):
    d = date(2026, 11, 17)
    monkeypatch.setattr(engine, "expiries", lambda ix: [d])
    return d


def test_quiet_expiry_day_straddle_wins(expiry_day):
    df = synthetic_day(expiry_day, [25000 + (i % 3) for i in range(75)])
    tr = engine.simulate_straddle(df, "NIFTY", dict(entry_hm=1000, sl=0.6, wing=10, days="expiry"))
    assert len(tr) == 1 and tr[0]["result"] == "time" and tr[0]["pnl"] > 0


def test_big_move_stops_the_losing_leg(expiry_day):
    df = synthetic_day(expiry_day, [25000 + max(0, i - 12) * 12 for i in range(75)])     # rallies ~750 points
    tr = engine.simulate_straddle(df, "NIFTY", dict(entry_hm=1000, sl=0.6, wing=10, days="expiry"))
    ce = next(l for l in tr[0]["legs"] if l["kind"] == "CE" and l["side"] == "SELL")
    pe = next(l for l in tr[0]["legs"] if l["kind"] == "PE" and l["side"] == "SELL")
    assert tr[0]["stops"] == "CE" and ce["stopped"]
    assert ce["exit"] <= ce["sl"] * 1.15          # loss on the stopped leg is capped near its stop
    assert pe["exit"] < pe["entry"] * 0.2        # the other leg decays: that is how the straddle still earns


def test_live_mode_keeps_open_trade(expiry_day):
    df = synthetic_day(expiry_day, [25000 + (i % 3) for i in range(75)]).iloc[:30]      # data ends at ~11:40
    closed = engine.simulate_straddle(df, "NIFTY", dict(entry_hm=1000, sl=0.6, wing=10, days="expiry"))
    live = engine.simulate_straddle(df, "NIFTY", dict(entry_hm=1000, sl=0.6, wing=10, days="expiry"), live=True)
    assert live[0]["result"] == "open" and closed[0]["result"] != "open"


def test_non_expiry_day_skipped(expiry_day):
    df = synthetic_day(date(2026, 11, 16), [25000] * 75)
    assert engine.simulate_straddle(df, "NIFTY", dict(entry_hm=1000, sl=0.6, wing=10, days="expiry")) == []


def test_resample_session_aligned():
    df = synthetic_day(date(2026, 11, 16), list(range(25000, 25075)))
    b = engine.bars(df, 15)
    first = datetime.fromtimestamp(int(b["time"].iloc[0]), IST)
    assert (first.hour, first.minute) == (9, 15) and len(b) == 25


# ------------------------------------------------------------------ safety
def test_paper_only_no_order_code():
    src = " ".join(p.read_text() for p in ROOT.glob("*.py"))
    for bad in ("place_order", "/order/place", "v2/order", "modify_order", "api-hft"):
        assert bad not in src


def test_books_file_valid():
    books = json.loads((ROOT / "books.json").read_text())
    assert books and len({b["id"] for b in books}) == len(books)
    for b in books:
        assert b["index"] in market.INDICES and b["family"] in ("straddle", "orb", "supertrend", "pullback")
        if b["family"] == "straddle":
            assert b["params"]["wing"] > 0, "paper books must be hedged (wings) to keep risk and margin bounded"


def test_epoch_seconds_any_pandas_unit():
    import pandas as pd
    import market
    want = 1794455100                                             # 2026-11-12 09:15 IST
    for unit in ("ns", "us", "s"):                                # pandas 3 stores µs / s, pandas 2 ns
        s = pd.Series(pd.to_datetime(["2026-11-12T09:15:00+05:30"])).astype(f"datetime64[{unit}, UTC]")
        assert int(market.epoch_s(s).iloc[0]) == want
    f = market._frame([["2026-11-12T09:15:00+05:30", 1, 2, 0.5, 1.5]])
    assert int(f["time"].iloc[0]) == want


def test_save_pickle_is_atomic(tmp_path):
    import pandas as pd
    import market
    p = tmp_path / "x.pkl"
    market.save_pickle(pd.DataFrame(dict(a=[1, 2])), p)
    market.save_pickle(pd.DataFrame(dict(a=[3])), p)
    assert list(pd.read_pickle(p)["a"]) == [3] and [f.name for f in tmp_path.iterdir()] == ["x.pkl"]
