"""Does a protective stop loss help the stock momentum account? (users want an SL on every call)
Stop = entry reference × (1 − x), checked on each day's low; after a stop the slot stays in cash until the next
monthly decision. Chosen on 2019–23, judged on 2024–26, ₹20k account, same costs as stock_final.py."""
import sys
from datetime import date
from pathlib import Path
import pandas as pd
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import stocks  # noqa
OUT = Path(__file__).resolve().parent / "out"
TR, TE = (date(2019, 1, 1), date(2023, 12, 31)), (date(2024, 1, 1), date(2026, 12, 31))
prep = stocks.prepare(stocks.load_all(refresh=False), stocks.market_filters(stocks.nifty_daily(refresh=False), refresh=False))
base = dict(stocks.FINAL)
rows = []
for x in (None, 0.10, 0.15, 0.20, 0.25):
    p = dict(base, stop_pct=x)
    a, b = stocks.stats(stocks.simulate(prep, p, *TR, 20000.0)), stocks.stats(stocks.simulate(prep, p, *TE, 20000.0))
    rows.append(dict(stop=x, **{f"tr_{k}": v for k, v in a.items()}, **{f"te_{k}": v for k, v in b.items()}))
    print(f"stop {x}: train n {a['n']} win {a['win']} pf {a['pf']} cagr {a['cagr']} dd {a['maxdd']} | test n {b['n']} win {b['win']} pf {b['pf']} cagr {b['cagr']} dd {b['maxdd']}", flush=True)
pd.DataFrame(rows).to_csv(OUT / "stock_stops.csv", index=False)
