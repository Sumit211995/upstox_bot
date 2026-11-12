"""Does news help option buying? Daily tone of Indian market news (GDELT, free) — 2022, 2023, 2025 available.
Tone of day d is known before day d+1's open. Tested stand-alone and as a filter on the buying rules."""
import sys
from datetime import date
from pathlib import Path
import numpy as np, pandas as pd
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import fno, swing  # noqa
OUT = Path(__file__).resolve().parent / "out"; SPLIT = date(2025, 1, 1)
tone = pd.read_csv(OUT / "news_tone.csv"); tone["day"] = pd.to_datetime(tone["day"]).dt.date
tone = tone.sort_values("day")
tone["tone_chg"] = tone["tone"] - tone["tone"].rolling(5).mean().shift()
tone["tone_rk"] = tone["tone"].rolling(60, min_periods=30).rank(pct=True)
tone["chg_rk"] = tone["tone_chg"].rolling(60, min_periods=30).rank(pct=True)
f = pd.read_pickle(OUT / "buy_features.pkl").merge(pd.read_pickle(OUT / "participants.pkl"), on="day", how="left")
f = f.merge(tone, on="day", how="left").sort_values(["sym", "day"]).reset_index(drop=True)
have = f["tone"].notna()
fo = fno.load_all(); lots = fno.lots(); chain = swing.Chain(fo)
for n in (1, 3):
    f[f"fwd{n}"] = f.groupby("sym")["spot"].shift(-n) / f["spot"] - 1
IDX = f["sym"].isin(["NIFTY", "BANKNIFTY"]) & have
def sig(col, thr, contra=False):
    x = f[col]; s = np.where(x >= 1 - thr, 1, np.where(x <= thr, -1, 0)); s = np.where(have, s, 0); return -s if contra else s
print("1. direction: % right over 3 days (Nifty+BankNifty)")
for col in ("tone_rk", "chg_rk"):
    for contra in (False, True):
        s = sig(col, 0.15, contra); x = f[IDX].assign(s=s[IDX.values]); x = x[x.s != 0]
        for per, y in (("train", x[x.day < SPLIT]), ("test", x[x.day >= SPLIT])):
            print(f"   {col} {'fade' if contra else 'follow'} {per}: n={len(y)} right3={100*(np.sign(y.fwd3)==y.s).mean():.1f}% avg={1e4*(y.fwd3*y.s).mean():+.0f}bp")
def run(s, p):
    s = np.where(have, s, 0)
    tr = [t for t in swing.simulate(f, chain, "oi", p, lots, syms=("NIFTY", "BANKNIFTY"), sig=s) if t["reason"] != "open"]
    a = swing.stats([t for t in tr if t["entry_day"] < SPLIT]); b = swing.stats([t for t in tr if t["entry_day"] >= SPLIT])
    return f"train n={a['n']} pf={a['pf']} net={a['net']:.0f} | test n={b['n']} pf={b['pf']} net={b['net']:.0f}"
P = dict(sl=0.3, tp=None, hold=5, min_dte=15)
print("2. stand-alone buying:")
for col in ("tone_rk", "chg_rk"):
    for contra in (False, True):
        print(f"   {col} {'fade' if contra else 'follow'}:", run(sig(col, 0.15, contra), P))
print("3. as a filter (only days with news data):")
oi = swing.signal(f, "oi", dict(mode="pct", thr=0.1, band=2)).to_numpy()
parts = [1 - f["DII_opt_net_rk"], 1 - f["Client_fut_net_chg_rk"], f["DII_fut_net_chg_rk"], f["FII_opt_net_rk"], f["DII_fut_share_rk"]]
sm = sum(parts) / 5; smart = np.where(sm >= 0.8, 1, np.where(sm <= 0.2, -1, 0))
for name, base in (("OI-extreme", oi), ("smart money", smart)):
    print(f"   {name} alone:", run(base, P))
    t = f["tone_rk"].to_numpy()
    agree = np.where((base > 0) & (t < 0.2), 0, np.where((base < 0) & (t > 0.8), 0, base))      # skip if news strongly against
    print(f"   {name} skip-if-news-against:", run(agree, P))
    with_ = np.where((base > 0) & (t >= 0.5), base, np.where((base < 0) & (t <= 0.5), base, 0))
    print(f"   {name} only-if-news-agrees:", run(with_, P))
