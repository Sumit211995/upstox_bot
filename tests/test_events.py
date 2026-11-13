from datetime import date, timedelta

import pandas as pd

import events


def _frame(closes, opens=None, vols=None, start=date(2024, 1, 1)):
    n = len(closes)
    opens = opens or closes
    vols = vols or [1000] * n
    return pd.DataFrame(dict(day=[start + timedelta(i) for i in range(n)], open=opens,
                             high=[max(o, c) * 1.01 for o, c in zip(opens, closes)],
                             low=[min(o, c) * 0.99 for o, c in zip(opens, closes)], close=closes, volume=vols))


def test_intraday_costs_small_and_sell_side_heavier():
    c = events.intraday_costs(10000, 10000)
    assert 5 < c < 30                                        # ₹5 brokerage each side + STT 0.025% on sell + …
    assert events.intraday_costs(0, 10000) > events.intraday_costs(10000, 0)   # STT is on the sell side


def test_detect_gap_and_news_day():
    closes = [100.0] * 40 + [106.0]
    opens = [100.0] * 40 + [104.0]
    vols = [1000] * 40 + [6000]
    prep = events.prepare({"AAA": _frame(closes, opens, vols)})
    ev = events.detect(prep)
    types = {e["type"] for e in ev}
    assert types == {"gap", "news-day"}
    nd = [e for e in ev if e["type"] == "news-day"][0]
    assert nd["direction"] == "up" and nd["move"] == 6.0 and nd["vol_mult"] == 6.0


def test_swing_buys_next_open_not_signal_close():
    closes = [100.0] * 40 + [106.0, 107.0, 108.0, 109.0]
    opens = [100.0] * 40 + [100.0, 110.0, 108.0, 109.0]       # next day opens at 110 (entry), not 106 (signal close)
    vols = [1000] * 40 + [6000, 1000, 1000, 1000]
    prep = events.prepare({"AAA": _frame(closes, opens, vols)})
    res = events.simulate_swing(prep, dict(move=0.04, vol=3, dir="up", hold=2, atr_k=None, max_pos=3),
                                date(2024, 1, 1), date(2024, 12, 31), 20000.0)
    t = res["trades"][0]
    assert abs(t["entry"] - 110 * (1 + events.SLIP_SWING)) < 1e-6 and t["days"] == 2 and t["reason"] == "time"


def test_intraday_fade_short_profits_when_gap_up_fills():
    closes = [100.0] * 30 + [101.0]
    opens = [100.0] * 30 + [104.0]                            # gaps up 4%, closes back at 101 → a short makes ≈ 3%
    prep = events.prepare({"AAA": _frame(closes, opens)})
    res = events.simulate_intraday(prep, dict(gap=0.03, mode="fade", per_day=1), date(2024, 1, 1), date(2024, 12, 31))
    t = res["trades"][0]
    assert t["side"] == "short" and t["pnl"] > 0 and abs(t["ret"] - 0.028) < 0.004
    st = events.stats(res)
    assert st["n"] == 1 and st["win"] == 100.0


def test_no_rule_passed_means_no_calls():
    prep = events.prepare({"AAA": _frame([100.0] * 30)})
    assert events.PASSED == [] and events.calls(prep) == []
