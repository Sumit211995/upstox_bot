"""Honest version of the 'smart money' score: pick the components on 2022–24 ONLY (best train PF as stand-alone
buy signals in fii_research.csv), then judge on 2025–26."""
import sys
from datetime import date
from pathlib import Path
import numpy as np, pandas as pd
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import fno, swing  # noqa
OUT = Path(__file__).resolve().parent / "out"; SPLIT = date(2025, 1, 1)
R = pd.read_csv(OUT / "fii_research.csv")
alone = R[R.test.str.endswith(" alone") & ~R.test.str.startswith("OI")].copy()
alone = alone.sort_values("tr_pf", ascending=False).head(5)          # chosen on TRAIN only
print("components chosen on 2022–24:\n", alone[["test", "tr_pf", "te_pf"]].to_string(index=False))
f = pd.read_pickle(OUT / "buy_features.pkl").merge(pd.read_pickle(OUT / "participants.pkl"), on="day", how="left")
f = f.sort_values(["sym", "day"]).reset_index(drop=True)
parts = []
for t in alone.test:
    who, m = t.split()[0], t.split()[1]
    x = f[f"{who}_{m}"]
    parts.append(1 - x if "(opposite)" in t else x)
f["smart5"] = sum(parts) / len(parts)
fo = fno.load_all(); lots = fno.lots(); chain = swing.Chain(fo)
for thr in (0.1, 0.15, 0.2):
    s = np.where(f.smart5 >= 1 - thr, 1, np.where(f.smart5 <= thr, -1, 0))
    for hold in (5, 7):
        tr = [t for t in swing.simulate(f, chain, "oi", dict(sl=0.3, tp=None, hold=hold, min_dte=15), lots,
                                        syms=("NIFTY", "BANKNIFTY"), sig=s) if t["reason"] != "open"]
        a = swing.stats([t for t in tr if t["entry_day"] < SPLIT]); b = swing.stats([t for t in tr if t["entry_day"] >= SPLIT])
        print(f"thr {thr} hold {hold}: train n={a['n']} pf={a['pf']} net={a['net']:.0f} | TEST n={b['n']} pf={b['pf']} net={b['net']:.0f}")
        for spread, budget, sl in ((0, 20000, 0.3), (6, 12000, 0.5)):
            for per, (st, en) in {"train": (None, date(2024, 12, 31)), "test": (SPLIT, None)}.items():
                t2 = swing.simulate(f, chain, "oi", dict(sl=sl, tp=None, hold=hold, min_dte=15, spread=spread), lots,
                                    budget=budget, syms=("NIFTY", "BANKNIFTY"), sig=s, capital=20000, start=st, end=en)
                e = [t["equity"] for t in t2]
                print(f"      ₹20k {'naked' if not spread else 'spread6'} {per}: n={len(t2)} end={e[-1] if e else 20000:.0f} min={min(e + [20000]):.0f}")
