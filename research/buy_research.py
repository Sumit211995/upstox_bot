"""Option-BUYING research on REAL NSE option prices (daily bhavcopy), small capital (₹20,000 per trade).

Walk-forward: every signal family × setting is simulated on 2022–2024 (tuning) and 2025–2026 (unseen test).
Output: research/out/buy_grid.csv (every run), buy_summary.md, buy_best.json.
Run:  python research/buy_research.py        (~10 min; needs `python fno.py` first to download the data)
"""
import itertools
import json
import sys
import time
from datetime import date
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import fno    # noqa: E402
import swing  # noqa: E402

OUT = Path(__file__).resolve().parent / "out"
SPLIT = date(2025, 1, 1)
BUDGET = 20000.0


def signal_variants():
    v = []
    for thr, band, contra in itertools.product((0.1, 0.2), (2, 5, 10), (False, True)):
        v.append(("oi", dict(mode="pct", thr=thr, band=band, contra=contra)))
    for thr, band in itertools.product((0.30, 0.35, 0.40), (2, 5)):
        v.append(("oi", dict(mode="abs", thr=thr, band=band)))                 # the user's fixed 30:70 rule
    for thr, band in itertools.product((0.1, 0.2), (2, 5, 10)):
        v.append(("oichg", dict(mode="pct", thr=thr, band=band)))
    for m, contra in itertools.product((0.0, 0.03), (False, True)):
        v.append(("buildup", dict(oi_min=m, contra=contra)))
    for e, contra in itertools.product((10, 20), (False, True)):
        v.append(("trend", dict(ema=e, contra=contra)))
    for t, contra in itertools.product((0.01, 0.03), (False, True)):
        v.append(("fii", dict(fii_thr=t, contra=contra)))
    for fam in ("oi+trend", "oi+buildup", "oi+fii", "oichg+oi"):
        for thr in (0.1, 0.2):
            v.append((fam, dict(mode="pct", thr=thr, band=5, ema=20, oi_min=0.0, fii_thr=0.01)))
    return v


TRADE = [dict(offset=o, sl=sl, tp=tp, hold=h, min_dte=d)
         for o, sl, tp, h, d in itertools.product((0, -1), (0.3, 0.5), (0.5, 1.0, None), (1, 3, 5), (5, 15))]


def label(fam, p):
    keys = [k for k in ("mode", "thr", "band", "contra", "oi_min", "ema", "fii_thr") if k in p]
    return fam + " " + " ".join(f"{k}={p[k]}" for k in keys)


def main():
    t0 = time.time()
    f = pd.read_pickle(OUT / "buy_features.pkl")
    fo = fno.load_all()
    fo = fo[fo["sym"].isin(fno.UNIVERSE)]
    lots = fno.lots()
    chain = swing.Chain(fo)
    for s in fno.UNIVERSE:
        chain.sym(s)
    print(f"data ready {time.time()-t0:.0f}s", flush=True)
    rows, sv = [], signal_variants()
    for i, (fam, sp) in enumerate(sv):
        sig = swing.signal(f, fam, sp)
        for tp in TRADE:
            p = {**sp, **tp}
            tr = swing.simulate(f, chain, fam, p, lots, budget=BUDGET, sig=sig)
            for grp in ("index", "stock"):
                g = [t for t in tr if swing.is_index(t["sym"]) == (grp == "index") and t["reason"] != "open"]
                a = swing.stats([t for t in g if t["entry_day"] < SPLIT])
                b = swing.stats([t for t in g if t["entry_day"] >= SPLIT])
                rows.append(dict(group=grp, family=fam, signal=label(fam, sp), **{k: tp[k] for k in tp},
                                 params=json.dumps(p), **{f"tr_{k}": v for k, v in a.items()},
                                 **{f"te_{k}": v for k, v in b.items()}))
        print(f"{i+1}/{len(sv)} {label(fam, sp)}  {time.time()-t0:.0f}s", flush=True)
    g = pd.DataFrame(rows)
    g.to_csv(OUT / "buy_grid.csv", index=False)
    print("saved", len(g))


if __name__ == "__main__":
    main()
