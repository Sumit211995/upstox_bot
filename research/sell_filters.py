"""Can the straddle-SELLING books be improved? Day filters from the previous evening's data (smart-money score,
ADX trend strength, India VIX change, gap risk): skip days that look like trend days. Train 2022–24 / test 2025–26."""
import json, sys
from datetime import date, datetime
from pathlib import Path
import numpy as np, pandas as pd
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import engine, market, swing  # noqa
OUT = Path(__file__).resolve().parent / "out"; SPLIT = date(2025, 1, 1)
F = pd.read_pickle(OUT / "buy_features_tech.pkl")
books = json.load(open(Path(__file__).resolve().parents[1] / "books.json"))
rows = []
for b in books:
    ix = b["index"]
    df = engine.load(ix)
    tr = engine.simulate_straddle(df, ix, b["params"])
    t = pd.DataFrame([dict(day=datetime.fromtimestamp(x["t0"], engine.IST).date(), pnl=x["pnl"]) for x in tr])
    fx = F[F.sym == ("NIFTY" if ix == "SENSEX" else ix)][["day", "smart", "adx", "rsi14", "bb_w_rk"]].copy()
    fx["prev"] = fx["day"]; fx = fx.sort_values("day")
    nxt = dict(zip(fx["day"], fx["day"].shift(-1)))
    fx["day"] = fx["day"].map(nxt)                                   # yesterday's evening data → today's trade
    vix = engine.load("NIFTY").groupby("day")["vix"].last()
    t = t.merge(fx, on="day", how="left")
    t["vix_chg"] = t["day"].map(lambda d: vix.get(d)) / t["day"].map(lambda d: vix.shift().get(d)) - 1
    vd = market.vix_daily()                                          # India VIX at YESTERDAY's close (known before entry)
    vr = swing.vix_rank(vd); vc = vd.set_index("day")["close"]
    prev_of = dict(zip(vc.index[1:], vc.index[:-1]))
    t["vix_prev"] = t["day"].map(lambda d: vc.get(prev_of.get(d)))
    t["vix_rank_prev"] = t["day"].map(lambda d: vr.get(prev_of.get(d)))
    filters = {"none": t.index == t.index,
               "skip VIX rank < 20% (cheap premium)": ~(t.vix_rank_prev < 0.2),
               "skip VIX rank > 80%": ~(t.vix_rank_prev > 0.8),
               "skip VIX > 20": ~(t.vix_prev > 20),
               "skip VIX < 12": ~(t.vix_prev < 12),
               "skip smart extreme (≤20/≥80)": ~((t.smart >= 0.8) | (t.smart <= 0.2)),
               "skip ADX > 30": ~(t.adx > 30),
               "skip ADX > 25": ~(t.adx > 25),
               "skip squeeze (narrow bands)": ~(t.bb_w_rk <= 0.2),
               "skip RSI extreme (<30/>70)": ~((t.rsi14 < 30) | (t.rsi14 > 70)),
               "only smart neutral (35–65)": (t.smart > 0.35) & (t.smart < 0.65)}
    for name, m in filters.items():
        m = m.fillna(True) if hasattr(m, "fillna") else m
        x = t[m]
        for per, y in (("train", x[x.day < SPLIT]), ("test", x[x.day >= SPLIT])):
            p = y.pnl.to_numpy()
            gl = -p[p < 0].sum()
            rows.append(dict(book=b["id"], filter=name, period=per, n=len(p), win=round(100 * (p > 0).mean(), 1) if len(p) else None,
                             pf=round(p[p > 0].sum() / gl, 2) if gl else None, net=round(p.sum())))
    print(b["id"], "done", flush=True)
R = pd.DataFrame(rows); R.to_csv(OUT / "sell_filters.csv", index=False)
W = R.pivot_table(index=["book", "filter"], columns="period", values=["n", "win", "pf", "net"])
pd.set_option("display.width", 250); pd.set_option("display.max_rows", 100)
print(W.to_string())
