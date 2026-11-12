"""₹20k account with SMALLER trades: per-trade budget caps (cheaper strikes) and index debit spreads."""
import sys
from datetime import date
from pathlib import Path
import pandas as pd
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import fno, swing  # noqa
_r = open(Path(__file__).with_name("rolling_start.py")).read()
exec("import numpy as np\n" + _r[_r.index("def run("):_r.index("for keep, lab in")])
OUT = Path(__file__).resolve().parent / "out"; SPLIT = date(2025, 1, 1)
f = pd.read_pickle(OUT / "buy_features_tech.pkl"); fo = fno.load_all(); lots = fno.lots(); chain = swing.Chain(fo)
IDX = ("NIFTY", "BANKNIFTY")
def members(budget, spread):
    sp = dict(spread=spread) if spread else {}
    return [dict(name="Index smart money", signal="smart", syms=IDX, budget=budget, params=dict(smart_thr=0.2, sl=0.5, tp=None, hold=5, offset=0, min_dte=25, **sp)),
            dict(name="Index 20-day breakout 1:2", signal="breakout", syms=IDX, budget=budget, params=dict(adx=0, sl=0.35, tp=0.7, hold=8, offset=0 if spread else 1, min_dte=25, **sp)),
            dict(name="Index EMA cross 1:2", signal="emax", syms=IDX, budget=budget, params=dict(sl=0.25, tp=0.5, hold=3, offset=0 if spread else 1, min_dte=25, **sp)),
            dict(name="Stock smart money", signal="smart", syms=fno.STOCKS, budget=budget, params=dict(smart_thr=0.2, sl=0.3, tp=None, hold=5, offset=0, min_dte=25))]
for budget, spread, mp in ((10000, 0, 2), (7000, 0, 2), (10000, 4, 2), (7000, 4, 2), (7000, 6, 2), (10000, 6, 2), (6500, 4, 3)):
    cands = []
    for pr, m in enumerate(members(budget, spread)):
        for t in swing.simulate(f, chain, m["signal"], m["params"], lots, budget=m["budget"], syms=m["syms"]):
            t["strategy"] = m["name"]; cands.append((pr, t))
    tr = swing.combine([c for c in cands if c[1]["signal_day"] < SPLIT], 20000, mp)
    te = swing.combine([c for c in cands if c[1]["signal_day"] >= SPLIT], 20000, mp)
    a, b = swing.stats([t for t in tr if t["reason"] != "open"]), swing.stats([t for t in te if t["reason"] != "open"])
    print(f"budget {budget} spread {spread} slots {mp}: train n={a['n']} win={a['win']} pf={a['pf']} end={tr[-1]['equity'] if tr else 0:.0f} | test n={b['n']} win={b['win']} pf={b['pf']}")
    run(cands, 20000, mp, label=f"   rolling budget {budget} spread {spread}")
