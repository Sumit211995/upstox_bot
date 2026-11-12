"""Final option-BUYING results for buy_books.json (every strategy + the combined accounts) → research/out/buy_history.json
and buy_summary.md, read by the dashboard. Needs research/out/buy_features_tech.pkl (see tech_research.py)."""
import json, sys
from datetime import date
from pathlib import Path
import numpy as np, pandas as pd
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import fno, swing, swing_bot  # noqa
HERE = Path(__file__).resolve().parents[1]; OUT = HERE / "research" / "out"; SPLIT = date(2025, 1, 1)
f = swing.add_vix(pd.read_pickle(OUT / "buy_features_tech.pkl"))
fo = fno.load_all(); lots = fno.lots(); chain = swing.Chain(fo)
strats, accounts = swing_bot.load_books()
iso = lambda t: {k: (v.isoformat() if isinstance(v, date) else v) for k, v in t.items()}
def years(tr):
    y = {}
    for t in tr:
        d = y.setdefault(str(t["entry_day"].year), dict(n=0, w=0, pnl=0.0)); d["n"] += 1; d["w"] += t["pnl"] > 0; d["pnl"] = round(d["pnl"] + t["pnl"])
    return y
out = dict(split=SPLIT.isoformat(), strategies={}, accounts={})
md = ["# Option buying: every strategy + combined accounts (real NSE option prices)", "",
      "| Strategy | Trades 22–24 | Win % | PF | Trades 25–26 | **Win %** | **PF** | Net 25–26 |", "|---|---|---|---|---|---|---|---|"]
for st in strats:
    tr = [t for t in swing.simulate(f, chain, st["signal"], st["params"], lots, budget=20000, syms=st["syms"]) if t["reason"] != "open"]
    a, b = swing.stats([t for t in tr if t["entry_day"] < SPLIT]), swing.stats([t for t in tr if t["entry_day"] >= SPLIT])
    out["strategies"][st["id"]] = dict(train=a, test=b, years=years(tr), trades=[iso(t) for t in tr][::-1][:600])
    md.append(f"| {st['label']} | {a['n']} | {a['win']} | {a['pf']} | {b['n']} | {b['win']} | {b['pf']} | {b['net']:,.0f} |")
md += ["", "| Account | Period | Trades | Win % | PF | Start → end | Lowest |", "|---|---|---|---|---|---|---|"]
by = {s["id"]: s for s in strats}
for ac in accounts:
    cands = []
    for mid in ac["members"]:
        st = by[mid]
        for t in swing.simulate(f, chain, st["signal"], swing_bot.member_params(st, ac), lots, budget=ac["budget"], syms=st["syms"]):
            t["strategy"], t["strategy_label"] = st["id"], st["label"]; cands.append((st["priority"], t))
    res = {}
    for per, (a, z) in {"train": (None, date(2024, 12, 31)), "test": (SPLIT, None), "all": (None, None)}.items():
        tk = swing.combine([(p, t) for p, t in cands if (a is None or t["signal_day"] >= a) and (z is None or t["signal_day"] <= z)], ac["capital"], ac["max_pos"])
        s = swing.stats([t for t in tk if t["reason"] != "open"]); eq = [ac["capital"]] + [t["equity"] for t in sorted(tk, key=lambda t: t["exit_day"])]
        s.update(end=eq[-1], low=min(eq)); res[per] = s
        if per == "all":
            res["years"] = years([t for t in tk if t["reason"] != "open"]); res["trades"] = [iso(t) for t in tk][::-1]
        md.append(f"| {ac['label']} | {per} | {s['n']} | {s['win']} | {s['pf']} | ₹{ac['capital']:,} → ₹{s['end']:,.0f} | ₹{s['low']:,.0f} |")
    runs = []                                                   # start in every month, run 12 months
    for y in range(2022, 2026):
        for m in range(1, 13):
            s0 = date(y, m, 1); e0 = date(y + (m + 11) // 12, (m + 11) % 12 + 1, 1)
            if s0 > date(2025, 11, 1): break
            tk = swing.combine([(p, t) for p, t in cands if s0 <= t["signal_day"] < e0], ac["capital"], ac["max_pos"])
            eq = [ac["capital"]] + [t["equity"] for t in sorted(tk, key=lambda t: t["exit_day"])]
            runs.append(dict(end=eq[-1], low=min(eq)))
    r = pd.DataFrame(runs)
    res["rolling"] = dict(starts=len(r), profit_pct=round(100 * (r.end > ac["capital"]).mean()), median_end=round(r.end.median()),
                          worst_end=round(r.end.min()), below_half_pct=round(100 * (r.low < ac["capital"] / 2).mean()))
    out["accounts"][ac["id"]] = res
    md.append(f"| {ac['label']} | start any month, 12 months | {res['rolling']['starts']} runs | profit in {res['rolling']['profit_pct']}% | | median end ₹{res['rolling']['median_end']:,} | fell below half {res['rolling']['below_half_pct']}% |")
g = pd.read_csv(OUT / "tech_grid.csv"); g["both"] = (g.tr_net > 0) & (g.te_net > 0)
out["families"] = {grp: [dict(family=k[0], sp=k[1], settings=int(len(x)), both=round(100 * x.both.mean()), tr_pf=x.tr_pf.median(),
                              te_pf=x.te_pf.median(), te_win=x.te_win.median())
                         for k, x in g[g.group == grp].groupby(["family", "sp"])] for grp in ("index", "stock")}
fr = pd.read_csv(OUT / "fii_research.csv")
out["participants"] = [dict(test=r.test, tr_pf=r.tr_pf, te_pf=r.te_pf, tr_n=int(r.tr_n), te_n=int(r.te_n)) for r in fr.itertuples() if r.test.endswith(" alone")]
out["news"] = dict(standalone=dict(tr_pf=1.48, te_pf=1.75, right=54.4), filter_oi=dict(alone=1.44, skip_against=1.13))
(OUT / "buy_history.json").write_text(json.dumps(out, default=lambda x: None if isinstance(x, float) and np.isnan(x) else str(x)))
(OUT / "buy_summary.md").write_text("\n".join(md) + "\n")
print("\n".join(md))
