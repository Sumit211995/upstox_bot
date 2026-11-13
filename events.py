"""Big-news days for shares, detected from price + volume (paper research / signals only).

Free per-company news archives going back years do not exist (GDELT keeps ~3 months, NSE's announcement API is not
used), but a big-news day almost always shows up in the price and volume: results, big orders, deals, regulatory news.
Two kinds of event:

* GAP event (known at 09:15): the share OPENS ≥ x% away from yesterday's close  → tested as an INTRADAY trade
  (follow the gap or fade it, exit at the close; optional stop on the day's high / low, stop assumed first).
* NEWS-DAY event (known at the close): the share CLOSES ≥ x% up / down on ≥ k× its 20-day average volume → tested as a
  SWING trade bought at the next open (post-news drift or reversal), with time / ATR exits.

Public:
    prepare(frames, nifty)                         -> long DataFrame of per-day features (no look-ahead)
    detect(prep, day=None)                         -> list of events on `day` (default: last day) for the dashboard
    intraday_costs(buy_value, sell_value)          -> ₹ charges for one intraday round trip
    swing_costs(value, side)                       -> ₹ charges for one delivery order (= stocks.costs)
    simulate_intraday(prep, p, start, end, capital) / simulate_swing(prep, p, start, end, capital)
    stats(result)                                  -> trades / win / PF / avg / net / max drawdown / per year
    calls(prep, day=None)                          -> today's calls of the rules that PASSED the research (may be [])
"""
import math
from datetime import date

import numpy as np
import pandas as pd

import stocks

# ------------------------------------------------------------------ costs
SLIP_INTRA = 0.0007              # 0.07% each side (large caps at the open / close)
SLIP_SWING = stocks.SLIP         # 0.1% each side
MAX_GAP = 0.20                   # gaps above 20% are treated as data problems (unadjusted corporate actions)


def intraday_costs(buy_value, sell_value):
    """Upstox intraday equity charges for one round trip (buy + sell, either order). Slippage is separate."""
    c = 0.0
    for v, side in ((buy_value, "buy"), (sell_value, "sell")):
        if v <= 0:
            continue
        brk = min(20.0, 0.0005 * v)                      # ₹20 or 0.05% per order, whichever is lower
        ex, sebi = stocks.EXCH * v, stocks.SEBI * v
        c += brk + ex + sebi + stocks.GST * (brk + ex + sebi)
        c += 0.00025 * v if side == "sell" else 0.00003 * v   # STT 0.025% on sell · stamp 0.003% on buy
    return round(c, 2)


def swing_costs(value, side):
    return stocks.costs(value, side)


# ------------------------------------------------------------------ features
def prepare(frames, nifty=None):
    """frames: {sym: daily candles}. Returns one DataFrame (sym, day, OHLCV, prev_close, gap, ret, vol_mult, atr,
    ema200, mom252, nifty_up, mom_rank). Every column on row `day` uses only data known by that day's open (gap) or
    close (everything else)."""
    rows = []
    for s, df in frames.items():
        d = stocks.indicators(df)
        d["sym"] = s
        d["prev_close"] = d["close"].shift(1)
        d["gap"] = d["open"] / d["prev_close"] - 1
        d["ret"] = d["close"] / d["prev_close"] - 1
        d["vol_mult"] = d["volume"] / d["vol20"]
        rows.append(d[["sym", "day", "open", "high", "low", "close", "volume", "prev_close", "gap", "ret", "vol_mult",
                       "atr", "ema200", "mom252"]])
    p = pd.concat(rows, ignore_index=True)
    if nifty is not None:
        n = stocks.indicators(nifty)
        up = pd.DataFrame({"day": n["day"], "nifty_up": (n["close"] > n["ema200"]) & n["ema200"].notna()})
        p = p.merge(up, on="day", how="left")
    else:
        p["nifty_up"] = True
    p["nifty_up"] = p["nifty_up"].fillna(False).astype(bool)
    ok = p["mom252"].notna() & p["ema200"].notna() & (p["close"] > p["ema200"])
    p["mom_rank"] = np.nan
    p.loc[ok, "mom_rank"] = p[ok].groupby("day")["mom252"].rank(ascending=False, method="first")
    p = p[p["prev_close"].notna()].sort_values(["day", "sym"]).reset_index(drop=True)
    return p


def detect(prep, day=None, gap_thr=0.03, move_thr=0.04, vol_thr=3.0):
    """Big-news events on `day`: gap events (open vs previous close) and news days (close move on heavy volume)."""
    day = day or prep["day"].max()
    t = prep[prep["day"] == day]
    out = []
    for r in t.itertuples(index=False):
        if abs(r.gap) >= gap_thr and abs(r.gap) <= MAX_GAP:
            out.append(dict(sym=r.sym, day=day, type="gap", move=round(100 * r.gap, 2), vol_mult=_r(r.vol_mult),
                            direction="up" if r.gap > 0 else "down", close=round(r.close, 2)))
        if abs(r.ret) >= move_thr and (r.vol_mult or 0) >= vol_thr and abs(r.ret) <= MAX_GAP:
            out.append(dict(sym=r.sym, day=day, type="news-day", move=round(100 * r.ret, 2), vol_mult=_r(r.vol_mult),
                            direction="up" if r.ret > 0 else "down", close=round(r.close, 2)))
    out.sort(key=lambda x: -abs(x["move"]))
    return out


def _r(x):
    return None if x is None or (isinstance(x, float) and math.isnan(x)) else round(float(x), 1)


# ------------------------------------------------------------------ intraday simulator (gap events)
def simulate_intraday(prep, p, start, end, capital=20000.0):
    """p: gap (≥ |gap|), gap_max, gap_side 'up' | 'down' | None, mode 'follow' | 'fade', stop (fraction or None),
    per_day (max trades a day), rank 'big' | 'small' (which gaps first), regime (Nifty > EMA200 the previous day —
    known at the open), side 'both' | 'long', slip (per side).
    Entry at the open, exit at the close; a stop is checked on the day's high / low and assumed hit first."""
    t = prep[(prep["day"] >= start) & (prep["day"] <= end)]
    t = t[(t["gap"].abs() >= p["gap"]) & (t["gap"].abs() <= min(p.get("gap_max") or MAX_GAP, MAX_GAP))]
    if p.get("gap_side") == "up":
        t = t[t["gap"] > 0]
    elif p.get("gap_side") == "down":
        t = t[t["gap"] < 0]
    if p.get("regime"):
        prev_up = prep.drop_duplicates("day")[["day", "nifty_up"]].copy()
        prev_up["day_next"] = prev_up["day"].shift(-1)
        t = t.merge(prev_up[["day_next", "nifty_up"]].rename(columns={"day_next": "day", "nifty_up": "nup_prev"}),
                    on="day", how="left")
        t = t[t["nup_prev"].fillna(False).astype(bool)]
    slip = p.get("slip", SLIP_INTRA)
    eq, trades, curve = capital, [], []
    for day, g in t.groupby("day", sort=True):
        g = g.reindex(g["gap"].abs().sort_values(ascending=p.get("rank") == "small").index).head(p.get("per_day", 2))
        slot = eq / p.get("per_day", 2)
        pnl_day = 0.0
        for r in g.itertuples(index=False):
            d = 1 if r.gap > 0 else -1
            if p["mode"] == "fade":
                d = -d
            if p.get("side") == "long" and d < 0:
                continue
            entry = r.open * (1 + d * slip)
            sh = int(slot // entry)
            if sh <= 0:
                continue
            exit_px, why = r.close, "close"
            s = p.get("stop")
            if s:
                stop_px = entry * (1 - d * s)
                if (d > 0 and r.low <= stop_px) or (d < 0 and r.high >= stop_px):
                    exit_px, why = stop_px, "stop"
            exit_px *= (1 - d * slip)
            buy_v, sell_v = (entry * sh, exit_px * sh) if d > 0 else (exit_px * sh, entry * sh)
            ch = intraday_costs(buy_v, sell_v)
            pnl = d * (exit_px - entry) * sh - ch
            pnl_day += pnl
            trades.append(dict(sym=r.sym, day=day, side="long" if d > 0 else "short", gap=round(100 * r.gap, 2),
                               entry=round(entry, 2), exit=round(exit_px, 2), shares=sh, charges=ch, pnl=round(pnl, 2),
                               ret=round(pnl / (entry * sh), 5), reason=why))
        eq += pnl_day
        curve.append((day, eq))
    return dict(trades=trades, curve=curve, capital=capital, start=start, end=end)


# ------------------------------------------------------------------ swing simulator (news days)
def simulate_swing(prep, p, start, end, capital=20000.0):
    """p: move (≥ |close move|), vol (≥ volume multiple), dir 'up' (buy after big up days = drift) | 'down' (buy after
    big down days = reversal), hold (trading days, exit at that day's close), atr_k (stop k×ATR, target 2k×ATR, or
    None), regime (Nifty > EMA200 on the signal day), mom_top (only shares ranked ≤ N by 12-month momentum),
    max_pos. Long only (delivery). Signal at day t's close → buy at t+1's open."""
    days = sorted(prep.loc[(prep["day"] >= start) & (prep["day"] <= end), "day"].unique())
    by_day = {d: g for d, g in prep[prep["day"].isin(days)].groupby("day", sort=False)}
    bars = {(r.sym, r.day): r for r in prep[prep["day"].isin(days)].itertuples(index=False)}
    cash, pos, pending, trades, curve = capital, [], [], [], []
    mp = p.get("max_pos", 3)
    for day in days:
        # 1) buys decided yesterday, at today's open
        for o in pending:
            if len(pos) >= mp or any(x["sym"] == o["sym"] for x in pos):
                continue
            r = bars.get((o["sym"], day))
            if r is None:
                continue
            eq_now = cash + sum(x["shares"] * x["last"] for x in pos)
            px = r.open * (1 + SLIP_SWING)
            sh = int(min(cash, eq_now / mp) // (px * 1.0012))
            if sh <= 0:
                continue
            cost = sh * px + swing_costs(sh * px, "buy")
            if cost > cash:
                continue
            cash -= cost
            gapf = px / o["ref"]
            pos.append(dict(sym=o["sym"], signal_day=o["day"], entry_day=day, entry=px, shares=sh, cost=cost, n=0,
                            last=r.close, stop=o["stop"] * gapf if o["stop"] else None,
                            target=o["target"] * gapf if o["target"] else None, move=o["move"]))
        pending = []
        # 2) manage open positions on today's bar (stop first), time exit at the close
        for x in list(pos):
            r = bars.get((x["sym"], day))
            if r is None:
                continue
            x["n"] += 1
            exit_px, why = None, None
            if x["stop"] and r.low <= x["stop"]:
                exit_px, why = min(r.open, x["stop"]) if x["n"] > 1 else x["stop"], "stop"
            elif x["target"] and r.high >= x["target"]:
                exit_px, why = max(r.open, x["target"]) if x["n"] > 1 else x["target"], "target"
            elif x["n"] >= p["hold"]:
                exit_px, why = r.close, "time"
            x["last"] = r.close
            if exit_px is not None:
                px = exit_px * (1 - SLIP_SWING)
                gross = px * x["shares"]
                net = gross - swing_costs(gross, "sell")
                cash += net
                pnl = net - x["cost"]
                trades.append(dict(sym=x["sym"], signal_day=x["signal_day"], entry_day=x["entry_day"], exit_day=day,
                                   entry=round(x["entry"], 2), exit=round(px, 2), shares=x["shares"], pnl=round(pnl, 2),
                                   ret=round(pnl / x["cost"], 5), days=x["n"], reason=why, move=x["move"]))
                pos.remove(x)
        # 3) today's news days → buy at the next open
        g = by_day.get(day)
        if g is not None and len(pos) < mp:
            sig = g[(g["vol_mult"] >= p["vol"]) & (g["ret"].abs() <= MAX_GAP)]
            sig = sig[sig["ret"] >= p["move"]] if p["dir"] == "up" else sig[sig["ret"] <= -p["move"]]
            if p.get("regime"):
                sig = sig[sig["nifty_up"]]
            if p.get("mom_top"):
                sig = sig[sig["mom_rank"] <= p["mom_top"]]
            held = {x["sym"] for x in pos}
            for r in sig.sort_values("vol_mult", ascending=False).itertuples(index=False):
                if r.sym in held:
                    continue
                k = p.get("atr_k")
                stop = r.close - k * r.atr if k else None
                target = r.close + 2 * k * r.atr if k else None
                pending.append(dict(sym=r.sym, day=day, ref=r.close, stop=stop, target=target, move=round(100 * r.ret, 2)))
        curve.append((day, cash + sum(x["shares"] * x["last"] for x in pos)))
    return dict(trades=trades, curve=curve, capital=capital, start=start, end=end)


# ------------------------------------------------------------------ statistics
def stats(res):
    t = pd.DataFrame(res["trades"])
    cap = res["capital"]
    if t.empty:
        return dict(n=0, win=None, pf=None, avg=None, net=0.0, maxdd=None, cagr=None, years={})
    pnl = t["pnl"]
    gw, gl = pnl[pnl > 0].sum(), -pnl[pnl < 0].sum()
    eq = pd.Series([v for _, v in res["curve"]], index=pd.to_datetime([d for d, _ in res["curve"]]))
    peak = eq.cummax()
    dd = float(((eq - peak) / peak).min()) if len(eq) else 0.0
    yrs = max((eq.index[-1] - eq.index[0]).days / 365.25, 0.1) if len(eq) else 1
    end = float(eq.iloc[-1]) if len(eq) else cap
    cagr = (end / cap) ** (1 / yrs) - 1 if end > 0 else -1.0
    day_col = "day" if "day" in t else "exit_day"
    years = t.groupby(pd.to_datetime(t[day_col]).dt.year)["pnl"].sum().round(0).to_dict()
    return dict(n=int(len(t)), win=round(100 * float((pnl > 0).mean()), 1), pf=round(float(gw / gl), 2) if gl else None,
                avg=round(100 * float(t["ret"].mean()), 3), net=round(float(pnl.sum()), 0), maxdd=round(100 * dd, 1),
                cagr=round(100 * cagr, 1), end=round(end, 0), years={int(k): v for k, v in years.items()})


# ------------------------------------------------------------------ live calls (rules that passed the research)
PASSED = []          # filled from research/event_research.py results: list of (kind, params, label, horizon)


def calls(prep, day=None):
    """Calls for the rules that passed (see PASSED). Each: sym, type, side, entry, stop, target, time, why."""
    day = day or prep["day"].max()
    out = []
    t = prep[prep["day"] == day]
    for kind, p, label, horizon in PASSED:
        if kind == "swing":
            sig = t[(t["vol_mult"] >= p["vol"]) & (t["ret"].abs() <= MAX_GAP)]
            sig = sig[sig["ret"] >= p["move"]] if p["dir"] == "up" else sig[sig["ret"] <= -p["move"]]
            if p.get("regime"):
                sig = sig[sig["nifty_up"]]
            if p.get("mom_top"):
                sig = sig[sig["mom_rank"] <= p["mom_top"]]
            for r in sig.sort_values("vol_mult", ascending=False).itertuples(index=False):
                k = p.get("atr_k")
                out.append(dict(sym=r.sym, type="news-day", rule=label, side="BUY", entry="next 09:15 open",
                                ref=round(r.close, 2), stop=round(r.close - k * r.atr, 2) if k else None,
                                target=round(r.close + 2 * k * r.atr, 2) if k else None, time=horizon,
                                why=f"closed {100 * r.ret:+.1f}% on {r.vol_mult:.1f}× its 20-day volume"))
    return out
