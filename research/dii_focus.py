"""Stress-test participant-positioning signals (DII futures, FII options, fade-Client) and a combined score."""
import itertools, sys
from datetime import date
from pathlib import Path
import numpy as np, pandas as pd
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import fno, swing  # noqa
OUT = Path(__file__).resolve().parent / "out"; SPLIT = date(2025, 1, 1)
f = pd.read_pickle(OUT / "buy_features.pkl").merge(pd.read_pickle(OUT / "participants.pkl"), on="day", how="left")
f = f.sort_values(["sym", "day"]).reset_index(drop=True)
fo = fno.load_all(); lots = fno.lots(); chain = swing.Chain(fo)
f["smart"] = (f["DII_fut_share_rk"] + f["FII_opt_net_rk"] + (1 - f["Client_opt_net_rk"])) / 3
f["smart_rk"] = f.groupby("sym")["smart"].transform(lambda s: s.rolling(60, min_periods=30).rank(pct=True))
def sig(col, thr):
    x = f[col]; return np.where(x >= 1 - thr, 1, np.where(x <= thr, -1, 0))
def run(s, p, syms=("NIFTY", "BANKNIFTY")):
    tr = [t for t in swing.simulate(f, chain, "oi", p, lots, syms=syms, sig=s) if t["reason"] != "open"]
    return tr, swing.stats([t for t in tr if t["entry_day"] < SPLIT]), swing.stats([t for t in tr if t["entry_day"] >= SPLIT])
rows = []
for col, thr, sl, hold, dte in itertools.product(("DII_fut_share_rk", "smart", "smart_rk", "FII_opt_net_rk"), (0.1, 0.15, 0.2, 0.3),
                                                (0.3, 0.5), (3, 5, 7), (10, 15)):
    _, a, b = run(sig(col, thr), dict(offset=0, sl=sl, tp=None, hold=hold, min_dte=dte))
    rows.append(dict(col=col, thr=thr, sl=sl, hold=hold, dte=dte, tr_n=a["n"], tr_pf=a["pf"], tr_net=a["net"], te_n=b["n"], te_pf=b["pf"], te_net=b["net"]))
G = pd.DataFrame(rows); G["both"] = (G.tr_net > 0) & (G.te_net > 0); G.to_csv(OUT / "dii_focus_grid.csv", index=False)
pd.set_option("display.width", 220)
print(G.groupby("col").agg(settings=("both", "size"), both=("both", "mean"), tr_pf=("tr_pf", "median"), te_pf=("te_pf", "median")).round(2))
print(G.groupby(["col", "thr"]).agg(both=("both", "mean"), tr_pf=("tr_pf", "median"), te_pf=("te_pf", "median")).round(2))
print(G.groupby(["col", "hold"]).agg(both=("both", "mean"), te_pf=("te_pf", "median")).round(2).unstack())
for col in ("DII_fut_share_rk", "smart"):
    tr, a, b = run(sig(col, 0.15), dict(offset=0, sl=0.3, tp=None, hold=5, min_dte=15))
    print("\n", col, "train", a, "\n test", b)
    for s_ in ("NIFTY", "BANKNIFTY"): print("  ", s_, swing.stats([t for t in tr if t["sym"] == s_]))
    for y in range(2022, 2027): print("  ", y, swing.stats([t for t in tr if t["entry_day"].year == y]))
    print("   CE", swing.stats([t for t in tr if t["kind"] == "CE"]), "\n   PE", swing.stats([t for t in tr if t["kind"] == "PE"]))
    # ₹20k accounts: spread vs naked
    for spread, budget, slx in ((6, 12000, 0.5), (4, 8000, 0.5), (0, 20000, 0.3)):
        for per, (st, en) in {"train": (None, date(2024, 12, 31)), "test": (SPLIT, None)}.items():
            t2 = swing.simulate(f, chain, "oi", dict(offset=0, sl=slx, tp=None, hold=5, min_dte=15, spread=spread), lots,
                                budget=budget, syms=("NIFTY", "BANKNIFTY"), sig=sig(col, 0.15), capital=20000, start=st, end=en)
            e = [t["equity"] for t in t2]
            print(f"   ₹20k spread{spread} {per}: n={len(t2)} pf={swing.stats(t2)['pf']} end={e[-1] if e else 20000} min={min(e+[20000])}")
