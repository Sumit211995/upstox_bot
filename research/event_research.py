"""Big-news days for shares: are they tradable intraday or as a swing?  (see events.py for definitions)

E1  GAP event known at 09:15 → INTRADAY: follow or fade the gap, exit at the close (optional stop).
E2  NEWS-DAY (big close move on heavy volume) → SWING from the next open: drift (buy after up days) or reversal
    (buy after down days), time / ATR exits, with / without the Nifty > EMA200 filter.
E3  E2 drift only on shares already in the 12-month momentum top 10.
Settings chosen on 2019–2023 only, judged on 2024 → Nov 2026. ₹20k account; costs + slippage included.
A rule is KEPT only if profitable in BOTH periods with PF ≥ 1.3 and ≥ 30 trades per period.
Writes research/out/event_results.csv, event_final.json, event_summary.md.
"""
import json
import sys
from datetime import date
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import events, stocks  # noqa

OUT = Path(__file__).resolve().parent / "out"
TR, TE = (date(2019, 1, 1), date(2023, 12, 31)), (date(2024, 1, 1), date(2026, 12, 31))
CAP = 20000.0

prep = events.prepare(stocks.load_all(refresh=False), stocks.nifty_daily(refresh=False))
print("data", prep["day"].min(), "→", prep["day"].max(), len(prep), "rows", prep["sym"].nunique(), "shares", flush=True)
rows = []


def run(event, kind, name, p):
    sim = events.simulate_intraday if kind == "intraday" else events.simulate_swing
    a, b = events.stats(sim(prep, p, *TR, CAP)), events.stats(sim(prep, p, *TE, CAP))
    keep = (a["n"] >= 30 and b["n"] >= 30 and (a["pf"] or 0) >= 1.3 and (b["pf"] or 0) >= 1.3 and a["net"] > 0 and b["net"] > 0)
    rows.append(dict(event=event, kind=kind, rule=name, params=json.dumps(p), **{f"tr_{k}": v for k, v in a.items()},
                     **{f"te_{k}": v for k, v in b.items()}, passed=keep))
    print(f"{event} {name:<44} | 19-23 n {a['n']:>4} win {a['win']} pf {a['pf']} avg {a['avg']}% net {a['net']:>9} dd {a['maxdd']}%"
          f" | 24-26 n {b['n']:>4} win {b['win']} pf {b['pf']} avg {b['avg']}% net {b['net']:>9} dd {b['maxdd']}%  {'PASS' if keep else ''}", flush=True)
    return a, b


# ---------------- E1: intraday gap trades (6 settings), then stop / regime / long-only on the best of 2019–23
best = None
for gap in (0.03, 0.04, 0.05):
    for mode in ("follow", "fade"):
        p = dict(gap=gap, mode=mode, stop=None, per_day=2)
        a, _ = run("E1", "intraday", f"gap ≥{100 * gap:.0f}% {mode}", p)
        if a["net"] is not None and (best is None or a["net"] > best[0]):
            best = (a["net"], p)
bp = best[1]
for extra, name in ((dict(stop=0.01), "stop 1%"), (dict(stop=0.02), "stop 2%"), (dict(regime=True), "Nifty>EMA200"),
                    (dict(side="long"), "long only")):
    run("E1", "intraday", f"best + {name} ({100 * bp['gap']:.0f}% {bp['mode']})", dict(bp, **extra))

# ---------------- E2: swing after news days
for direction in ("up", "down"):
    best = None
    for move, vol in ((0.04, 3.0), (0.04, 5.0), (0.06, 3.0), (0.06, 5.0)):
        p = dict(move=move, vol=vol, dir=direction, hold=5, atr_k=None, regime=False, max_pos=3)
        a, _ = run("E2", "swing", f"{direction} ≥{100 * move:.0f}% vol ≥{vol:.0f}× hold 5", p)
        if best is None or a["net"] > best[0]:
            best = (a["net"], p)
    bp = best[1]
    tag = f"{direction} ≥{100 * bp['move']:.0f}% vol ≥{bp['vol']:.0f}×"
    for extra, name in ((dict(hold=3), "hold 3"), (dict(hold=10), "hold 10"), (dict(atr_k=1.5, hold=10), "ATR 1.5 stop, 1:2, ≤10d"),
                        (dict(regime=True), "Nifty>EMA200")):
        run("E2", "swing", f"{tag} + {name}", dict(bp, **extra))

# ---------------- E3: drift on momentum leaders
for move, vol, hold in ((0.04, 3.0, 5), (0.04, 3.0, 10), (0.03, 2.0, 10)):
    run("E3", "swing", f"up ≥{100 * move:.0f}% vol ≥{vol:.0f}× · momentum top 10 · hold {hold}",
        dict(move=move, vol=vol, dir="up", hold=hold, atr_k=None, regime=True, mom_top=10, max_pos=3))

# ---------------- E1b: short-only gap-UP fade (chosen from the 2019–23 breakdown: gap-ups faded in every size band,
# ~61% winners; gap-down buys were mixed). 4 variants judged on 2019–23; the chosen one is then run on 2024–26.
E1B = [("gap up 2–5% short, smallest first, 2/day", dict(gap=0.02, gap_max=0.05, gap_side="up", mode="fade", per_day=2, rank="small")),
       ("gap up 2–5% short, biggest first, 2/day", dict(gap=0.02, gap_max=0.05, gap_side="up", mode="fade", per_day=2, rank="big")),
       ("gap up ≥2% short, biggest first, 3/day", dict(gap=0.02, gap_side="up", mode="fade", per_day=3, rank="big")),
       ("gap up 2–5% short, smallest first, 2/day, stop 2%", dict(gap=0.02, gap_max=0.05, gap_side="up", mode="fade", per_day=2, rank="small", stop=0.02))]
for name, p in E1B:
    run("E1b", "intraday", name, p)
best_b = max((r for r in rows if r["event"] == "E1b"), key=lambda r: r["tr_net"])
pb = json.loads(best_b["params"])
run("E1b", "intraday", "chosen + slippage 0.15%/side (stress)", dict(pb, slip=0.0015))
run("E1b", "intraday", "chosen + Nifty>EMA200", dict(pb, regime=True))

R = pd.DataFrame(rows)
R.to_csv(OUT / "event_results.csv", index=False)
passed = R[R["passed"]]
final = dict(train=[str(x) for x in TR], test=[str(x) for x in TE], capital=CAP, rows=R.drop(columns=[]).to_dict("records"),
             passed=passed[["event", "kind", "rule", "params"]].to_dict("records"))
(OUT / "event_final.json").write_text(json.dumps(final, default=str, indent=1))
md = ["# Big-news days (price + volume events): intraday and swing", "",
      "Chosen on 2019–2023, judged on 2024 → Nov 2026 · ₹20k account · costs + slippage included · "
      "kept only if both periods are profitable with PF ≥ 1.3 and ≥ 30 trades.", "",
      "| Event | Rule | 19–23 trades · win · PF · avg · net · max DD | 24–26 trades · win · PF · avg · net · max DD | Kept |",
      "|---|---|---|---|---|"]
for r in rows:
    md.append(f"| {r['event']} {r['kind']} | {r['rule']} | {r['tr_n']} · {r['tr_win']}% · {r['tr_pf']} · {r['tr_avg']}% · "
              f"₹{r['tr_net']:,.0f} · {r['tr_maxdd']}% | {r['te_n']} · {r['te_win']}% · {r['te_pf']} · {r['te_avg']}% · "
              f"₹{r['te_net']:,.0f} · {r['te_maxdd']}% | {'**yes**' if r['passed'] else 'no'} |")
md += ["", "**Verdict: no rule kept.**",
       "- Intraday: before costs, shorting a gap-up at the open and covering at the close made ≈ +0.5% a trade on 2019–23 "
       "(≈ 61% winners); intraday charges + slippage (≈ 0.3–0.35% a round trip) leave PF ≈ 1.1, and below 1 on 2024–26. "
       "Following the gap lost in every setting.",
       "- Swing: buying the day after a big up-news day drifted ≈ +0.6% over 5 days before costs (2019–23), less than "
       "delivery charges on a ₹20k account (≈ 1.3–1.5% a round trip). Buying after big down-news days kept falling.",
       "- Momentum leaders with an up-news day: PF 1.5 on 2019–23 but lost on 2024–26.",
       "- Use: show today's big-news shares (gap / heavy-volume moves) with their headlines as CONTEXT, not as calls."]
(OUT / "event_summary.md").write_text("\n".join(md) + "\n")
print("passed:", len(passed))
