"""Strategy rules + trade simulator shared by the research backtests, the strategy history and the paper bot.

Every strategy produces SIGNALS on the index (time, direction, stop and target as index levels). The simulator
walks the 5-minute index candles to find the exit, then values the trade as a real OPTION position:
premium in / out from the calibrated model (options.py), real lot size, Indian charges and slippage.
"""
import json
import math
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import numpy as np
import pandas as pd

import market
import options

HERE = Path(__file__).resolve().parent
IST = timezone(timedelta(hours=5, minutes=30))
EXPIRY_FILE = HERE / "research" / "expiries.json"
SLIP_PCT, SLIP_MIN = 0.005, 0.5         # slippage per option order: 0.5% of premium, at least ₹0.5


# ------------------------------------------------------------------ data
def load(index, n=5):
    """5-minute index candles + India VIX, with IST day / clock columns."""
    df = market.history(market.INDICES[index]["key"], n)
    vix = market.history(market.VIX_KEY, n)[["time", "close"]].rename(columns={"close": "vix"})
    return prepare(df, vix)


def prepare(df, vix):
    df = df.merge(vix, on="time", how="left").sort_values("time").reset_index(drop=True)
    df["vix"] = df["vix"].ffill().bfill().fillna(15.0)
    t = pd.to_datetime(df["time"], unit="s", utc=True).dt.tz_convert("Asia/Kolkata")
    df["day"] = t.dt.date
    df["hm"] = t.dt.hour * 100 + t.dt.minute
    return df[(df["hm"] >= 915) & (df["hm"] <= 1525)].reset_index(drop=True)


def bars(df5, minutes):
    """Higher timeframe bars built from the 5-minute frame (bar time = start; `end` = time it closes)."""
    out = market.resample(df5[["time", "open", "high", "low", "close"]], minutes)
    per = 86400 if minutes == "D" else minutes * 60
    out["end"] = out["time"] + per
    if minutes == "D":                                    # a daily bar is complete at 15:30 IST
        d = pd.to_datetime(out["time"], unit="s", utc=True).dt.tz_convert("Asia/Kolkata").dt.normalize()
        out["end"] = (d + pd.Timedelta(hours=15, minutes=30)).astype("int64") // 10**9
    else:                                                 # the last bar of the day closes at 15:30 at the latest
        d = pd.to_datetime(out["time"], unit="s", utc=True).dt.tz_convert("Asia/Kolkata").dt.normalize()
        close = (d + pd.Timedelta(hours=15, minutes=30)).astype("int64") // 10**9
        out["end"] = np.minimum(out["end"], close)
    return out


# ------------------------------------------------------------------ expiry calendar
def _sensex_rule(start, end):
    """Sensex weekly expiry weekday by period (BSE): Fri until 2024, Tue Jan–Aug 2025, Thu from Sep 2025."""
    out, d = [], max(start, date(2023, 5, 15))
    while d <= end:
        wd = 4 if d < date(2025, 1, 1) else 1 if d < date(2025, 9, 1) else 3
        if d.weekday() == wd:
            out.append(d)
        d += timedelta(days=1)
    return out


def expiries(index):
    """Sorted expiry dates (date objects) for an index: history from NSE files + live contract list."""
    cal = json.loads(EXPIRY_FILE.read_text()) if EXPIRY_FILE.exists() else {}
    days = {date.fromisoformat(x) for x in cal.get(index, [])}
    if index == "SENSEX":
        days |= set(_sensex_rule(date(2023, 5, 15), date.today() + timedelta(days=60)))
    try:
        for e in market.instruments().get(index, {}).get("expiries", []):
            days.add(datetime.fromtimestamp(e, IST).date())
    except Exception:
        pass
    return sorted(days)


def build_expiry_file():
    """Collect real expiry dates from the cached NSE bhavcopy files (research helper)."""
    import glob
    cal = {}
    for f in sorted(glob.glob(str(market.DATA / "bhav" / "*.pkl"))):
        d = pd.read_pickle(f)
        for s, e in d[["symbol", "expiry"]].drop_duplicates().itertuples(index=False):
            if s in ("NIFTY", "BANKNIFTY", "MIDCPNIFTY"):
                cal.setdefault(s, set()).add(e.isoformat())
    EXPIRY_FILE.parent.mkdir(exist_ok=True)
    EXPIRY_FILE.write_text(json.dumps({k: sorted(v) for k, v in cal.items()}, indent=0))


def expiry_ts(d):
    return int(datetime(d.year, d.month, d.day, 15, 30, tzinfo=IST).timestamp())


def choose_expiry(exps, day, min_days=0):
    """Nearest expiry on/after `day` + min_days (calendar days)."""
    target = day + timedelta(days=min_days)
    for e in exps:
        if e >= target:
            return e
    return None


# ------------------------------------------------------------------ indicators
def ema(s, n):
    return s.ewm(span=n, adjust=False).mean()


def rsi(s, n=14):
    d = s.diff()
    up = d.clip(lower=0).ewm(alpha=1 / n, adjust=False).mean()
    dn = (-d.clip(upper=0)).ewm(alpha=1 / n, adjust=False).mean()
    return 100 - 100 / (1 + up / dn.replace(0, np.nan))


def atr(df, n=14):
    pc = df["close"].shift()
    tr = pd.concat([df["high"] - df["low"], (df["high"] - pc).abs(), (df["low"] - pc).abs()], axis=1).max(axis=1)
    return tr.ewm(alpha=1 / n, adjust=False).mean()


def supertrend(df, n=10, mult=3.0):
    a = atr(df, n).to_numpy()
    hl2 = ((df["high"] + df["low"]) / 2).to_numpy()
    close = df["close"].to_numpy()
    ub, lb = hl2 + mult * a, hl2 - mult * a
    st, dirn = np.full(len(df), np.nan), np.ones(len(df))
    fub, flb = ub.copy(), lb.copy()
    for i in range(1, len(df)):
        fub[i] = ub[i] if ub[i] < fub[i - 1] or close[i - 1] > fub[i - 1] else fub[i - 1]
        flb[i] = lb[i] if lb[i] > flb[i - 1] or close[i - 1] < flb[i - 1] else flb[i - 1]
        if dirn[i - 1] == 1:
            dirn[i] = -1 if close[i] < flb[i] else 1
        else:
            dirn[i] = 1 if close[i] > fub[i] else -1
        st[i] = flb[i] if dirn[i] == 1 else fub[i]
    return pd.Series(st, index=df.index), pd.Series(dirn, index=df.index)


# ------------------------------------------------------------------ strategies → signals
# A signal: dict(t=entry time (epoch s, trade opens at the first 5m candle starting at/after t), side=+1 CE / -1 PE,
#                sl=index stop, tp=index target or None, exit_hm=forced exit clock (e.g. 1515), why=text)

def sig_orb(df5, p):
    """Opening Range Breakout: range of the first `orb` minutes; trade the first close beyond it."""
    out = []
    first_hm = 915 + (p["orb"] // 60) * 100 + p["orb"] % 60
    for day, g in df5.groupby("day", sort=True):
        rng = g[g["hm"] < first_hm]
        if len(rng) < p["orb"] // 5:
            continue
        hi, lo = rng["high"].max(), rng["low"].min()
        width = (hi - lo) / lo
        if width > p.get("max_range", 0.012) or width < p.get("min_range", 0.0):
            continue
        rest = g[(g["hm"] >= first_hm) & (g["hm"] <= p.get("last_entry", 1300))]
        for r in rest.itertuples():
            side = 1 if r.close > hi else -1 if r.close < lo else 0
            if not side:
                continue
            entry = r.close
            sl = (lo if side > 0 else hi) if p.get("sl", "range") == "range" else (hi + lo) / 2
            risk = abs(entry - sl)
            if risk <= 0:
                break
            tp = entry + side * p["rr"] * risk if p.get("rr") else None
            out.append(dict(t=int(r.time) + 300, side=side, sl=sl, tp=tp, exit_hm=p.get("exit_hm", 1515),
                            why=f"ORB{p['orb']} {'up' if side > 0 else 'down'} break {hi:.0f}/{lo:.0f}"))
            break
    return out


def sig_pullback(df5, p):
    """delta_bot's pullback rule on Indian index bars: EMA50/200 trend + RSI dip-and-recover, SL = k×ATR."""
    b = bars(df5, p["tf"])
    c = b["close"]
    b["e_f"], b["e_s"], b["rsi"], b["atr"] = ema(c, 50), ema(c, 200), rsi(c), atr(b)
    lo_r, hi_r = p.get("rsi_lo", 40), 100 - p.get("rsi_lo", 40)
    out = []
    pr = b["rsi"].shift()
    up = (b["e_f"] > b["e_s"]) & (c > b["e_s"]) & (pr < lo_r) & (b["rsi"] >= lo_r)
    dn = (b["e_f"] < b["e_s"]) & (c < b["e_s"]) & (pr > hi_r) & (b["rsi"] <= hi_r)
    for i in np.flatnonzero((up | dn).to_numpy())[1:]:
        r = b.iloc[i]
        hm = int(pd.Timestamp(int(r["end"]), unit="s", tz="UTC").tz_convert("Asia/Kolkata").strftime("%H%M"))
        if p.get("intraday", True) and hm > p.get("last_entry", 1430):
            continue
        side = 1 if up.iloc[i] else -1
        risk = p.get("atr_k", 1.5) * r["atr"]
        out.append(dict(t=int(r["end"]), side=side, sl=r["close"] - side * risk,
                        tp=r["close"] + side * p.get("rr", 2) * risk if p.get("rr") else None,
                        exit_hm=1515 if p.get("intraday", True) else None,
                        why=f"pullback {p['tf']}m RSI {r['rsi']:.0f}"))
    return out


def sig_supertrend(df5, p):
    """Supertrend flip on `tf`-minute bars; stop = the supertrend line (trails), exit by 15:15."""
    b = bars(df5, p["tf"])
    st, d = supertrend(b, p.get("n", 10), p.get("mult", 3.0))
    b["st"], b["dir"] = st, d
    flips = np.flatnonzero((b["dir"] != b["dir"].shift()).to_numpy())
    out = []
    for i in flips[1:]:
        r = b.iloc[i]
        hm = int(pd.Timestamp(int(r["end"]), unit="s", tz="UTC").tz_convert("Asia/Kolkata").strftime("%H%M"))
        if hm < p.get("first_entry", 945) or hm > p.get("last_entry", 1400):
            continue
        side = int(r["dir"])
        if p.get("ema"):                                  # optional trend filter
            e = ema(b["close"], p["ema"]).iloc[i]
            if (side > 0) != (r["close"] > e):
                continue
        risk = abs(r["close"] - r["st"])
        out.append(dict(t=int(r["end"]), side=side, sl=r["st"],
                        tp=r["close"] + side * p["rr"] * risk if p.get("rr") else None, exit_hm=1515,
                        trail=("st", p["tf"], p.get("n", 10), p.get("mult", 3.0)),
                        why=f"supertrend {p['tf']}m flip"))
    return out


SIGNALS = {"orb": sig_orb, "pullback": sig_pullback, "supertrend": sig_supertrend}


# ------------------------------------------------------------------ simulator: directional option BUYING
def _slip(px):
    return max(SLIP_MIN, px * SLIP_PCT)


def simulate_buy(df5, index, signals, p, lots=1, live=False):
    """Turn index signals into option trades (buy CE for up, PE for down); one open trade at a time.
    live=True: a trade whose exit has not happened yet (data ends) is returned with result="open", valued at the last candle."""
    info = market.INDICES[index]
    lot, step, exch = info["lot"] * lots, info["step"], info["exchange"]
    exps = expiries(index)
    T = df5["time"].to_numpy()
    H, L, O, C, V = (df5[k].to_numpy() for k in ("high", "low", "open", "close", "vix"))
    HM = df5["hm"].to_numpy()
    D = df5["day"].to_numpy()
    trail_cache = {}
    trades, busy_until = [], 0
    for s in sorted(signals, key=lambda x: x["t"]):
        if s["t"] < busy_until:
            continue
        i = int(np.searchsorted(T, s["t"]))
        if i >= len(T):
            break
        day = D[i]
        if s.get("exit_hm") and HM[i] > s["exit_hm"] - 5:
            continue
        exp = choose_expiry(exps, day, p.get("min_dte", 1 if s.get("exit_hm") else 3))
        if exp is None:
            continue
        side, kind = s["side"], ("CE" if s["side"] > 0 else "PE")
        spot0 = O[i]
        sl, tp = s["sl"], s["tp"]
        if (spot0 - sl) * side <= 0:                       # gapped through the stop before entry
            continue
        strike = options.pick_strike(spot0, step, kind, p.get("itm", 1))
        ets = expiry_ts(exp)
        prem0 = options.premium(index, spot0, strike, kind, ets, T[i], V[i])
        if prem0 < 1:
            continue
        trail = None
        if s.get("trail"):
            key = s["trail"]
            if key not in trail_cache:
                b = bars(df5, key[1])
                st, _ = supertrend(b, key[2], key[3])
                idx = np.searchsorted(b["end"].to_numpy(), T, side="right") - 1
                trail_cache[key] = np.where(idx >= 0, st.to_numpy()[np.clip(idx, 0, None)], np.nan)
            trail = trail_cache[key]
        j, exit_spot, why = i, None, "time"
        open_now = False
        for j in range(i, len(T)):
            if trail is not None and j > i and not math.isnan(trail[j]):
                sl = max(sl, trail[j]) if side > 0 else min(sl, trail[j])
            lo_hit = L[j] <= sl if side > 0 else H[j] >= sl
            tp_hit = tp is not None and (H[j] >= tp if side > 0 else L[j] <= tp)
            if lo_hit:                                    # stop first when both touch (pessimistic)
                exit_spot = min(O[j], sl) if side > 0 else max(O[j], sl)
                why = "stop" if s["sl"] == sl else "trail"
                break
            if tp_hit:
                exit_spot, why = tp, "target"
                break
            if s.get("exit_hm") and (HM[j] >= s["exit_hm"] or D[j] != day):
                exit_spot, why = (O[j] if D[j] == day else C[j - 1]), "time"
                break
            if not s.get("exit_hm") and (D[j] >= exp or T[j] >= ets - 15 * 60):
                exit_spot, why = O[j], "expiry"
                break
        else:
            if not live:
                continue
            j, exit_spot, why, open_now = len(T) - 1, C[-1], "open", True
        prem1 = options.premium(index, exit_spot, strike, kind, ets, T[j], V[j])
        buy_px, sell_px = prem0 + _slip(prem0), max(0.05, prem1 - _slip(prem1))
        cost = options.charges(buy_px * lot, "buy", exch) + options.charges(sell_px * lot, "sell", exch)
        pnl = (sell_px - buy_px) * lot - cost
        risk_pts = abs(spot0 - s["sl"])
        trades.append(dict(index=index, side="BUY CE" if side > 0 else "BUY PE", kind=kind, strike=strike,
                           expiry=exp.isoformat(), t0=int(T[i]), t1=int(T[j]), spot0=float(spot0),
                           spot1=float(exit_spot), sl=float(s["sl"]), tp=float(tp) if tp else None,
                           prem0=round(buy_px, 2), prem1=round(sell_px, 2), lot=lot, charges=round(cost, 2),
                           pnl=round(pnl, 2), pts=round((exit_spot - spot0) * side, 2),
                           r=round((exit_spot - spot0) * side / risk_pts, 2) if risk_pts else 0,
                           result=why, why=s.get("why", ""), vix=round(float(V[i]), 2), sl_now=float(sl)))
        busy_until = 2**62 if open_now else int(T[j]) + 1
    return trades


# ------------------------------------------------------------------ simulator: option SELLING (hedged)
def simulate_straddle(df5, index, p, lots=1, live=False):
    """Sell ATM CE + PE at `entry_hm`, buy wings `wing` strikes away (hedge), stop each short leg at +sl% premium,
    close everything at `exit_hm`. Runs on each day (optionally only expiry days)."""
    info = market.INDICES[index]
    lot, step, exch = info["lot"] * lots, info["step"], info["exchange"]
    exps = expiries(index)
    trades = []
    for day, g in df5.groupby("day", sort=True):
        exp = choose_expiry(exps, day, 0)
        if exp is None:
            continue
        dte = (exp - day).days
        if p.get("days") == "expiry" and dte != 0:
            continue
        if p.get("days") == "nonexpiry" and dte == 0:
            continue
        if dte > p.get("max_dte", 7):
            continue
        e = g[g["hm"] >= p.get("entry_hm", 920)]
        if len(e) < 3:
            continue
        ets = expiry_ts(exp)
        r0 = e.iloc[0]
        spot0, t0, vix0 = r0["open"], int(r0["time"]), r0["vix"]
        k = options.atm_strike(spot0, step)
        w = p.get("wing", 0) * step
        legs = [("CE", k, -1), ("PE", k, -1)]
        if w:
            legs += [("CE", k + w, +1), ("PE", k - w, +1)]
        entry = {(kd, kk): options.premium(index, spot0, kk, kd, ets, t0, vix0) for kd, kk, _ in legs}
        if min(entry[("CE", k)], entry[("PE", k)]) < 2:
            continue
        stopped, exit_px = {}, {}
        last = e[e["hm"] < p.get("exit_hm", 1515)]          # close at 15:15 = end of the 15:10 candle
        for r in last.itertuples():
            for kd in ("CE", "PE"):
                if kd in stopped:
                    continue
                worst = r.high if kd == "CE" else r.low       # short CE hurts when index rises, PE when it falls
                px = options.premium(index, worst, k, kd, ets, int(r.time), r.vix)
                if px >= entry[(kd, k)] * (1 + p["sl"]):
                    stopped[kd] = int(r.time)
                    exit_px[(kd, k)] = px
                    if w:                                     # close that side's hedge too
                        hk = k + w if kd == "CE" else k - w
                        exit_px[(kd, hk)] = options.premium(index, worst, hk, kd, ets, int(r.time), r.vix)
            if len(stopped) == 2:
                break
        rl = last.iloc[-1]
        spot1, t1 = rl["close"], int(rl["time"]) + 300
        xm = p.get("exit_hm", 1515) // 100 * 60 + p.get("exit_hm", 1515) % 100 - 5      # last candle before the exit
        still_open = live and day == df5["day"].iloc[-1] and int(rl["hm"]) < xm // 60 * 100 + xm % 60 and len(stopped) < 2
        pnl, cost = 0.0, 0.0
        for kd, kk, sgn in legs:
            p0 = entry[(kd, kk)]
            p1 = exit_px.get((kd, kk), options.premium(index, spot1, kk, kd, ets, t1, rl["vix"]))
            if sgn < 0:                                       # short leg: sell then buy back
                s0, b1 = max(0.05, p0 - _slip(p0)), p1 + _slip(p1)
                pnl += (s0 - b1) * lot
                cost += options.charges(s0 * lot, "sell", exch) + options.charges(b1 * lot, "buy", exch)
            else:                                             # hedge: buy then sell
                b0, s1 = p0 + _slip(p0), max(0.05, p1 - _slip(p1))
                pnl += (s1 - b0) * lot
                cost += options.charges(b0 * lot, "buy", exch) + options.charges(s1 * lot, "sell", exch)
        credit = (entry[("CE", k)] + entry[("PE", k)]) * lot
        trades.append(dict(index=index, side="SELL STRADDLE" + (f" ±{p['wing']} wings" if w else ""), kind="CE+PE",
                           strike=k, expiry=exp.isoformat(), t0=t0, t1=t1 if len(stopped) < 2 else max(stopped.values()),
                           spot0=float(spot0), spot1=float(spot1), credit=round(credit, 2), lot=lot,
                           charges=round(cost, 2), pnl=round(pnl - cost, 2), stops=",".join(sorted(stopped)) or "-",
                           result="open" if still_open else
                           ("both stopped" if len(stopped) == 2 else ("one leg stopped" if stopped else "time")),
                           legs=[dict(kind=kd, strike=kk, side="SELL" if sg < 0 else "BUY", entry=round(entry[(kd, kk)], 2),
                                      exit=round(exit_px.get((kd, kk), options.premium(index, spot1, kk, kd, ets, t1, rl["vix"])), 2),
                                      stopped=kd in stopped and sg < 0, sl=round(entry[(kd, kk)] * (1 + p["sl"]), 2) if sg < 0 else None)
                                 for kd, kk, sg in legs],
                           why=f"{p.get('entry_hm', 920)} straddle, SL {int(p['sl'] * 100)}%, DTE {dte}",
                           vix=round(float(vix0), 2), margin=round((w * lot if w else spot0 * lot * 0.12), 0)))
    return trades


# ------------------------------------------------------------------ stats
def stats(trades, capital=100000):
    if not trades:
        return dict(n=0)
    pnl = np.array([t["pnl"] for t in trades])
    eq = capital + np.cumsum(pnl)
    peak = np.maximum.accumulate(np.concatenate([[capital], eq]))[1:]
    wins = pnl[pnl > 0]
    loss = -pnl[pnl <= 0].sum()
    streak = cur = 0
    for x in pnl:
        cur = cur + 1 if x <= 0 else 0
        streak = max(streak, cur)
    days = (trades[-1]["t1"] - trades[0]["t0"]) / 86400 or 1
    return dict(n=len(trades), win=round(100 * len(wins) / len(pnl), 1), pf=round(wins.sum() / loss, 2) if loss else None,
                net=round(pnl.sum(), 0), avg=round(pnl.mean(), 0), max_dd=round(float((peak - eq).max()), 0),
                worst=round(pnl.min(), 0), best=round(pnl.max(), 0), lose_streak=streak,
                per_month=round(len(pnl) / (days / 30.4), 1))
