"""All buying strategies, one table: technical signals (RSI, EMA, MACD, ADX breakout, Supertrend, Bollinger squeeze),
open-interest and smart-money signals, alone and with a trend filter; exits incl. 1:2 risk-reward.
Real NSE option prices; settings judged on 2022–24 (train) and 2025–26 (test). Output research/out/tech_grid.csv"""
import itertools, sys, time
from datetime import date
from pathlib import Path
import pandas as pd
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import fno, swing  # noqa
OUT = Path(__file__).resolve().parent / "out"; SPLIT = date(2025, 1, 1)
f = pd.read_pickle(OUT / "buy_features_tech.pkl")
fo = fno.load_all(); lots = fno.lots(); chain = swing.Chain(fo)
for s in fno.UNIVERSE: chain.sym(s)
SIG = [("rsipb", dict(rsi_lo=lo)) for lo in (5, 10, 20)] + [("breakout", dict(adx=a)) for a in (0, 20, 25)] + \
      [(x, {}) for x in ("emax", "supertrend", "macd", "squeeze")] + \
      [(x + "+tf", {}) for x in ("emax", "supertrend", "macd", "squeeze")] + [("breakout+tf", dict(adx=20)), ("rsipb+tf", dict(rsi_lo=10))] + \
      [("oi", dict(mode="pct", thr=0.1, band=2)), ("oi+tf", dict(mode="pct", thr=0.1, band=2)), ("oi+rsipb", dict(mode="pct", thr=0.2, band=2, rsi_lo=20)),
       ("smart", dict(smart_thr=0.2)), ("smart+tf", dict(smart_thr=0.2)), ("breakout+oi", dict(adx=20, mode="pct", thr=0.3, band=2))]
EXITS = [(0.2, 0.4), (0.25, 0.5), (0.3, 0.6), (0.35, 0.7), (0.3, None), (0.5, None)]
GRID = [dict(sl=a, tp=b, hold=h, offset=o, min_dte=d) for (a, b), h, o, d in itertools.product(EXITS, (3, 5, 8), (0, 1), (15, 25))]
rows, t0 = [], time.time()
for i, (fam, sp) in enumerate(SIG):
    sig = swing.signal(f, fam, sp)
    for g in GRID:
        p = {**sp, **g}
        tr = [t for t in swing.simulate(f, chain, fam, p, lots, sig=sig) if t["reason"] != "open"]
        for grp in ("index", "stock"):
            x = [t for t in tr if swing.is_index(t["sym"]) == (grp == "index")]
            if grp == "index":
                x = [t for t in x if t["sym"] in ("NIFTY", "BANKNIFTY")]
            a = swing.stats([t for t in x if t["entry_day"] < SPLIT]); b = swing.stats([t for t in x if t["entry_day"] >= SPLIT])
            rows.append(dict(group=grp, family=fam, sp=str(sp), **g, rr="1:2" if g["tp"] else "no target",
                             **{f"tr_{k}": v for k, v in a.items()}, **{f"te_{k}": v for k, v in b.items()}))
    print(f"{i+1}/{len(SIG)} {fam} {sp} {time.time()-t0:.0f}s", flush=True)
pd.DataFrame(rows).to_csv(OUT / "tech_grid.csv", index=False)
print("saved")
