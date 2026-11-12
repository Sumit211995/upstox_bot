"""Does an India VIX filter improve the option-BUYING strategies? (the most common filter option buyers use)

Every past trade of each buy_books.json strategy is tagged with India VIX on its signal day: the level, its 60-day
percentile rank and its 5-day change. Filters are chosen on 2022–24 only and checked untouched on 2025–26.
A filter is worth keeping only if it raises PF in BOTH periods and keeps at least 60% of the trades.
Output: research/out/vix_research.csv
"""
import sys
from datetime import date, datetime
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import fno, market, swing, swing_bot  # noqa

OUT = Path(__file__).resolve().parents[1] / "research" / "out"
SPLIT = date(2025, 1, 1)


def vix_daily():
    path = market.DATA / "vix_daily.pkl"
    if path.exists():
        return pd.read_pickle(path)
    df = market.fetch_range(market.VIX_KEY, date(2021, 9, 1), datetime.now(market.IST).date(), unit="days", n=1)
    df["day"] = pd.to_datetime(df["time"], unit="s", utc=True).dt.tz_convert("Asia/Kolkata").dt.date
    df.to_pickle(path)
    return df


v = vix_daily().set_index("day")["close"]
vx = pd.DataFrame(dict(vix=v, vix_rank=v.rolling(60).apply(lambda s: (s <= s.iloc[-1]).mean(), raw=False),
                       vix_chg5=v.pct_change(5)))

f = pd.read_pickle(OUT / "buy_features_tech.pkl")
fo = fno.load_all(); lots = fno.lots(); chain = swing.Chain(fo)
strats, _ = swing_bot.load_books()

FILTERS = {
    "none": lambda t: True,
    "skip VIX rank > 80%": lambda t: not (t.vix_rank > 0.8),
    "skip VIX rank > 90%": lambda t: not (t.vix_rank > 0.9),
    "skip VIX rank < 20%": lambda t: not (t.vix_rank < 0.2),
    "skip VIX > 20": lambda t: not (t.vix > 20),
    "skip VIX 5d up > 15%": lambda t: not (t.vix_chg5 > 0.15),
    "only VIX rank < 50%": lambda t: t.vix_rank < 0.5,
}

rows = []
for st in strats:
    tr = [t for t in swing.simulate(f, chain, st["signal"], st["params"], lots, budget=20000, syms=st["syms"]) if t["reason"] != "open"]
    df = pd.DataFrame(tr)
    df = df.join(vx, on="signal_day")
    for name, keep in FILTERS.items():
        k = df[df.apply(keep, axis=1)]
        a = swing.stats(k[k.entry_day < SPLIT].to_dict("records"))
        b = swing.stats(k[k.entry_day >= SPLIT].to_dict("records"))
        rows.append(dict(strategy=st["id"], filter=name, tr_n=a["n"], tr_win=a["win"], tr_pf=a["pf"], tr_net=a["net"],
                         te_n=b["n"], te_win=b["win"], te_pf=b["pf"], te_net=b["net"]))
    print(st["id"], "done", flush=True)

r = pd.DataFrame(rows)
base = r[r["filter"] == "none"].set_index("strategy")
r["keeps_trades"] = r.apply(lambda x: x.tr_n >= 0.6 * base.loc[x.strategy, "tr_n"] and x.te_n >= 0.6 * base.loc[x.strategy, "te_n"], axis=1)
r["better_both"] = r.apply(lambda x: (x.tr_pf or 0) > (base.loc[x.strategy, "tr_pf"] or 0)
                           and (x.te_pf or 0) > (base.loc[x.strategy, "te_pf"] or 0), axis=1)
r.to_csv(OUT / "vix_research.csv", index=False)
pd.set_option("display.width", 200)
print(r.to_string(index=False))
