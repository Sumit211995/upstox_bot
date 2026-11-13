from datetime import date, timedelta

import numpy as np
import pandas as pd
import pytest

import stocks


def _frame(closes, start=date(2024, 1, 1), opens=None, vol=1000.0):
    days = [start + timedelta(days=i) for i in range(len(closes))]
    c = np.asarray(closes, dtype=float)
    o = np.asarray(opens, dtype=float) if opens is not None else c
    return pd.DataFrame(dict(day=days, open=o, high=np.maximum(o, c) * 1.001, low=np.minimum(o, c) * 0.999,
                             close=c, volume=vol))


def test_costs_delivery():
    buy, sell = stocks.costs(10000, "buy"), stocks.costs(10000, "sell")
    # STT 10 + brokerage 20 + stamp 1.5 + GST on (20 + tiny) ≈ 35.2
    assert 34 < buy < 37
    assert sell > buy                     # DP charge on the sell side
    assert stocks.costs(100, "buy") < 5   # brokerage capped at 2.5%
    assert stocks.costs(0, "sell") == 0


def test_indicators_no_lookahead():
    d = stocks.indicators(_frame(np.linspace(100, 300, 300)))
    # prior 20-day high excludes today: on a rising series it is yesterday's high, below today's close
    assert d["hi20"].iloc[-1] < d["close"].iloc[-1]
    assert d["ema200"].iloc[:200].isna().all() and d["ema200"].iloc[-1] > 0
    assert d["rsi2"].iloc[-1] > 90         # straight up → RSI near 100
    # momentum uses the close 21 days ago, not today's
    assert d["mom252"].iloc[-1] == pytest.approx(d["close"].iloc[-22] / d["close"].iloc[-253] - 1) or np.isnan(d["mom252"].iloc[-1])


def _prep(frames, nifty_up=True):
    p = stocks.prepare(frames)
    p["mkt"] = {d: dict(nifty_up=nifty_up, vix_rank=0.5, smart=0.5) for d in p["days"]}
    return p


def test_momentum_buys_next_open_and_rebalances():
    n = 300
    up = _frame(np.linspace(100, 400, n), opens=np.linspace(100, 400, n) + 1)     # strong winner
    flat = _frame(np.full(n, 100.0))
    down = _frame(np.linspace(400, 100, n))
    prep = _prep({"UP": up, "FLAT": flat, "DOWN": down})
    p = dict(stocks.FINAL, max_pos=1, freq=21)
    days = prep["days"]
    st = stocks.simulate(prep, p, days[260], days[-1], 20000.0)
    buys = [t for t in st["closed"]]
    assert buys and buys[0]["sym"] == "UP"
    # signal on days[260]'s close → filled at the NEXT day's open (+ slippage), never the signal-day close
    pos = buys[0]
    assert pos["entry_day"] == days[261]
    assert pos["entry"] == pytest.approx(up.loc[261, "open"] * (1 + stocks.SLIP), abs=0.01)


def test_market_filter_keeps_cash():
    n = 300
    up = _frame(np.linspace(100, 400, n))
    prep = _prep({"UP": up}, nifty_up=False)
    st = stocks.simulate(prep, dict(stocks.FINAL, max_pos=1), prep["days"][260], prep["days"][-1], 20000.0)
    assert not st["closed"] and st["equity"][-1][1] == 20000.0


def test_paper_step_matches_simulate_and_is_idempotent():
    n = 300
    frames = {"UP": _frame(np.linspace(100, 400, n)), "FLAT": _frame(np.full(n, 100.0))}
    prep = _prep(frames)
    p = dict(stocks.FINAL, max_pos=1)
    days = prep["days"]
    acct = stocks.new_account(20000.0, p)
    acct["asof"] = days[259]                                   # start tracking from day 260
    acct, ev = stocks.paper_step(acct, days[-1], prep)
    sim = stocks.simulate(prep, p, days[260], days[-1], 20000.0)
    assert acct["equity"][-1][1] == sim["equity"][-1][1]
    assert any(e["type"] == "buy" for e in ev)
    again, ev2 = stocks.paper_step(acct, days[-1], prep)       # nothing new to do
    assert ev2 == []


def test_pullback_entry_and_rsi_exit():
    closes = list(np.linspace(100, 200, 260)) + [190, 180, 175] + [190, 205, 215, 220]
    prep = _prep({"A": _frame(closes)})
    p = dict(kind="pullback", rsi_thr=10, exit="rsi70", stop_atr=None, max_hold=10, max_pos=1, regime=False,
             vix_max=None, smart_min=None)
    st = stocks.simulate(prep, p, prep["days"][250], prep["days"][-1], 20000.0)
    t = [x for x in st["closed"] if x["reason"] != "open"]
    assert t and t[0]["reason"] == "RSI(2) > 70" and t[0]["entry_day"] > t[0]["signal_day"]


def test_split_gap_detector_and_adjust():
    df = _frame([100, 101, 50, 51], opens=[100, 101, 50, 51])
    assert stocks.split_gaps(df) and stocks.split_gaps(df)[0][1] < 0.6
    adj = stocks.adjust("TMPV", _frame([100.0, 100.0], start=date(2025, 10, 13)))
    assert adj["close"].iloc[0] == pytest.approx(60.5) and adj["close"].iloc[1] == 100.0


def test_signals_momentum_lists_top_k():
    n = 300
    prep = _prep({"UP": _frame(np.linspace(100, 400, n)), "UP2": _frame(np.linspace(100, 250, n)),
                  "DOWN": _frame(np.linspace(400, 100, n))})
    out = stocks.signals(prep["days"][-1], prep, dict(stocks.FINAL, max_pos=2))
    assert [x["sym"] for x in out] == ["UP", "UP2"] and out[0]["exit_rule"]
