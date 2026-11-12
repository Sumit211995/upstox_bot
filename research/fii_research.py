"""FII / DII / Pro / Client positions (NSE participant-wise OI) as option-BUYING signals and as filters on the
OI-extreme rule. Real NSE option prices, train 2022–24 / test 2025–26. Output: research/out/fii_research.csv"""
import itertools, sys
from datetime import date
from pathlib import Path
import numpy as np, pandas as pd
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import fno, swing  # noqa

OUT = Path(__file__).resolve().parent / "out"
SPLIT = date(2025, 1, 1)
f = pd.read_pickle(OUT / "buy_features.pkl")
fo = fno.load_all(); lots = fno.lots(); chain = swing.Chain(fo)
days = sorted(f["day"].unique())

# --- participant table: every participant, index futures long share + index option net positions
rows = []
for d in days:
    p = fno.participant(d)
    if p is None:
        continue
    r = dict(day=d)
    for who in ("FII", "DII", "Pro", "Client"):
        x = p.loc[who]
        L, S = x.fut_idx_long, x.fut_idx_short
        r[f"{who}_fut_share"] = L / (L + S) if L + S else np.nan
        r[f"{who}_fut_net"] = L - S
        r[f"{who}_opt_net"] = (x.opt_idx_call_long - x.opt_idx_call_short) - (x.opt_idx_put_long - x.opt_idx_put_short)
        r[f"{who}_stk_share"] = x.fut_stk_long / (x.fut_stk_long + x.fut_stk_short)
    rows.append(r)
P = pd.DataFrame(rows).sort_values("day").reset_index(drop=True)
base = [c for c in P.columns if c != "day"]
for c in base:
    P[c + "_chg"] = P[c].diff()
for c in base + [c + "_chg" for c in base]:
    P[c + "_rk"] = P[c].rolling(60, min_periods=30).rank(pct=True)
P.to_pickle(OUT / "participants.pkl")
f = f.drop(columns=[c for c in f.columns if c.startswith(("FII_", "DII_", "Pro_", "Client_"))]).merge(P, on="day", how="left")
f = f.sort_values(["sym", "day"]).reset_index(drop=True)
for n in (1, 3, 5):
    f[f"fwd{n}"] = f.groupby("sym")["spot"].shift(-n) / f["spot"] - 1
IDX = f["sym"].isin(["NIFTY", "BANKNIFTY"])

def pf_sig(col, thr, contra=False):
    """+1 when the participant's measure is in its top `thr` of 60 days (they are most long), -1 bottom."""
    x = f[col]
    s = np.where(x >= 1 - thr, 1, np.where(x <= thr, -1, 0))
    return -s if contra else s

print("=== 1. Does the participant measure predict the index? (Nifty+BankNifty, % of days right, avg move bp)")
diag = []
for who, m in itertools.product(("FII", "DII", "Pro", "Client"), ("fut_share_rk", "fut_net_chg_rk", "opt_net_rk", "opt_net_chg_rk")):
    s = pf_sig(f"{who}_{m}", 0.15)
    x = f[IDX].assign(s=s[IDX.values])
    x = x[x.s != 0]
    for per, y in (("train", x[x.day < SPLIT]), ("test", x[x.day >= SPLIT])):
        d = dict(who=who, measure=m, period=per, n=len(y))
        for n in (1, 3, 5):
            d[f"right{n}"] = round(100 * (np.sign(y[f"fwd{n}"]) == y.s).mean(), 1)
            d[f"bp{n}"] = round(1e4 * (y[f"fwd{n}"] * y.s).mean(), 0)
        diag.append(d)
D = pd.DataFrame(diag)
W = D.pivot_table(index=["who", "measure"], columns="period", values=["right3", "bp3", "n"])
pd.set_option("display.width", 220)
print(W.round(1).to_string())

print("\n=== 2. As option-buying signals (ATM, stop 30%, hold 5, expiry ≥15d) and 3. as filters on OI-extreme")
BASE = dict(mode="pct", thr=0.1, band=2, offset=0, sl=0.3, tp=None, hold=5, min_dte=15)
res = []
def run(sig, p=BASE, syms=("NIFTY", "BANKNIFTY")):
    tr = [t for t in swing.simulate(f, chain, "oi", p, lots, syms=syms, sig=sig) if t["reason"] != "open"]
    a = swing.stats([t for t in tr if t["entry_day"] < SPLIT]); b = swing.stats([t for t in tr if t["entry_day"] >= SPLIT])
    return a, b
base_sig = swing.signal(f, "oi", BASE).to_numpy()
a, b = run(base_sig)
res.append(dict(test="OI-extreme alone (current book)", tr_n=a["n"], tr_pf=a["pf"], tr_net=a["net"], te_n=b["n"], te_pf=b["pf"], te_net=b["net"]))
for who, m, contra in itertools.product(("FII", "DII", "Pro", "Client"), ("fut_share_rk", "fut_net_chg_rk", "opt_net_rk"), (False, True)):
    s = pf_sig(f"{who}_{m}", 0.15, contra)
    a, b = run(s)
    res.append(dict(test=f"{who} {m}{' (opposite)' if contra else ''} alone", tr_n=a["n"], tr_pf=a["pf"], tr_net=a["net"], te_n=b["n"], te_pf=b["pf"], te_net=b["net"]))
    # filter: keep OI-extreme trades only when the participant measure is NOT strongly against them
    x = f[f"{who}_{m}"].to_numpy()
    agree = (np.sign(x - 0.5) * (-1 if contra else 1))
    keep = np.where((base_sig != 0) & (agree * base_sig >= 0), base_sig, 0)
    a, b = run(keep)
    res.append(dict(test=f"OI-extreme + {who} {m}{' (opposite)' if contra else ''} agrees", tr_n=a["n"], tr_pf=a["pf"], tr_net=a["net"], te_n=b["n"], te_pf=b["pf"], te_net=b["net"]))
R = pd.DataFrame(res); R.to_csv(OUT / "fii_research.csv", index=False)
print(R.to_string(index=False))
