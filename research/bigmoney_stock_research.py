"""Per-share big money (NSE delivery volume) — does it improve the share and stock-option strategies?

Delivery data starts Oct 2019, so: choose on 2020–2023, judge untouched on 2024–Nov 2026.
 A. Filter / rank boost on the share momentum account (stocks.FINAL incl. −15% stop).
 B. Stand-alone swing signal: unusual delivery (qty ≥ X× its 20-day avg) + close up ≥ 2% + close > EMA 50.
 C. Filter on the stock-option smart-money buys (buy_books.json stk-smart): skip signals the share's delivery disagrees with.
Keep rule: A / C must raise PF AND net (CAGR) in BOTH periods keeping ≥ 60% of trades; B must make money in both
periods with PF ≥ 1.3 and ≥ 30 trades each.
Outputs: research/out/bmstock_A.csv, bmstock_B.csv, bmstock_C.csv, bmstock_summary.json, bmstock_summary.md
"""
import json
import sys
from datetime import date
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import bigmoney_stocks as bm  # noqa
import stocks  # noqa

OUT = Path(__file__).resolve().parent / "out"
TR, TE = (date(2020, 1, 1), date(2023, 12, 31)), (date(2024, 1, 1), date(2026, 12, 31))
FEAT = ["deliv_pct", "deliv_avg20", "deliv_rank", "deliv_x", "accum", "deliv_trend", "accum20"]

deliv = bm.add_features(bm.load())
print("delivery rows", len(deliv), "days", deliv["day"].nunique(), deliv["day"].min(), "→", deliv["day"].max(),
      "symbols", deliv["sym"].nunique(), flush=True)
frames = stocks.load_all(refresh=False)
for s, df in frames.items():
    x = deliv[deliv["sym"] == s][["day"] + FEAT]
    df = df.merge(x, on="day", how="left")
    df["ret1"] = df["close"].pct_change()
    frames[s] = df
filters = stocks.market_filters(stocks.nifty_daily(refresh=False), refresh=False)
prep = stocks.prepare(frames, filters)


def ok(v):
    return v is not None and not (isinstance(v, float) and np.isnan(v))


def run(p, rank_fn=None, entry_fn=None):
    """stocks.simulate on both periods, with the momentum ranking / entry rule temporarily replaced."""
    orig_rank, orig_entry, orig_rnk = stocks.momentum_rank, stocks._entry_ok, stocks._rank
    try:
        if rank_fn:
            stocks.momentum_rank = rank_fn
        if entry_fn:
            stocks._entry_ok = entry_fn
            stocks._rank = lambda r, p: -(r.get("deliv_x") or 0)
        a = stocks.stats(stocks.simulate(prep, p, *TR, 20000.0))
        b = stocks.stats(stocks.simulate(prep, p, *TE, 20000.0))
    finally:
        stocks.momentum_rank, stocks._entry_ok, stocks._rank = orig_rank, orig_entry, orig_rnk
    return a, b


ORIG_RANK = stocks.momentum_rank


def ranker(keep=None, boost=None, pool=6):
    def fn(pr, day, p):
        r = ORIG_RANK(pr, day, p)
        rows = pr["rows"]
        get = lambda x, k: rows[x["sym"]].get(day, {}).get(k)
        if keep:
            r = [x for x in r if keep(lambda k, x=x: get(x, k))]
        if boost:
            top = r[:pool]
            top.sort(key=lambda x: -boost(lambda k, x=x: get(x, k), x))
            r = top + r[pool:]
        return r
    return fn


rows = []


def rec(group, name, a, b):
    rows.append(dict(group=group, test=name, **{f"tr_{k}": v for k, v in a.items()}, **{f"te_{k}": v for k, v in b.items()}))
    print(f"{group} {name:<46} | 20–23 n {a['n']} win {a['win']} pf {a['pf']} cagr {a['cagr']} dd {a['maxdd']} "
          f"| 24–26 n {b['n']} win {b['win']} pf {b['pf']} cagr {b['cagr']} dd {b['maxdd']}", flush=True)


# ---------------------------------------------------------------- A: momentum account
base = dict(stocks.FINAL)
rec("A", "baseline momentum (FINAL)", *run(base))
A = {
    "keep 20d delivery % rank ≥ 50%": ranker(keep=lambda g: not ok(g("deliv_rank")) or g("deliv_rank") >= 0.5),
    "keep 20d delivery % rank ≥ 30%": ranker(keep=lambda g: not ok(g("deliv_rank")) or g("deliv_rank") >= 0.3),
    "keep delivery qty trend (20d/60d) ≥ 1": ranker(keep=lambda g: not ok(g("deliv_trend")) or g("deliv_trend") >= 1.0),
    "keep accumulation days (20d) ≥ 40%": ranker(keep=lambda g: not ok(g("accum20")) or g("accum20") >= 0.4),
    "top-6 momentum re-ranked by delivery rank": ranker(boost=lambda g, x: g("deliv_rank") if ok(g("deliv_rank")) else 0.5),
    "rank by momentum × (0.5 + delivery rank)": ranker(boost=lambda g, x: x["mom"] * (0.5 + (g("deliv_rank") if ok(g("deliv_rank")) else 0.5)), pool=50),
}
for name, fn in A.items():
    rec("A", name, *run(base, rank_fn=fn))

# ---------------------------------------------------------------- B: stand-alone unusual-delivery swing
def entry(X):
    def fn(r, p):
        if not (r["close"] > 0) or not ok(r.get("ema50")) or not ok(r.get("deliv_x")) or not ok(r.get("ret1")):
            return False
        return r["deliv_x"] >= X and r["ret1"] >= 0.02 and r["close"] > r["ema50"]
    return fn


B = [(2.0, dict(atr_k=1.5, exit="rr", max_hold=15)), (2.0, dict(atr_k=2.0, exit="rr", max_hold=20)),
     (2.0, dict(atr_k=2.0, exit="trail", max_hold=30)), (3.0, dict(atr_k=1.5, exit="rr", max_hold=15)),
     (3.0, dict(atr_k=2.0, exit="rr", max_hold=20)), (3.0, dict(atr_k=2.0, exit="trail", max_hold=30))]
for X, ex in B:
    p = dict(kind="breakout", n=20, max_pos=3, regime=True, vix_max=None, smart_min=None, **ex)
    rec("B", f"delivery ≥ {X:g}× · {ex['exit']} {ex['atr_k']}×ATR · ≤{ex['max_hold']}d", *run(p, entry_fn=entry(X)))

pd.DataFrame([r for r in rows if r["group"] == "A"]).to_csv(OUT / "bmstock_A.csv", index=False)
pd.DataFrame([r for r in rows if r["group"] == "B"]).to_csv(OUT / "bmstock_B.csv", index=False)

# ---------------------------------------------------------------- C: stock-option smart-money buys
import fno, swing, swing_bot  # noqa: E402
f = swing.add_vix(pd.read_pickle(OUT / "buy_features_tech.pkl"))
fo = fno.load_all(); lots = fno.lots(); chain = swing.Chain(fo)
st = {s["id"]: s for s in swing_bot.load_books()[0]}["stk-smart"]
tr = [t for t in swing.simulate(f, chain, st["signal"], st["params"], lots, budget=20000, syms=st["syms"]) if t["reason"] != "open"]
t = pd.DataFrame(tr).merge(deliv[["sym", "day"] + FEAT].rename(columns={"day": "signal_day"}), on=["sym", "signal_day"], how="left")
C = {
    "all signals (baseline)": lambda r: True,
    "skip when delivery trend disagrees (CE needs 20d/60d ≥ 1, PE ≤ 1)":
        lambda r: not ok(r.deliv_trend) or (r.deliv_trend >= 1 if r.kind == "CE" else r.deliv_trend <= 1),
    "skip when accumulation disagrees (CE needs ≥ 40%, PE ≤ 60%)":
        lambda r: not ok(r.accum20) or (r.accum20 >= 0.4 if r.kind == "CE" else r.accum20 <= 0.6),
    "CE only: skip when delivery % rank < 30%": lambda r: r.kind != "CE" or not ok(r.deliv_rank) or r.deliv_rank >= 0.3,
}
crow = []
for name, keep in C.items():
    k = t[t.apply(keep, axis=1)]
    a = swing.stats(k[(k.signal_day >= TR[0]) & (k.signal_day <= TR[1])].to_dict("records"))
    b = swing.stats(k[k.signal_day >= TE[0]].to_dict("records"))
    crow.append(dict(test=name, tr_n=a["n"], tr_win=a["win"], tr_pf=a["pf"], tr_net=a["net"],
                     te_n=b["n"], te_win=b["win"], te_pf=b["pf"], te_net=b["net"]))
    print(f"C {name:<70} | 20–23 n {a['n']} win {a['win']} pf {a['pf']} net {a['net']} | 24–26 n {b['n']} win {b['win']} pf {b['pf']} net {b['net']}", flush=True)
pd.DataFrame(crow).to_csv(OUT / "bmstock_C.csv", index=False)


# ---------------------------------------------------------------- verdicts
def better(r, b0, key="cagr"):
    return all((r[f"{s}_pf"] or 0) > (b0[f"{s}_pf"] or 0) and (r[f"{s}_{key}"] or -1e9) > (b0[f"{s}_{key}"] or -1e9)
               and r[f"{s}_n"] >= 0.6 * b0[f"{s}_n"] for s in ("tr", "te"))


A0 = rows[0]
verdict = dict(
    A=[dict(test=r["test"], keep=better(r, A0)) for r in rows if r["group"] == "A" and r is not A0],
    B=[dict(test=r["test"], keep=all((r[f"{s}_pf"] or 0) >= 1.3 and (r[f"{s}_cagr"] or 0) > 0 and r[f"{s}_n"] >= 30 for s in ("tr", "te")))
       for r in rows if r["group"] == "B"],
    C=[dict(test=r["test"], keep=better(r, crow[0], "net")) for r in crow[1:]],
)
cov = dict(first=str(deliv["day"].min()), last=str(deliv["day"].max()), days=int(deliv["day"].nunique()),
           symbols=int(deliv["sym"].nunique()), missing=sorted(set(stocks.UNIVERSE) - set(deliv["sym"].unique())))
json.dump(dict(coverage=cov, A=[r for r in rows if r["group"] == "A"], B=[r for r in rows if r["group"] == "B"],
               C=crow, verdict=verdict), open(OUT / "bmstock_summary.json", "w"), indent=1, default=str)
print(json.dumps(verdict, indent=1))
