"""Stocks tab: share (delivery) swing trading, PAPER only. Runs in the evening after the buying-books job.

Strategy (research/stock_research.py, chosen on 2019–23, judged on 2024–26): 12-month momentum. Every 21 trading days
hold the 3 large-cap shares with the strongest return from 12 months ago to 1 month ago (only above their EMA 200),
and only while Nifty is above its EMA 200; otherwise stay in cash. Orders decided at the close fill at the next open.

Writes state/stocks_acct.pkl (the paper account), state/stocks.json (what the dashboard shows) and sends the calls
for tomorrow's open to Telegram; `morning_calls` repeats them at 09:15.
"""
import json
import logging
import pickle
from datetime import date, datetime

import news
import stocks
from config import HERE

log = logging.getLogger("stocks")
STATE = HERE / "state"
ACCT, VIEW = STATE / "stocks_acct.pkl", STATE / "stocks.json"
RESEARCH = HERE / "research" / "out" / "stock_final.json"
CAPITAL = 20000.0
TOP = 10                             # ranking rows shown on the page


def _load():
    try:
        return pickle.loads(ACCT.read_bytes())
    except Exception:
        return None


def _iso(x):
    return x.isoformat() if isinstance(x, (date, datetime)) else x


def _clean(o):
    if isinstance(o, dict):
        return {k: _clean(v) for k, v in o.items()}
    if isinstance(o, (list, tuple)):
        return [_clean(v) for v in o]
    if isinstance(o, float) and o != o:
        return None
    return _iso(o)


HORIZON = "Swing · about 1–3 months"


def _orders(st):
    p = st["params"]
    sl = f"−{100 * p['stop_pct']:.0f}%" if p.get("stop_pct") else None
    return ([dict(side="SELL", sym=o["sym"], reason=o.get("reason", ""), when="next 09:15 open") for o in st["pending_sell"]]
            + [dict(side="BUY", sym=o["sym"], ref=round(o["ref"], 2), why=o.get("why", ""), when="next 09:15 open",
                    stop=round(o["stop"], 2) if o.get("stop") else None, stop_pct=sl, target=None,
                    target_rule=f"none fixed: held while in the top {p['max_pos']} (reviewed every {p['freq']} trading days)",
                    horizon=p.get("horizon") or HORIZON) for o in st["pending_buy"]])


def calls_text(orders, when="tomorrow's open"):
    lines = []
    for o in orders:
        if o["side"] == "BUY":
            lines.append(f"• BUY {o['sym']}\n  Entry: {when} (ref ₹{o['ref']:,.2f})"
                         + (f"\n  SL: ₹{o['stop']:,.2f} ({o['stop_pct']} from entry)" if o.get("stop") else "")
                         + f"\n  Target: {o['target_rule']}\n  Time: {o['horizon']}\n  Why: {o.get('why', '')}")
        else:
            lines.append(f"• SELL {o['sym']} at {when} · {o.get('reason', '')}")
    return f"📈 STOCK CALL · {when} (paper, ₹{CAPITAL:,.0f} momentum account)\n" + "\n".join(lines)


def run(notify=None, today=None):
    """Evening job. Returns the view dict written to state/stocks.json."""
    prep = stocks.build(refresh=True)
    st = _load()
    if st is None:
        st = stocks.new_account(CAPITAL)
        last = prep["days"][-1]
        if (today or datetime.now(stocks.market.IST).date()) > last:
            # started during the day (today's prices not in yet): begin AFTER the last finished day, so the first
            # decision is made on today's close and bought at the next open, never at an open that already passed
            st["asof"] = last
    if not st["positions"] and not st["pending_buy"]:
        st["params"] = dict(stocks.FINAL)                      # no open trades: pick up the current rule set
    st, events = stocks.paper_step(st, today=today, prep=prep)
    STATE.mkdir(exist_ok=True)
    ACCT.write_bytes(pickle.dumps(st))
    day = st["asof"]
    m = prep["mkt"].get(day, {})
    ranked = stocks.momentum_rank(prep, day, st["params"])[:TOP]
    rows = prep["rows"]
    held = {p["sym"] for p in st["positions"]}
    for i, x in enumerate(ranked):
        x["rank"], x["held"], x["pick"] = i + 1, x["sym"] in held, i < st["params"]["max_pos"]
    heads = {}
    try:
        names = stocks.names()
    except Exception:
        names = {}
    for s in [x["sym"] for x in ranked[:5]] + sorted(held):
        if s not in heads:
            try:
                heads[s] = news.headlines(s, limit=4, name=names.get(s))
            except Exception:
                heads[s] = []
    positions = []
    for p in st["positions"]:
        r = rows[p["sym"]].get(day)
        last = r["close"] if r else p["last"]
        positions.append(dict(sym=p["sym"], shares=p["shares"], entry=round(p["entry"], 2), entry_day=p["entry_day"],
                              cost=round(p["cost"], 2), last=round(last, 2), value=round(last * p["shares"], 2),
                              pnl=round(last * p["shares"] - p["cost"], 2), days=p["days"], why=p.get("why", ""),
                              stop=round(p["stop"], 2) if p.get("stop") else None, target=None))
    eq = st["equity"][-1][1] if st["equity"] else st["capital"]
    try:
        research = json.loads(RESEARCH.read_text())
    except Exception:
        research = {}
    orders = _orders(st)
    try:
        old_orders = json.loads(VIEW.read_text()).get("orders")
    except Exception:
        old_orders = None
    view = dict(asof=day, updated=datetime.now().timestamp(), capital=st["capital"], cash=round(st["cash"], 2),
                equity=round(eq, 2), positions=positions, closed=st["closed"][::-1], equity_curve=st["equity"][-260:],
                orders=orders, next_rebalance_in=(-st["n"]) % st["params"]["freq"], params=st["params"],
                market=dict(nifty_up=bool(m.get("nifty_up")), vix_rank=m.get("vix_rank")), ranking=ranked, news=heads,
                rule=stocks.exit_rule_text(st["params"]), research=research, events=events,
                horizon=st["params"].get("horizon") or HORIZON)
    VIEW.write_text(json.dumps(_clean(view)))
    if notify:
        for e in events:
            if e["type"] == "buy":
                notify(f"📈 PAPER BUY {e['sym']} · {e['shares']} shares @ ₹{e['price']:,.2f} (₹{e['cost']:,.0f})\n{e.get('why', '')}")
            elif e["type"] == "sell":
                notify(f"📉 PAPER SELL {e['sym']} · {e['shares']} @ ₹{e['exit']:,.2f} · P&L ₹{e['pnl']:,.0f} ({e['reason']})")
            elif e["type"] == "skip":
                notify(f"📈 skipped {e['sym']}: {e['reason']}")
        if orders and _clean(orders) != old_orders:                 # evening retries never repeat the call
            notify(calls_text(orders))
    log.info("stock job done for %s (%d orders for the next open)", day, len(orders))
    return view


def morning_orders():
    """The orders waiting for today's open (from last night), for the 09:15 live call."""
    st = _load()
    return _orders(st) if st else []
