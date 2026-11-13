"""Stock SWING trading research (cash shares, delivery): which end-of-day rules make money after real costs?
Choose on 2019–2023, judge untouched on 2024 → Nov 2026. Fresh ₹20,000 account per period (max 3 positions, whole
shares, next-open fills, 0.1% slippage, Upstox delivery charges). Benchmark: buy-and-hold Nifty 50.
Writes research/out/stock_grid.csv, stock_final.json, stock_summary.md."""
import json, sys, time
from datetime import date
from pathlib import Path
import numpy as np, pandas as pd
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import stocks  # noqa
OUT = Path(__file__).resolve().parent / "out"
TR, TE = (date(2019, 1, 1), date(2023, 12, 31)), (date(2024, 1, 1), date(2026, 12, 31))
t0 = time.time()
frames = stocks.load_all(refresh=False)
nifty = stocks.nifty_daily(refresh=False)
filt = stocks.market_filters(nifty, refresh=False)
prep = stocks.prepare(frames, filt)
print("prepared", len(frames), "symbols", round(time.time() - t0), "s", flush=True)


def bench(a, b):
    n = nifty[(nifty.day >= a) & (nifty.day <= b)].set_index("day")["close"]
    yrs = (n.index[-1] - n.index[0]).days / 365.25
    return dict(cagr=round(100 * ((n.iloc[-1] / n.iloc[0]) ** (1 / yrs) - 1), 1), maxdd=round(100 * float((n / n.cummax() - 1).min()), 1))


B = dict(train=bench(*TR), test=bench(*TE))
print("Nifty buy & hold", B, flush=True)
base = dict(max_pos=3, regime=False, vix_max=None, smart_min=None)
GRID = []
for thr in (5, 10):
    for ex in ("rsi70", "ema5"):
        for sa in (None, 3):
            GRID.append(dict(base, kind="pullback", rsi_thr=thr, exit=ex, stop_atr=sa, max_hold=10))
for n in (20, 55):
    for ex in ("trail", "rr"):
        for vm in (1.5, 0):
            GRID.append(dict(base, kind="breakout", n=n, exit=ex, atr_k=3 if ex == "trail" else 2, vol_mult=vm, max_hold=40))
for lb in (126, 252):
    for fq in (21, 10):
        for k in (3, 5):
            GRID.append(dict(base, kind="momentum", lookback=lb, freq=fq, max_pos=k, regime=True, max_hold=10 ** 9))


def label(p):
    if p["kind"] == "pullback":
        return f"pullback RSI2<{p['rsi_thr']} exit {p['exit']} stop {p['stop_atr'] or '-'}"
    if p["kind"] == "breakout":
        return f"breakout {p['n']}d {p['exit']} {p['atr_k']}ATR vol>{p['vol_mult'] or '-'}"
    return f"momentum {p['lookback']}d every {p['freq']}d top {p['max_pos']}"


def run(p, capital=20000.0):
    a, b = stocks.simulate(prep, p, *TR, capital), stocks.simulate(prep, p, *TE, capital)
    return stocks.stats(a), stocks.stats(b), a, b


rows = []
for i, p in enumerate(GRID):
    a, b, sa, sb = run(p)
    skips = 0
    rows.append(dict(family=p["kind"], setting=label(p), **{f"tr_{k}": v for k, v in a.items()}, **{f"te_{k}": v for k, v in b.items()}))
    print(f"{i + 1}/{len(GRID)} {label(p):<48} train n {a['n']} win {a['win']} pf {a['pf']} cagr {a['cagr']} dd {a['maxdd']} | "
          f"test n {b['n']} win {b['win']} pf {b['pf']} cagr {b['cagr']} dd {b['maxdd']}  {round(time.time() - t0)}s", flush=True)
G = pd.DataFrame(rows)
G["params"] = [json.dumps(p) for p in GRID]


def pick(g):
    ok = g[(g.tr_pf.fillna(0) >= 1.3) & (g.tr_n >= 30)]
    return (ok if len(ok) else g).sort_values("tr_cagr", ascending=False).iloc[0]


best = {fam: pick(G[G.family == fam]) for fam in ("pullback", "breakout", "momentum")}
for fam, r in best.items():
    print("BEST (train) ", fam, r.setting, "train cagr", r.tr_cagr, "pf", r.tr_pf, "| test cagr", r.te_cagr, "pf", r.te_pf, flush=True)
# filters on each family's train-best
frows = []
for fam, r in best.items():
    p0 = json.loads(r.params)
    for name, extra in (("none", {}), ("Nifty > EMA200", dict(regime=True)), ("VIX rank ≤ 80%", dict(vix_max=0.8)),
                        ("smart money ≥ 20%", dict(smart_min=0.2))):
        if fam == "momentum" and name == "Nifty > EMA200":
            continue
        p = dict(p0, **extra)
        a, b, _, _ = run(p)
        frows.append(dict(family=fam, setting=r.setting, filter=name, **{f"tr_{k}": v for k, v in a.items()},
                          **{f"te_{k}": v for k, v in b.items()}, params=json.dumps(p)))
        print("  filter", fam, name, "train", a, "| test", b, flush=True)
F = pd.DataFrame(frows)
G.to_csv(OUT / "stock_grid.csv", index=False); F.to_csv(OUT / "stock_filters.csv", index=False)
print("saved", round(time.time() - t0), "s")
