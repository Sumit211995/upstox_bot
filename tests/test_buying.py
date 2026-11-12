"""Option-buying engine: signal direction, real-price trade rules (stop / gap / time / spread), small-account mode."""
from datetime import date, timedelta

import numpy as np
import pandas as pd

import fno
import news
import swing


def test_oi_signal_buys_the_side_with_less_oi():
    f = pd.DataFrame({"cs2": [0.30, 0.50, 0.70], "cs2_pct": [0.05, 0.5, 0.95]})
    assert list(swing.signal(f, "oi", dict(mode="abs", thr=0.30, band=2))) == [1, 0, -1]     # 30:70 → buy CE
    assert list(swing.signal(f, "oi", dict(mode="pct", thr=0.10, band=2))) == [1, 0, -1]
    assert list(swing.signal(f, "oi", dict(mode="pct", thr=0.10, band=2, contra=True))) == [-1, 0, 1]


def _chain(prices, kind="CE", strikes=(100.0, 110.0), expiry=date(2026, 1, 30), sym="NIFTY"):
    """prices: list of (open, high, low, close) per day for strike 100; strike 110 = half of strike 100."""
    days = [date(2026, 1, 5) + timedelta(days=i) for i in range(len(prices))]
    rows = []
    for d, (o, h, l, c) in zip(days, prices):
        for k in strikes:
            m = 1.0 if k == strikes[0] else 0.5
            rows.append(dict(sym=sym, type=kind, expiry=expiry, strike=k, open=o * m, high=h * m, low=l * m,
                             close=c * m, volume=10.0, day=d))
    rows.append(dict(sym=sym, type="FUT", expiry=expiry, strike=0.0, open=1, high=1, low=1, close=1, volume=1, day=days[0]))
    return swing.Chain(pd.DataFrame(rows)), days


def _run(prices, p, budget=1e9, kind="CE"):
    ch, days = _chain(prices, kind)
    bars, menu = ch.sym("NIFTY")
    swing.SLIP["index"] = 0.0
    try:
        return swing.trade(ch, bars, menu, "NIFTY", days[0], 100.0, kind, dict(min_dte=1, **p), 1, 0.0, budget)
    finally:
        swing.SLIP["index"] = 0.005


def test_entry_is_next_day_open_and_stop_wins_ties():
    # day0 = signal day; day1 open 10 → entry 10; day2 touches both stop (7) and target (20) → stop assumed first
    t = _run([(9, 9, 9, 9), (10, 12, 9, 11), (11, 25, 6, 15)], dict(sl=0.3, tp=1.0, hold=5))
    assert t["entry"] == 10 and t["reason"] == "stop" and abs(t["exit"] - 7) < 1e-9


def test_gap_through_stop_fills_at_open_and_time_exit():
    t = _run([(9, 9, 9, 9), (10, 11, 9, 10), (5, 6, 4, 5)], dict(sl=0.3, tp=None, hold=5))
    assert t["reason"] == "stop (gap)" and t["exit"] == 5
    t = _run([(9, 9, 9, 9), (10, 11, 9, 10), (10, 12, 9, 12), (12, 14, 11, 13)], dict(sl=0.3, tp=None, hold=3))
    assert t["reason"] == "time" and t["exit"] == 13 and t["days"] == 3


def test_budget_steps_out_of_the_money_or_skips():
    t = _run([(9, 9, 9, 9), (10, 11, 9, 10), (10, 11, 9, 10)], dict(sl=0.3, tp=None, hold=2), budget=6)
    assert t["strike"] == 110.0 and t["entry"] == 5          # ATM (₹10) too dear → 1 strike OTM (₹5)
    assert _run([(9, 9, 9, 9), (10, 11, 9, 10)], dict(sl=0.3, hold=2, afford=False), budget=6) is None


def test_debit_spread_value_and_charges():
    t = _run([(9, 9, 9, 9), (10, 11, 9, 10), (14, 15, 13, 14)], dict(sl=0.5, tp=None, hold=2, spread=1))
    assert t["strike2"] == 110.0 and t["entry"] == 5 and t["exit"] == 7     # 10-5 in, 14-7 out
    assert t["pnl"] < 2 and t["charges"] > 0                                 # 4 orders of charges


def test_small_account_takes_one_position_at_a_time():
    ch, days = _chain([(10, 11, 9, 10)] * 8)
    f = pd.DataFrame(dict(sym="NIFTY", day=days, spot=100.0, cs2=0.2, cs2_pct=0.05))
    tr = swing.simulate(f, ch, "oi", dict(mode="pct", thr=0.1, band=2, sl=0.3, tp=None, hold=3, min_dte=1),
                        {"NIFTY": 1}, capital=1000)
    assert len(tr) >= 2 and all(a["exit_day"] < b["entry_day"] for a, b in zip(tr, tr[1:]))
    assert "equity" in tr[-1]


def test_participant_parse(tmp_path, monkeypatch):
    monkeypatch.setattr(fno, "PART_DIR", tmp_path)
    (tmp_path / "20251110.csv").write_text(
        '"Participant wise Open Interest",,,\nClient Type,a,b,c,d,e,f,g,h,i,j,k,l,m,n\n'
        "Client,1,2,3,4,5,6,7,8,9,10,11,12,13,14\nDII,1,2,3,4,5,6,7,8,9,10,11,12,13,14\n"
        "FII,27579,184286,3,4,5,6,7,8,9,10,11,12,13,14\nPro,1,2,3,4,5,6,7,8,9,10,11,12,13,14\nTOTAL,1,2\n")
    p = fno.participant(date(2025, 11, 10))
    assert p.loc["FII", "fut_idx_short"] == 184286 and list(p.index) == ["Client", "DII", "FII", "Pro"]


def test_news_mood_score():
    assert news.score("Sensex surges, Nifty hits record high") > 0
    assert news.score("Stocks crash as banks slump on fraud probe") < 0


def test_participant_table_and_smart_signal(tmp_path, monkeypatch):
    monkeypatch.setattr(fno, "PART_DIR", tmp_path)
    days = [date(2025, 1, 1) + timedelta(days=i) for i in range(70)]
    for i, d in enumerate(days):                       # DII getting steadily longer, FII options net rising
        rows = ["Client,100,%d,1,1,10,10,10,10,1,1,1,1,1,1" % (100 - i), "DII,%d,100,1,1,0,%d,0,0,1,1,1,1,1,1" % (100 + i, 200 - i),
                "FII,100,100,1,1,%d,10,10,10,1,1,1,1,1,1" % (10 + i), "Pro,100,100,1,1,10,10,10,10,1,1,1,1,1,1"]
        (tmp_path / f"{d:%Y%m%d}.csv").write_text("title\nClient Type,a\n" + "\n".join(rows) + "\n")
    t = fno.participant_table(days)
    assert len(t) == 70 and t["DII_fut_share"].iloc[-1] > t["DII_fut_share"].iloc[0]
    f = pd.DataFrame(dict(sym="NIFTY", day=days, spot=100.0)).merge(t, on="day")
    f["smart"] = swing.smart_score(f)
    s = swing.signal(f, "smart", dict(smart_thr=0.2))
    assert s.iloc[-1] in (0, 1) and (s >= 0).all()      # everything points up → never a PE signal


def _t(sym, d0, d1, cost, pnl, reason="time"):
    return dict(sym=sym, signal_day=d0 - timedelta(days=1), entry_day=d0, exit_day=d1, cost=cost, pnl=pnl, reason=reason)


def test_combined_account_slots_cash_and_priority():
    d = date(2025, 1, 6)
    cands = [(2, _t("NIFTY", d, d + timedelta(days=3), 9000, 500)),        # lower priority, same day
             (1, _t("BANKNIFTY", d, d + timedelta(days=2), 9000, -4000)),  # taken first (priority 1)
             (3, _t("RELIANCE", d, d + timedelta(days=1), 9000, 100)),     # lowest priority: no slot left (max 2)
             (1, _t("ITC", d + timedelta(days=3), d + timedelta(days=4), 17000, 1000))]  # cash 20k−4k = 16k … minus open NIFTY 9k → too dear
    tk = swing.combine(cands, 20000, max_pos=2)
    assert [t["sym"] for t in tk] == ["BANKNIFTY", "NIFTY"]
    assert max(t["equity"] for t in tk) == 16500 and min(t["equity"] for t in tk) == 16000   # ITC skipped: cash 7k


def test_technical_signals_breakout_and_ema_cross():
    n = 80
    c = np.r_[np.linspace(100, 90, 60), np.linspace(90, 120, 20)]          # downtrend, then a strong rally
    bars = pd.DataFrame(dict(sym="NIFTY", day=[date(2025, 1, 1) + timedelta(days=i) for i in range(n)],
                             o=c, h=c * 1.005, l=c * 0.995, c=c, v=np.nan))
    t = swing.technicals(bars)
    assert {"rsi14", "adx", "st", "hi20", "e50"} <= set(t.columns)
    bo = swing.signal(t, "breakout", dict(adx=0))
    assert (bo == 1).any() and not (bo.iloc[60:] == -1).any()             # breakouts up during the rally only
    assert t["rsi14"].iloc[-1] > 70


def test_live_reprice_moves_with_the_underlying():
    import live
    base = live.reprice("NIFTY", "CE", 25000, "2026-12-29", 300.0, 25000.0, "2026-11-11", 25000.0, now_ts=1794456000)
    up = live.reprice("NIFTY", "CE", 25000, "2026-12-29", 300.0, 25000.0, "2026-11-11", 25200.0, now_ts=1794456000)
    assert 250 < base < 320 and up > base + 60                            # ≈ delta 0.5 × 200 points


def test_dashboard_is_read_only_and_locked_down():
    import dashboard
    assert "frame-ancestors 'none'" in dashboard.SECURITY_HEADERS["Content-Security-Policy"]
    assert dashboard.Handler.do_POST is dashboard.Handler.do_DELETE


def test_vix_filter_skips_only_when_rank_high():
    import pandas as pd, swing
    f = pd.DataFrame(dict(smart=[0.95, 0.95, 0.05], vix_rank=[0.9, 0.5, float("nan")]))
    p = dict(smart_thr=0.2)
    assert list(swing.signal(f, "smart", p)) == [1, 1, -1]
    assert list(swing.signal(f, "smart", dict(p, vix_max_rank=0.8))) == [0, 1, -1]   # missing VIX never blocks


def test_vix_rank():
    import pandas as pd, swing
    from datetime import date, timedelta
    v = pd.DataFrame(dict(day=[date(2025, 1, 1) + timedelta(i) for i in range(61)], close=list(range(60)) + [100]))
    assert swing.vix_rank(v).iloc[-1] == 1.0
