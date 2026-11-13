from datetime import date, timedelta

import numpy as np
import pandas as pd

import bigmoney_stocks as bm

CSV = """SYMBOL, SERIES, DATE1, PREV_CLOSE, OPEN_PRICE, HIGH_PRICE, LOW_PRICE, LAST_PRICE, CLOSE_PRICE, AVG_PRICE, TTL_TRD_QNTY, TURNOVER_LACS, NO_OF_TRADES, DELIV_QTY, DELIV_PER
TITAN, EQ, 12-Nov-2026, 4970.0, 4970.0, 5030.0, 4896.0, 4921.9, 4921.9, 4948.59, 732932, 36269.77, 67478, 395514, 53.96
TITAN, BL, 12-Nov-2026, 4970.0, 4970.0, 5030.0, 4896.0, 4921.9, 4921.9, 4948.59, 1000, 49.0, 1, 1000, 100.00
TATAMOTORS, EQ, 12-Nov-2024, 1, 1, 1, 1, 1, 790.5, 1, 1000, 1, 10, 400, 40.00
NOTINLIST, EQ, 12-Nov-2026, 1, 1, 1, 1, 1, 10.0, 1, 1000, 1, 10, -, -
"""


def test_parse_keeps_eq_universe_and_aliases():
    d = bm.parse(CSV)
    assert sorted(d["sym"]) == ["TITAN", "TMPV"]                       # BL series and unknown symbols dropped
    t = d[d.sym == "TITAN"].iloc[0]
    assert t.deliv_qty == 395514 and abs(t.deliv_pct - 53.96) < 1e-9 and t.day == date(2026, 11, 12)
    assert d[d.sym == "TMPV"].iloc[0].day == date(2024, 11, 12)          # old TATAMOTORS rows map to TMPV


def test_parse_handles_dash_values():
    d = bm.parse(CSV.replace("TITAN, EQ, 12-Nov-2026, 4970.0, 4970.0, 5030.0, 4896.0, 4921.9, 4921.9, 4948.59, 732932, 36269.77, 67478, 395514, 53.96",
                             "TITAN, EQ, 12-Nov-2026, 4970.0, 4970.0, 5030.0, 4896.0, 4921.9, 4921.9, 4948.59, 732932, 36269.77, 67478, -, -"))
    assert np.isnan(d[d.sym == "TITAN"].iloc[0].deliv_qty)


def _series(n=120, spike_at=None):
    days = [date(2025, 1, 1) + timedelta(i) for i in range(n)]
    q = np.full(n, 1000.0)
    if spike_at is not None:
        q[spike_at] = 3000.0
    close = 100 + np.arange(n) * 0.1
    return pd.DataFrame(dict(sym="TITAN", day=days, close=close, qty=q * 2, deliv_qty=q, deliv_pct=np.full(n, 50.0), trades=1))


def test_features_spike_and_no_lookahead():
    f = bm.add_features(_series(spike_at=100))
    assert abs(f.loc[100, "deliv_x"] - 3.0) < 1e-9                   # today vs PREVIOUS 20 days
    assert abs(f.loc[99, "deliv_x"] - 1.0) < 1e-9                    # the spike is not visible the day before
    g = bm.add_features(_series(spike_at=100).iloc[:100])            # truncating the future changes nothing
    pd.testing.assert_series_equal(f.loc[:99, "deliv_trend"], g["deliv_trend"], check_names=False)


def test_accumulation_flag():
    d = _series()
    d.loc[60, "deliv_pct"] = 80.0                                    # price up every day + delivery above its average
    f = bm.add_features(d)
    assert bool(f.loc[60, "accum"]) and not bool(f.loc[61, "accum"])
    assert 0 < f.loc[65, "accum20"] <= 1
