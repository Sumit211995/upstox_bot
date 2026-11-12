"""Start a small account in EVERY month (Jan 2022 → Nov 2025), run it 12 months: how often does it get hurt?"""
import sys
from datetime import date
from pathlib import Path
import numpy as np, pandas as pd
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
src = open(Path(__file__).with_name("combined.py")).read().split("res = {}")[0]
exec(src)
def run(cands, cap, mp, months=12, label=""):
    out = []
    for y in range(2022, 2026):
        for m in range(1, 13):
            s = date(y, m, 1)
            e = date(y + (m + months - 1) // 12, (m + months - 1) % 12 + 1, 1)
            if s > date(2025, 11, 1):
                break
            c = [(p, t) for p, t in cands if s <= t["signal_day"] < e]
            tk = swing.combine(c, cap, mp)
            eqs = [cap] + [t["equity"] for t in sorted(tk, key=lambda t: t["exit_day"])]
            out.append(dict(start=s, end=eqs[-1], low=min(eqs), n=len(tk)))
    d = pd.DataFrame(out)
    print(f"{label} ₹{cap//1000}k x{mp}: {len(d)} starts · ended in profit {100*(d.end>cap).mean():.0f}% · median end ₹{d.end.median():,.0f} · "
          f"worst end ₹{d.end.min():,.0f} · fell below half {100*(d.low<cap/2).mean():.0f}% · median lowest ₹{d.low.median():,.0f}")
    return d
for keep, lab in ((None, "ALL 4"), ({"Index smart money", "Index 20-day breakout 1:2"}, "smart+breakout"),
                  ({"Index smart money", "Index 20-day breakout 1:2", "Index EMA cross 1:2"}, "index only")):
    cc = [(p, t) for p, t in cands if keep is None or t["strategy"] in keep]
    for cap, mp in ((20000, 1), (50000, 2)):
        run(cc, cap, mp, label=lab)
