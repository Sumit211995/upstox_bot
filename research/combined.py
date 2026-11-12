"""All good buying strategies feeding ONE small account (settings chosen on 2022–24 only)."""
import json, sys
from datetime import date
from pathlib import Path
import pandas as pd
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import fno, swing  # noqa
OUT = Path(__file__).resolve().parent / "out"; SPLIT = date(2025, 1, 1)
f = pd.read_pickle(OUT / "buy_features_tech.pkl")
fo = fno.load_all(); lots = fno.lots(); chain = swing.Chain(fo)
IDX = ("NIFTY", "BANKNIFTY")
MEMBERS = [  # priority order = train PF order
    dict(name="Index smart money", signal="smart", syms=IDX, params=dict(smart_thr=0.2, sl=0.5, tp=None, hold=5, offset=0, min_dte=25)),
    dict(name="Index 20-day breakout 1:2", signal="breakout", syms=IDX, params=dict(adx=0, sl=0.35, tp=0.7, hold=8, offset=1, min_dte=25)),
    dict(name="Index EMA cross 1:2", signal="emax", syms=IDX, params=dict(sl=0.25, tp=0.5, hold=3, offset=1, min_dte=25)),
    dict(name="Stock smart money", signal="smart", syms=fno.STOCKS, params=dict(smart_thr=0.2, sl=0.3, tp=None, hold=5, offset=1, min_dte=25)),
]
cands = []
for pr, m in enumerate(MEMBERS):
    tr = swing.simulate(f, chain, m["signal"], m["params"], lots, budget=20000, syms=m["syms"])
    for t in tr:
        t["strategy"] = m["name"]
        cands.append((pr, t))
    print(m["name"], len(tr))
res = {}
for cap, mp in ((20000, 1), (50000, 2), (100000, 4)):
    for per, (a, z) in {"train": (None, date(2024, 12, 31)), "test": (SPLIT, None)}.items():
        c = [(p, t) for p, t in cands if (a is None or t["signal_day"] >= a) and (z is None or t["signal_day"] <= z)]
        tk = swing.combine(c, cap, mp)
        closed = [t for t in tk if t["reason"] != "open"]
        st = swing.stats(closed); eq = [t["equity"] for t in tk]
        by = pd.Series([t["strategy"] for t in tk]).value_counts().to_dict()
        print(f"₹{cap//1000}k x{mp} {per}: n={st['n']} win={st['win']} pf={st['pf']} net={st['net']:.0f} end={eq[-1] if eq else cap} min={min(eq+[cap])}  {by}")
        res[f"{cap}_{per}"] = dict(st, end=eq[-1] if eq else cap, min=min(eq + [cap]), by=by)
DBG = swing.combine([(p, t) for p, t in cands if t["signal_day"] <= date(2024, 12, 31)], 20000, 1)
for t in sorted(DBG, key=lambda t: t["exit_day"])[:12]: print("  ", t["entry_day"], t["exit_day"], t["sym"], t["strategy"][:18], t["cost"], t["pnl"], t["equity"])
json.dump(dict(members=MEMBERS, results=res), open(OUT / "combined.json", "w"), default=str, indent=1)
