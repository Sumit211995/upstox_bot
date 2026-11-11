"""Walk-forward research: every strategy family × index × settings.
Tune on 2022-01 → 2024-12 (train), judge the chosen settings only on 2025-01 → now (test).
Run: python research/run_research.py   (writes research/out/*.csv and research/out/summary.md)
"""
import itertools
import json
import sys
import time
from datetime import date
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import engine  # noqa: E402

SPLIT = date(2025, 1, 1)
OUT = Path(__file__).resolve().parent / "out"
INDEXES = ["NIFTY", "BANKNIFTY", "SENSEX", "MIDCPNIFTY"]


def grid(d):
    keys = list(d)
    return [dict(zip(keys, v)) for v in itertools.product(*d.values())]


FAMILIES = {
    "orb": grid(dict(orb=[15, 30], rr=[None, 2, 3], max_range=[0.006, 0.01, 0.015], itm=[0, 1])),
    "supertrend": grid(dict(tf=[5, 15], mult=[2.0, 3.0], ema=[None, 50], rr=[None, 3], itm=[1])),
    "pullback": grid(dict(tf=[15, 60], rr=[2, 3], atr_k=[1.0, 1.5], itm=[1])),
    "straddle": grid(dict(entry_hm=[920, 1000], sl=[0.4, 0.6, 0.8, 1.0], wing=[0, 6, 10],
                          days=["all", "expiry"], max_dte=[7])),
}


def split(trades):
    tr = [t for t in trades if pd.Timestamp(t["t0"], unit="s").date() < SPLIT]
    te = [t for t in trades if pd.Timestamp(t["t0"], unit="s").date() >= SPLIT]
    return tr, te


def score(st):
    """Rank settings on the TRAIN period: needs enough trades, then profit factor (net as tie-break)."""
    if st.get("n", 0) < 30 or st.get("pf") is None:
        return -1e9
    return st["pf"] * 1000 + st["net"] / 1e6


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    rows, best = [], {}
    t0 = time.time()
    for ix in INDEXES:
        df = engine.load(ix)
        for fam, params in FAMILIES.items():
            for p in params:
                if fam == "straddle":
                    trades = engine.simulate_straddle(df, ix, p)
                else:
                    trades = engine.simulate_buy(df, ix, engine.SIGNALS[fam](df, p), p)
                tr, te = split(trades)
                a, b = engine.stats(tr), engine.stats(te)
                rows.append(dict(index=ix, family=fam, params=json.dumps(p), **{f"train_{k}": v for k, v in a.items()},
                                 **{f"test_{k}": v for k, v in b.items()}))
                k = (ix, fam)
                if fam == "straddle" and not p["wing"]:
                    continue                                # unhedged selling is not allowed for the paper books
                if score(a) > best.get(k, (-1e18,))[0]:
                    best[k] = (score(a), p, a, b)
            print(f"{ix} {fam} done {time.time() - t0:.0f}s", flush=True)
    R = pd.DataFrame(rows)
    R.to_csv(OUT / "grid.csv", index=False)
    lines = ["| Index | Strategy | Best settings (chosen on 2022–24) | Train trades | Train win% | Train PF | Train net ₹ |"
             " Test trades | Test win% | Test PF | Test net ₹ | Test max DD ₹ |", "|" + "---|" * 12]
    for (ix, fam), (_, p, a, b) in sorted(best.items()):
        lines.append(f"| {ix} | {fam} | `{json.dumps(p)}` | {a.get('n')} | {a.get('win')} | {a.get('pf')} | {a.get('net')} |"
                     f" {b.get('n')} | {b.get('win')} | {b.get('pf')} | {b.get('net')} | {b.get('max_dd')} |")
    (OUT / "summary.md").write_text("\n".join(lines) + "\n")
    json.dump({f"{ix}|{fam}": dict(params=p, train=a, test=b) for (ix, fam), (_, p, a, b) in best.items()},
              open(OUT / "best.json", "w"), indent=1, default=float)
    print("\n".join(lines))
    # share of settings that were profitable in BOTH periods = how robust each family is
    R["both"] = (R["train_net"] > 0) & (R["test_net"] > 0)
    print("\nshare of settings profitable in train AND test:")
    print(R.groupby(["index", "family"])["both"].mean().unstack().round(2))


if __name__ == "__main__":
    main()
