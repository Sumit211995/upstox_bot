"""Stress-test the best option-buying lead (index OI-ratio extreme, ±2% strikes): neighbourhood grid, per index,
per year, slippage sensitivity, and a ₹20k one-position-at-a-time account. Output: research/out/buy_focus.json"""
import itertools, json, sys
from datetime import date
from pathlib import Path
import numpy as np, pandas as pd
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import fno, swing  # noqa

OUT = Path(__file__).resolve().parent / "out"
SPLIT = date(2025, 1, 1)
f = pd.read_pickle(OUT / "buy_features.pkl")
fo = fno.load_all(); fo = fo[fo["sym"].isin(fno.UNIVERSE)]
lots = fno.lots(); chain = swing.Chain(fo)
IDX = ("NIFTY", "BANKNIFTY", "MIDCPNIFTY")

def run(p, syms=IDX, slip=None):
    old = dict(swing.SLIP)
    if slip is not None:
        swing.SLIP["index"] = slip
    try:
        tr = [t for t in swing.simulate(f, chain, "oi", p, lots, syms=syms) if t["reason"] != "open"]
    finally:
        swing.SLIP.update(old)
    return tr

def split(tr):
    return swing.stats([t for t in tr if t["entry_day"] < SPLIT]), swing.stats([t for t in tr if t["entry_day"] >= SPLIT])

BASE = dict(mode="pct", thr=0.1, band=2, offset=0, sl=0.3, tp=None, hold=5, min_dte=15)
res = {"base": BASE}
# 1. neighbourhood
rows = []
for thr, band, sl, hold, dte in itertools.product((0.05, 0.1, 0.15, 0.2), (1, 2, 3, 5), (0.25, 0.3, 0.4), (3, 5, 7, 10), (10, 15, 20)):
    p = {**BASE, "thr": thr, "band": band, "sl": sl, "hold": hold, "min_dte": dte}
    a, b = split(run(p))
    rows.append(dict(thr=thr, band=band, sl=sl, hold=hold, min_dte=dte, tr_n=a["n"], tr_pf=a["pf"], tr_net=a["net"],
                     te_n=b["n"], te_pf=b["pf"], te_net=b["net"], te_win=b["win"], te_dd=b["maxdd"]))
nb = pd.DataFrame(rows); nb.to_csv(OUT / "buy_focus_grid.csv", index=False)
nb["both"] = (nb.tr_net > 0) & (nb.te_net > 0)
print("neighbourhood: %d settings, profitable in both periods: %.0f%%" % (len(nb), 100 * nb.both.mean()))
for k in ("thr", "band", "sl", "hold", "min_dte"):
    print(k, nb.groupby(k).agg(both=("both", "mean"), tr_pf=("tr_pf", "median"), te_pf=("te_pf", "median")).round(2).to_dict("index"))
res["neighbourhood_both_pct"] = round(100 * nb.both.mean(), 1)
# 2. per index / per year for base
tr = run(BASE)
print("\nBASE all:", split(tr))
for s in IDX:
    print(s, split([t for t in tr if t["sym"] == s]))
yrs = {}
for t in tr:
    y = t["entry_day"].year; d = yrs.setdefault(y, []); d.append(t)
for y, v in sorted(yrs.items()):
    print(y, swing.stats(v))
print("by side:", {k: swing.stats([t for t in tr if t["kind"] == k]) for k in ("CE", "PE")})
print("exit reasons:", pd.Series([t["reason"] for t in tr]).value_counts().to_dict())
# 3. slippage sensitivity
for sl_ in (0.005, 0.01, 0.02):
    print("slippage %.1f%%:" % (sl_ * 100), split(run(BASE, slip=sl_)))
json.dump(res, open(OUT / "buy_focus.json", "w"), default=str, indent=1)
