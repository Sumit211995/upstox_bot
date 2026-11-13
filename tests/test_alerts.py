from datetime import date

import alerts


def _acct():
    plan = [dict(sym="NIFTY", kind="CE", strike=25300.0, strike2=25600.0, expiry="2026-12-29", price=110.0, stop=55.0,
                 target=None, strategy_label="Index · smart money", why="x")]
    trade = dict(sym="NIFTY", kind="PE", strike=25000.0, strike2=None, expiry="2026-12-29", entry=200.0, stop=100.0,
                 target=400.0, lot=65, reason="open", entry_day="2026-11-10")
    return [("₹20k", dict(plan=plan, trades=[trade], asof="2026-11-12")), ("₹50k", dict(plan=plan, trades=[trade]))]


def test_morning_calls_once_and_deduped(tmp_path, monkeypatch):
    monkeypatch.setattr(alerts, "STATE", tmp_path)
    sent = []
    msg = alerts.morning_calls(sent.append, date(2026, 11, 13), _acct())
    assert len(sent) == 1 and msg.count("BUY NIFTY 25300 CE") == 1 and "SELL 25600 CE" in msg
    assert alerts.morning_calls(sent.append, date(2026, 11, 13), _acct()) is None and len(sent) == 1


def test_watch_alerts_once_per_level(tmp_path, monkeypatch):
    monkeypatch.setattr(alerts, "STATE", tmp_path)
    mark = lambda t, asof, ref: dict(price=95.0, pnl=-6500, source="live", hit_stop=True, hit_target=False)
    pos = alerts.option_positions(_acct(), mark)
    assert len(pos) == 1                                          # same position in two accounts → one alert
    sent = []
    alerts.watch(sent.append, date(2026, 11, 13), pos)
    alerts.watch(sent.append, date(2026, 11, 13), pos)
    assert len(sent) == 1 and "STOP hit" in sent[0]
