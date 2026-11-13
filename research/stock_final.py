"""Momentum (the only stock family that held up): filters, account sizes, yearly returns vs Nifty → stock_final.json
and stock_summary.md. Run after stock_research.py."""
import json, sys
from datetime import date
from pathlib import Path
import pandas as pd
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import stocks  # noqa
OUT = Path(__file__).resolve().parent / "out"
TR, TE, ALL = (date(2019, 1, 1), date(2023, 12, 31)), (date(2024, 1, 1), date(2026, 12, 31)), (date(2019, 1, 1), date(2026, 12, 31))
frames = stocks.load_all(refresh=False); nifty = stocks.nifty_daily(refresh=False)
prep = stocks.prepare(frames, stocks.market_filters(nifty, refresh=False))
base = dict(kind="momentum", lookback=252, freq=21, max_pos=3, regime=True, vix_max=None, smart_min=None, max_hold=10 ** 9,
            stop_pct=0.15)                                     # protective stop (research/stock_stops.py)
rows = []
def run(name, p, cap=20000.0):
    a, b = stocks.stats(stocks.simulate(prep, p, *TR, cap)), stocks.stats(stocks.simulate(prep, p, *TE, cap))
    rows.append(dict(test=name, capital=cap, **{f"tr_{k}": v for k, v in a.items()}, **{f"te_{k}": v for k, v in b.items()}))
    print(f"{name:<40} {cap:>8.0f} | train n {a['n']} win {a['win']} pf {a['pf']} cagr {a['cagr']} dd {a['maxdd']} | test n {b['n']} win {b['win']} pf {b['pf']} cagr {b['cagr']} dd {b['maxdd']}", flush=True)
run("momentum 252d monthly top3 (Nifty>EMA200)", base)
run("  no market filter", dict(base, regime=False))
run("  + VIX rank <= 80%", dict(base, vix_max=0.8))
run("  + smart money >= 20%", dict(base, smart_min=0.2))
run("  top 5", dict(base, max_pos=5))
run("momentum 252d monthly top3", base, 100000.0)
run("momentum 252d monthly top5", dict(base, max_pos=5), 100000.0)
pd.DataFrame(rows).to_csv(OUT / "stock_momentum.csv", index=False)
full = stocks.simulate(prep, base, *ALL, 20000.0)
yr = stocks.yearly(full)
n = nifty.set_index("day")["close"]; n.index = pd.to_datetime(n.index)
ny = n.groupby(n.index.year).last(); nprev = n.groupby(n.index.year).first()
nifty_y = {int(y): round(100 * (ny[y] / (ny[y - 1] if y - 1 in ny.index else nprev[y]) - 1), 1) for y in ny.index if y >= 2019}
print("yearly strategy", yr); print("yearly nifty   ", nifty_y)
t = pd.DataFrame([x for x in full["closed"] if x["reason"] != "open"])
print("most traded", t.sym.value_counts().head(8).to_dict())
json.dump(dict(params=base, rows=rows, yearly=yr, nifty_yearly=nifty_y, full=stocks.stats(full)), open(OUT / "stock_final.json", "w"), default=str, indent=1)
