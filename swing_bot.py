"""Evening job for the option-BUYING paper books (buy_books.json). PAPER ONLY: never places an order.

After NSE publishes the day's F&O file (~18:00–20:00 IST) it:
  1. downloads that day's option prices + OI and the FII/DII positions (free NSE files),
  2. replays every buying book with the SAME simulator as the backtest (swing.simulate) from the book's start date:
     entries fill at the REAL opening price, stops / exits use the REAL daily high / low / close,
  3. works out tomorrow's plan (signal at today's close → buy at tomorrow's open),
  4. sends new plans / fills / exits to Telegram and writes state/swing_<book>.json, logs/swing_<book>.csv,
     state/oi_scan.json (OI / FII / buildup for every index and stock, for the dashboard).
"""
import csv
import json
import logging
import time
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import numpy as np

import fno
import swing
from config import HERE
from notifier import send

log = logging.getLogger("swing")
IST = timezone(timedelta(hours=5, minutes=30))
STATE, LOGS = HERE / "state", HERE / "logs"
BOOKS_FILE = HERE / "buy_books.json"
LOOKBACK = 230                 # trading days kept in memory (60-day ranks, EMA 50, 20-day highs + warm-up); fits 1 GB
JCOLS = ["strategy", "sym", "signal_day", "entry_day", "exit_day", "kind", "strike", "strike2", "expiry", "entry", "exit", "stop",
         "target", "lot", "cost", "charges", "pnl", "reason", "days", "equity", "why"]


def load_books():
    """(strategies, accounts) from buy_books.json; "STOCKS" in syms expands to the stock universe."""
    if not BOOKS_FILE.exists():
        return [], []
    d = json.loads(BOOKS_FILE.read_text())
    strats = []
    for x in d.get("strategies", []):
        x = dict(x, syms=list(fno.STOCKS) if x["syms"] == "STOCKS" else x["syms"])
        strats.append(x)
    return strats, d.get("accounts", [])


def member_params(strat, acct):
    """A strategy's settings inside an account: index signals become debit spreads in the small account."""
    p = dict(strat["params"])
    if acct and acct.get("index_spread") and all(swing.is_index(s) for s in strat["syms"]):
        p.update(spread=acct["index_spread"], offset=0)
    return p


def _read(path, default):
    try:
        return json.loads(Path(path).read_text())
    except Exception:
        return default


def _iso(x):
    return x.isoformat() if isinstance(x, date) else x


def inr(x):
    return f"₹{x:,.0f}" if x >= 0 else f"-₹{-x:,.0f}"


def contract_text(t):
    s = f"{t['sym']} {t['strike']:.0f} {t['kind']}"
    if t.get("strike2"):
        s = f"{t['sym']} {t['strike']:.0f}/{t['strike2']:.0f} {t['kind']} spread"
    return s + f" (exp {_iso(t['expiry'])})"


# ------------------------------------------------------------------ data
def ensure_data(today, days_back=LOOKBACK):
    """Make sure the last `days_back` trading days are cached (first run on a new server downloads ~160 files)."""
    days = fno.trading_days(start=today - timedelta(days=int(days_back * 1.6)), end=today)[-days_back:]
    missing = [d for d in days if not (fno.FO_DIR / f"{d:%Y%m%d}.pkl").exists()]
    if missing:
        log.info("downloading %d NSE F&O files", len(missing))
        fno.download(missing, workers=4)
    return days


def build(today):
    """Features + option chain for the recent window. Returns (features, chain, last day with data)."""
    days = ensure_data(today)
    fo = fno.load_all(days)
    if fo.empty:
        return None, None, None
    last = max(fo["day"])
    part = fno.participant_table(sorted(fo["day"].unique()))
    f = swing.features(fo, part, swing.index_closes())
    tech = swing.technicals(swing.daily_bars(fo)).drop(columns=["ret1"])
    f = swing.add_vix(f.merge(tech, on=["sym", "day"], how="left"))
    return f, swing.Chain(fo), last


# ------------------------------------------------------------------ tonight's signals → tomorrow's contracts
def contract_for(chain, sym, day, spot, kind, p, budget):
    """The option (or debit spread) the backtest would buy at the next open, priced at today's REAL close, with the
    stop / target levels that will apply. Steps out of the money (max 3 strikes) until 1 lot fits the budget."""
    bars, menu = chain.sym(sym)
    lot = fno.lots().get(sym)
    if day not in menu or not lot:
        return None
    for step in range(4):
        c = swing.pick_contract(menu[day], day, spot, kind, p.get("offset", 0) - step, p.get("min_dte", 15))
        if c is None:
            return None
        e, k = c
        b = bars.get((day, e, k, kind))
        if not b or not b[3] > 0:
            return None
        px, k2 = b[3], None
        if p.get("spread"):
            ks = menu[day][(e, kind)]
            i = int(np.where(ks == k)[0][0]) + (p["spread"] if kind == "CE" else -p["spread"])
            if not 0 <= i < len(ks):
                return None
            k2 = float(ks[i])
            b2 = bars.get((day, e, k2, kind))
            if not b2:
                return None
            px = b[3] - b2[3]
        if px * lot <= budget or p.get("spread") and step == 3:
            break
    if px * lot > budget:
        return None
    sl, tp = p.get("sl", 0.3), p.get("tp")
    g = greeks_for(sym, kind, k, k2, e, day, float(spot), b[3], (b2[3] if k2 else None), lot)
    return dict(sym=sym, kind=kind, strike=k, strike2=k2, expiry=_iso(e), price=round(px, 2), lot=lot, **g,
                cost=round(px * lot, 0), stop=round(px * (1 - sl), 2), target=round(px * (1 + tp), 2) if tp else None,
                sl_pct=sl, tp_pct=tp, hold=p.get("hold"), spot=round(float(spot), 2))


def greeks_for(sym, kind, k, k2, expiry, day, spot, px1, px2, lot):
    """Greeks of the position for 1 lot, from the option's REAL close: implied vol → delta, theta (₹ lost per day),
    gamma, vega. For a debit spread: bought leg minus sold leg."""
    import options
    e = date.fromisoformat(_iso(expiry))
    t = options.trading_years(int(datetime(day.year, day.month, day.day, 15, 29, tzinfo=IST).timestamp()),
                              int(datetime(e.year, e.month, e.day, 15, 30, tzinfo=IST).timestamp()))
    out = {}
    try:
        iv1 = options.implied_vol(px1, spot, k, t, kind) or 0.2
        g = options.greeks(spot, k, t, iv1, kind)
        if k2 and px2:
            iv2 = options.implied_vol(px2, spot, k2, t, kind) or iv1
            g2 = options.greeks(spot, k2, t, iv2, kind)
            g = {x: g[x] - g2[x] for x in g}
        out = dict(iv=round(iv1 * 100, 1), delta=round(g["delta"], 2), gamma=round(g["gamma"] * lot, 3),
                   theta_day=round(g["theta"] * lot, 0), vega=round(g["vega"] * lot, 0), dte=(e - day).days)
    except Exception:
        pass
    return out


def tonight(f, chain, strat, last, p=None, budget=20000):
    """Every signal of one strategy on the last data day, with the contract it would buy tomorrow."""
    p = p or strat["params"]
    rows = f[(f["day"] == last) & f["sym"].isin(strat["syms"])]
    if rows.empty:
        return []
    sig = swing.signal(f, strat["signal"], p).loc[rows.index]
    out = []
    for r, sgn in zip(rows.itertuples(index=False), sig):
        if sgn == 0:
            continue
        c = contract_for(chain, r.sym, last, r.spot, "CE" if sgn > 0 else "PE", p, budget)
        if c:
            c.update(strategy=strat["id"], strategy_label=strat["label"], priority=strat["priority"],
                     signal_day=_iso(last), why=swing.why(r._asdict(), strat["signal"], p))
            out.append(c)
    return out


# ------------------------------------------------------------------ OI scanner (dashboard)
def suggest(chain, sym, day, spot, kind, min_dte=15):
    """The option the buying rules would pick (ATM, expiry ≥ 15 days), priced at today's REAL close."""
    bars, menu = chain.sym(sym)
    if day not in menu:
        return None
    c = swing.pick_contract(menu[day], day, spot, kind, 0, min_dte)
    if c is None:
        return None
    e, k = c
    b = bars.get((day, e, k, kind))
    lot = fno.lots().get(sym)
    if not b or not lot:
        return None
    return dict(kind=kind, strike=k, expiry=_iso(e), close=round(b[3], 2), lot=lot, cost=round(b[3] * lot, 0),
                fits_20k=b[3] * lot <= 20000)


def bigmoney(f, last):
    """FII / DII / Pro / Client positions (last 30 sessions), smart-money score and cash-market flows."""
    n = f[f["sym"] == "NIFTY"].sort_values("day").tail(30)
    rows = []
    for r in n.itertuples(index=False):
        d = r._asdict()
        x = dict(day=_iso(r.day), smart=round(float(d.get("smart", np.nan)) * 100, 1) if d.get("smart") == d.get("smart") else None,
                 nifty=round(r.spot, 2))
        for who in fno.WHO:
            for m in ("fut_share", "fut_net", "fut_net_chg", "opt_net", "opt_net_chg"):
                v = d.get(f"{who}_{m}")
                x[f"{who}_{m}"] = None if v is None or v != v else round(float(v) * (100 if m == "fut_share" else 1), 1)
        rows.append(x)
    cash = []
    try:
        c = fno.fii_dii_cash()
        cash = [dict(day=_iso(r.day), who=r.who, buy=r.buy, sell=r.sell, net=r.net) for r in c.tail(40).itertuples()]
    except Exception as e:
        log.warning("FII/DII cash fetch failed: %s", e)
    return dict(asof=_iso(last), rows=rows, cash=cash)



def scan(f, chain, last, books):
    rows = []
    for r in f[f["day"] == last].itertuples(index=False):
        d = r._asdict()
        x = dict(sym=r.sym, index=swing.is_index(r.sym), spot=round(r.spot, 2), ret=round(r.ret * 100, 2) if r.ret == r.ret else None,
                 cs2=round(r.cs2 * 100, 1) if r.cs2 == r.cs2 else None, cs5=round(r.cs5 * 100, 1) if r.cs5 == r.cs5 else None,
                 rank2=round(r.cs2_pct * 100, 0) if r.cs2_pct == r.cs2_pct else None,
                 pcr=round(r.pcr, 2) if r.pcr == r.pcr else None, support=r.support, resistance=r.resistance,
                 fut_oi_pct=round(r.fut_oi_pct * 100, 2) if r.fut_oi_pct == r.fut_oi_pct else None,
                 lot=fno.lots().get(r.sym))
        x["buildup"] = None if x["ret"] is None or x["fut_oi_pct"] is None else (
            ("long buildup" if x["fut_oi_pct"] > 0 else "short covering") if x["ret"] > 0 else
            ("short buildup" if x["fut_oi_pct"] > 0 else "long unwinding"))
        if "fii_long_share" in d and d["fii_long_share"] == d["fii_long_share"]:
            x["fii_long"] = round(d["fii_long_share"] * 100, 1)
            x["fii_chg"] = round(d["fii_share_chg"] * 100, 2) if d["fii_share_chg"] == d["fii_share_chg"] else None
        rows.append(x)
    oi_p = next((b["params"] for b in books if b["signal"] == "oi"), dict(mode="pct", thr=0.1, band=2))
    sm_p = next((b["params"] for b in books if b["signal"] == "smart"), dict(smart_thr=0.2))
    today = f[f["day"] == last]
    s_oi = swing.signal(f, "oi", oi_p).loc[today.index]
    s_sm = swing.signal(f, "smart", sm_p).loc[today.index] if "smart" in f else s_oi * 0
    for x, a, b, r in zip(rows, s_oi, s_sm, today.itertuples(index=False)):
        x["signal"] = "BUY CE" if a > 0 else "BUY PE" if a < 0 else None
        x["smart"] = round(float(r.smart) * 100, 0) if getattr(r, "smart", np.nan) == getattr(r, "smart", np.nan) else None
        x["smart_signal"] = ("BUY CE" if b > 0 else "BUY PE" if b < 0 else None) if x["index"] else None
        sig = x["signal"] or x["smart_signal"]
        x["opt"] = suggest(chain, r.sym, last, r.spot, sig[-2:]) if sig else None
    return rows


# ------------------------------------------------------------------ the evening job
def journal(book, t):
    path = LOGS / f"buy_{book['id']}.csv"
    path.parent.mkdir(exist_ok=True)
    new = not path.exists()
    with path.open("a", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=JCOLS, extrasaction="ignore")
        if new:
            w.writeheader()
        w.writerow({k: _iso(v) for k, v in t.items()})


def run(cfg, today=None, notify=True):
    """Run the evening job once. Returns the last data day processed (None if no data)."""
    today = today or datetime.now(IST).date()
    strats, accounts = load_books()
    f, chain, last = build(today)
    if f is None:
        return None
    lots = fno.lots()
    STATE.mkdir(exist_ok=True)
    by_id = {x["id"]: x for x in strats}
    # 1. tonight's signals of every strategy (shown on the dashboard, plain option, ₹20k max per lot)
    signals = []
    for st in strats:
        signals += tonight(f, chain, st, last)
    spot_of = {r.sym: float(r.spot) for r in f[f["day"] == last].itertuples(index=False)}
    (STATE / "buy_signals.json").write_text(json.dumps(dict(asof=_iso(last), updated=int(time.time()), signals=signals), default=float))
    # 2. the combined paper accounts
    for ac in accounts:
        start = date.fromisoformat(ac["start"])
        cands = []
        for mid in ac["members"]:
            st = by_id[mid]
            for t in swing.simulate(f, chain, st["signal"], member_params(st, ac), lots, budget=ac["budget"],
                                    syms=st["syms"], start=start):
                t["strategy"], t["strategy_label"] = st["id"], st["label"]
                cands.append((st["priority"], t))
        trades = swing.combine(cands, ac["capital"], ac["max_pos"], start=start)
        for t in trades:                                  # what the live estimate needs for open positions
            if t["reason"] == "open":
                bars, _ = chain.sym(t["sym"])
                b1 = bars.get((last, date.fromisoformat(_iso(t["expiry"])), t["strike"], t["kind"]))
                b2 = bars.get((last, date.fromisoformat(_iso(t["expiry"])), t.get("strike2"), t["kind"])) if t.get("strike2") else None
                t["last1"], t["last2"], t["ref_spot"] = (b1[3] if b1 else None), (b2[3] if b2 else None), spot_of.get(t["sym"])
                if b1 and t["ref_spot"]:
                    t.update(greeks_for(t["sym"], t["kind"], t["strike"], t.get("strike2"), t["expiry"], last,
                                        t["ref_spot"], b1[3], b2[3] if b2 else None, t["lot"]))
        open_ = [t for t in trades if t["reason"] == "open"]
        closed_pnl = sum(t["pnl"] for t in trades if t["reason"] != "open")
        cash = ac["capital"] + closed_pnl - sum(t["cost"] for t in open_)
        plan = []
        if last >= start:
            slots = ac["max_pos"] - len(open_)
            for st in sorted((by_id[m] for m in ac["members"]), key=lambda x: x["priority"]):
                for c in tonight(f, chain, st, last, member_params(st, ac), ac["budget"]):
                    if slots <= 0 or any(o["sym"] == c["sym"] for o in open_ + plan) or c["cost"] > cash / max(slots, 1):
                        continue
                    plan.append(c)
                    slots -= 1
                    cash -= c["cost"]
        sp = STATE / f"buy_{ac['id']}.json"
        old = _read(sp, {})
        seen = old.get("seen", {})
        for t in trades:
            k = f"{t['sym']}|{_iso(t['entry_day'])}"
            if k not in seen and notify:
                send(cfg, f"🟢 PAPER BUY filled · {ac['label']}\n{contract_text(t)} @ ₹{t['entry']:.2f} × {t['lot']} = {inr(t['cost'])}"
                          f" (real NSE open)\nstop ₹{t['stop']:.2f}" + (f" · target ₹{t['target']:.2f}" if t.get("target") else "")
                          + f"\n{t['strategy_label']}: {t['why']}")
                seen[k] = "open"
            if t["reason"] != "open" and seen.get(k) != "closed":
                journal(ac, t)
                if notify:
                    send(cfg, f"🏁 PAPER EXIT · {ac['label']} · {t['reason']}\n{contract_text(t)} ₹{t['entry']:.2f} → ₹{t['exit']:.2f}\n"
                              f"P&L {inr(t['pnl'])} (after ₹{t['charges']:.0f} charges) · account {inr(t['equity'])}")
                seen[k] = "closed"
        pk = "|".join(f"{c['sym']}{c['kind']}{c['signal_day']}" for c in plan)
        if plan and notify and old.get("plan_key") != pk:
            send(cfg, f"📋 PLAN for next session · {ac['label']}\n" + "\n".join(
                f"BUY {contract_text(c)} at 09:15 · ≈ ₹{c['price']} × {c['lot']} = {inr(c['cost'])} · stop ₹{c['stop']}"
                + (f" · target ₹{c['target']}" if c.get("target") else "") + f"\n  {c['strategy_label']}: {c['why']}" for c in plan)
                + "\nPaper only: no order is placed.")
        eq = ac["capital"] + closed_pnl
        sp.write_text(json.dumps(dict(asof=_iso(last), updated=int(time.time()), capital=ac["capital"], equity=eq,
                                      cash=round(cash, 0), plan=plan, plan_key=pk or old.get("plan_key"), seen=seen,
                                      trades=[{k: _iso(v) for k, v in t.items()} for t in trades]), default=float))
    try:
        (STATE / "bigmoney.json").write_text(json.dumps(bigmoney(f, last), default=float))
    except Exception:
        log.exception("bigmoney failed")
    try:
        import news
        news.tone()
    except Exception:
        pass
    (STATE / "oi_scan.json").write_text(json.dumps(dict(asof=_iso(last), updated=int(time.time()),
                                                        rows=scan(f, chain, last, strats)), default=float))
    log.info("swing job done for %s", last)
    return last

