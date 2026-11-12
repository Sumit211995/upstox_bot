"""A real ₹20,000 account: one position at a time, Nifty + Bank Nifty, index OI-extreme signal.
Compares naked option buying (various per-trade budgets) with debit spreads. Output: research/out/buy_account.csv"""
import itertools, sys
from datetime import date
from pathlib import Path
import pandas as pd
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import fno, swing  # noqa
OUT = Path(__file__).resolve().parent / "out"
SPLIT = date(2025, 1, 1)
f = pd.read_pickle(OUT / "buy_features.pkl")
fo = fno.load_all(); fo = fo[fo["sym"].isin(fno.UNIVERSE)]
lots = fno.lots(); chain = swing.Chain(fo)
BASE = dict(mode="pct", thr=0.1, band=2, offset=0, sl=0.3, tp=None, hold=5, min_dte=15)
rows = []
for syms in (("NIFTY",), ("NIFTY", "BANKNIFTY")):
    for spread, budget, sl, offset in itertools.product((0, 2, 4, 6), (8000, 12000, 20000), (0.3, 0.5), (0, -1)):
        p = {**BASE, "spread": spread, "sl": sl, "offset": offset}
        for per, (a, b) in {"train": (None, date(2024, 12, 31)), "test": (SPLIT, None)}.items():
            tr = [t for t in swing.simulate(f, chain, "oi", p, lots, budget=budget, syms=syms, capital=20000, start=a, end=b)]
            st = swing.stats(tr)
            eqs = [t["equity"] for t in tr]
            rows.append(dict(syms="+".join(syms), spread=spread, budget=budget, sl=sl, offset=offset, period=per, **st,
                             end_equity=eqs[-1] if eqs else 20000, min_equity=min(eqs + [20000]),
                             avg_cost=round(sum(t["cost"] for t in tr) / len(tr)) if tr else None))
d = pd.DataFrame(rows); d.to_csv(OUT / "buy_account.csv", index=False)
w = d.pivot_table(index=["syms", "spread", "budget", "sl", "offset"], columns="period",
                  values=["n", "win", "pf", "end_equity", "min_equity", "avg_cost"]).round(2)
pd.set_option("display.width", 250); pd.set_option("display.max_rows", 200)
print(w.sort_values(("end_equity", "test"), ascending=False).to_string())
