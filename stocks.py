"""Stock SWING trading (cash / delivery shares), paper only.

Universe: the Nifty 50 + the 20 F&O stocks the option bot already follows (current members: there is survivorship
bias in any backtest that goes back years with today's list). Daily candles from the free Upstox historical API
(they are already split / bonus ADJUSTED: e.g. RELIANCE's Oct-2024 1:1 bonus and BAJFINANCE's Jun-2025 split show no
gap), cached in data/eq/<SYM>.pkl and topped up incrementally.

Everything is end-of-day: a signal on day t's close is bought at day t+1's OPEN (no look-ahead). Stops / targets are
checked on each later day's high / low; rule exits (RSI, EMA, time) decided at a close are sold at the next open.
The research simulator and the paper account use the SAME day step (`_step`), so paper results follow the research.

Public:
    UNIVERSE, FINAL, keys(), candles(sym), load_all(), indicators(df), market_filters(), costs(value, side)
    prepare(frames) -> dict, simulate(prep, params, start, end, capital), stats(result)
    signals(today=None, prep=None, params=FINAL) -> list of buy candidates for tonight
    new_account(capital, params=FINAL) -> state,  paper_step(state, today=None, prep=None) -> (state, events)
"""
import gzip
import io
import json
import math
import time
from datetime import date, datetime, timedelta
from urllib.parse import quote

import numpy as np
import pandas as pd

import market

EQ_DIR = market.DATA / "eq"
START = date(2018, 1, 1)
NIFTY50 = ("ADANIENT", "ADANIPORTS", "APOLLOHOSP", "ASIANPAINT", "AXISBANK", "BAJAJ-AUTO", "BAJFINANCE", "BAJAJFINSV",
           "BEL", "BHARTIARTL", "CIPLA", "COALINDIA", "DRREDDY", "EICHERMOT", "ETERNAL", "GRASIM", "HCLTECH",
           "HDFCBANK", "HDFCLIFE", "HEROMOTOCO", "HINDALCO", "HINDUNILVR", "ICICIBANK", "INDUSINDBK", "INFY", "ITC",
           "JIOFIN", "JSWSTEEL", "KOTAKBANK", "LT", "M&M", "MARUTI", "NESTLEIND", "NTPC", "ONGC", "POWERGRID",
           "RELIANCE", "SBILIFE", "SBIN", "SHRIRAMFIN", "SUNPHARMA", "TATACONSUM", "TMPV", "TATASTEEL", "TCS",
           "TECHM", "TITAN", "TRENT", "ULTRACEMCO", "WIPRO")
FNO20 = ("RELIANCE", "HDFCBANK", "ICICIBANK", "INFY", "TCS", "SBIN", "AXISBANK", "KOTAKBANK", "LT", "BHARTIARTL",
         "ITC", "BAJFINANCE", "MARUTI", "SUNPHARMA", "HINDUNILVR", "M&M", "TATASTEEL", "HCLTECH", "TITAN", "ADANIENT")
UNIVERSE = tuple(sorted(set(NIFTY50) | set(FNO20)))

# ------------------------------------------------------------------ costs (Upstox delivery, Nov 2026)
SLIP = 0.001                    # 0.1% each side
BROKERAGE = 20.0                # ₹20 per executed order or 2.5%, whichever is lower
STT = 0.001                     # 0.1% on buy AND sell (delivery)
EXCH = 0.0000297                # NSE transaction charge
SEBI = 10 / 1e7
STAMP_BUY = 0.00015             # 0.015% on buy
GST = 0.18                      # on brokerage + exchange + SEBI
DP_SELL = 20.0                  # depository charge per scrip sold (incl. GST, approx.)


def costs(value, side):
    """₹ charges for one delivery order of `value` rupees ('buy' or 'sell'). Slippage is separate (in the price)."""
    if value <= 0:
        return 0.0
    brk = min(BROKERAGE, 0.025 * value)
    ex, sebi = EXCH * value, SEBI * value
    c = brk + STT * value + ex + sebi + GST * (brk + ex + sebi)
    c += STAMP_BUY * value if side == "buy" else DP_SELL
    return round(c, 2)


# ------------------------------------------------------------------ data
def keys(max_age_h=24):
    """Upstox instrument key for every UNIVERSE symbol (NSE_EQ), cached in data/eq_keys_all.json."""
    path = market.DATA / "eq_keys_all.json"
    if path.exists() and time.time() - path.stat().st_mtime < max_age_h * 3600:
        return json.loads(path.read_text())
    raw = market._session.get("https://assets.upstox.com/market-quote/instruments/exchange/NSE.json.gz", timeout=60)
    raw.raise_for_status()
    out = {x["trading_symbol"]: x["instrument_key"] for x in json.load(gzip.open(io.BytesIO(raw.content)))
           if x.get("instrument_type") == "EQ" and x.get("segment") == "NSE_EQ" and x.get("trading_symbol") in UNIVERSE}
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(out))
    return out


def names(max_age_h=72):
    """Company short names for the news search ("TITAN" → "Titan Company"), from the Upstox instrument file."""
    path = market.DATA / "eq_names.json"
    if path.exists() and time.time() - path.stat().st_mtime < max_age_h * 3600:
        return json.loads(path.read_text())
    raw = market._session.get("https://assets.upstox.com/market-quote/instruments/exchange/NSE.json.gz", timeout=60)
    raw.raise_for_status()
    out = {x["trading_symbol"]: (x.get("short_name") or x.get("name") or x["trading_symbol"]).strip()
           for x in json.load(gzip.open(io.BytesIO(raw.content)))
           if x.get("instrument_type") == "EQ" and x.get("segment") == "NSE_EQ" and x.get("trading_symbol") in UNIVERSE}
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(out))
    return out


def _fetch_days(key, a, b):
    """Daily candles (with volume) between two dates; one request per ~year."""
    parts, cur = [], a
    while cur <= b:
        nxt = min(cur + timedelta(days=360), b)
        url = f"{market.API}/{quote(key, safe='')}/days/1/{nxt.isoformat()}/{cur.isoformat()}"
        rows = market._get(url)["data"]["candles"]
        if rows:
            parts.append(pd.DataFrame([r[:6] for r in rows], columns=["ts", "open", "high", "low", "close", "volume"]))
        cur = nxt + timedelta(days=1)
        time.sleep(0.2)
    if not parts:
        return pd.DataFrame(columns=["day", "open", "high", "low", "close", "volume"])
    df = pd.concat(parts)
    df["day"] = pd.to_datetime(market.epoch_s(df["ts"]), unit="s", utc=True).dt.tz_convert("Asia/Kolkata").dt.date
    df = df.drop(columns="ts").astype({c: float for c in ("open", "high", "low", "close", "volume")})
    return df.drop_duplicates("day", keep="last").sort_values("day").reset_index(drop=True)[
        ["day", "open", "high", "low", "close", "volume"]]


def candles(sym, refresh=True, key=None):
    """Daily candles since 2018 for one share (or an index key), cached and topped up with new days only."""
    name = sym.replace("&", "_").replace("|", "_").replace(" ", "_")
    path = EQ_DIR / f"{name}.pkl"
    df = pd.read_pickle(path) if path.exists() else None
    if refresh:
        today = datetime.now(market.IST).date()
        key = key or keys().get(sym)
        if key is None:
            return df
        start = START if df is None or df.empty else df["day"].iloc[-1]
        if df is None or start < today or (start == today and df["day"].iloc[-1] == today):
            new = _fetch_days(key, start, today)
            df = new if df is None else pd.concat([df, new])
            df = df.drop_duplicates("day", keep="last").sort_values("day").reset_index(drop=True)
            path.parent.mkdir(parents=True, exist_ok=True)
            market.save_pickle(df, path)
    return df


def nifty_daily(refresh=True):
    return candles("NIFTY50_INDEX", refresh, key=market.INDICES["NIFTY"]["key"])


# Corporate actions the API does not adjust: (day the new price starts, factor applied to all earlier prices).
# TMPV = old TATAMOTORS: demerger of the commercial-vehicle business on 14 Oct 2025 (open / previous close = 0.605).
CORP_ACTIONS = {"TMPV": [(date(2025, 10, 14), 0.605)]}


def adjust(sym, df):
    for day, f in CORP_ACTIONS.get(sym, []):
        m = df["day"] < day
        df.loc[m, ["open", "high", "low", "close"]] = df.loc[m, ["open", "high", "low", "close"]] * f
    return df


def load_all(syms=UNIVERSE, refresh=True):
    """{sym: daily candles, corporate actions adjusted} for the universe (missing symbols are skipped)."""
    out = {}
    for s in syms:
        try:
            df = candles(s, refresh)
        except Exception:
            df = candles(s, False)
        if df is not None and len(df) > 60:
            out[s] = adjust(s, df.copy())
    return out


def split_gaps(df, thr=0.35):
    """Overnight moves bigger than `thr` (would hint at an unadjusted split / bonus). Returns a list of (day, ratio)."""
    r = df["open"].to_numpy()[1:] / df["close"].to_numpy()[:-1]
    idx = np.where((r < 1 - thr) | (r > 1 + thr))[0]
    return [(df["day"].iloc[i + 1], round(float(r[i]), 3)) for i in idx]


# ------------------------------------------------------------------ indicators
def _rsi(c, n):
    d = c.diff()
    up = d.clip(lower=0).ewm(alpha=1 / n, adjust=False).mean()
    dn = (-d.clip(upper=0)).ewm(alpha=1 / n, adjust=False).mean()
    r = 100 - 100 / (1 + up / dn.replace(0, np.nan))
    return r.where(dn > 0, np.where(up > 0, 100.0, 50.0)).where(d.notna() | (r.index > 0))   # no down days → 100


def indicators(df):
    """Adds EMA 5/10/50/200, RSI 2/14, ATR 14, prior 20/55-day highs (excluding today), 20-day average volume,
    6/12-month momentum (skipping the last month)."""
    d = df.copy()
    c = d["close"]
    for n in (5, 10, 50, 200):
        d[f"ema{n}"] = c.ewm(span=n, adjust=False).mean()
    d["rsi2"], d["rsi14"] = _rsi(c, 2), _rsi(c, 14)
    tr = pd.concat([d["high"] - d["low"], (d["high"] - c.shift()).abs(), (d["low"] - c.shift()).abs()], axis=1).max(axis=1)
    d["atr"] = tr.ewm(alpha=1 / 14, adjust=False).mean()
    d["hi20"], d["hi55"] = d["high"].rolling(20).max().shift(1), d["high"].rolling(55).max().shift(1)
    d["vol20"] = d["volume"].rolling(20).mean().shift(1)
    d["mom126"] = c.shift(21) / c.shift(126) - 1
    d["mom252"] = c.shift(21) / c.shift(252) - 1
    d.loc[d.index < 200, "ema200"] = np.nan                     # not enough history yet
    return d


def market_filters(nifty=None, refresh=True):
    """Per-day market context known at that day's close: Nifty above its EMA 200, India VIX 60-day rank, and the
    smart-money score (NSE participant data; only where research/out/buy_features_tech.pkl or live features exist)."""
    import swing
    n = indicators(nifty if nifty is not None else nifty_daily(refresh))
    out = pd.DataFrame({"day": n["day"], "nifty_up": (n["close"] > n["ema200"]).where(n["ema200"].notna())})
    try:
        v = market.vix_daily()
        r = swing.vix_rank(v)
        out = out.merge(pd.DataFrame({"day": r.index, "vix_rank": r.values}), on="day", how="left")
    except Exception:
        out["vix_rank"] = np.nan
    try:
        f = pd.read_pickle(market.DATA.parent / "research" / "out" / "buy_features_tech.pkl")
        s = f[f["sym"] == "NIFTY"][["day", "smart"]]
        out = out.merge(s, on="day", how="left")
    except Exception:
        out["smart"] = np.nan
    return out


# ------------------------------------------------------------------ strategies
# Final paper rules: see research/stock_research.py and research/out/stock_summary.md (filled in by the research).
FINAL = dict(kind="momentum", lookback=252, freq=21, max_pos=3, regime=True, vix_max=None, smart_min=None,
             max_hold=10 ** 9, label="12-month momentum, top 3, monthly", win=50.0, pf=4.55)
# Research (research/stock_research.py + stock_final.py, ₹20k, real delivery charges): 2019–23 CAGR 17.5% (Nifty 14.8%),
# max drawdown −30% (Nifty −38%); 2024–26 CAGR 23.0% (Nifty 5.4%), drawdown −18% (Nifty −16%). Pullback and breakout
# rules LOST after costs in both periods. win / pf above are the 2024–26 (unseen) trade stats.


def _entry_ok(r, p):
    """Is this symbol a buy at today's close? r = today's indicator row (dict)."""
    if not (r["close"] > 0) or pd.isna(r.get("ema200")):
        return False
    if p["kind"] == "pullback":
        return r["close"] > r["ema200"] and r["rsi2"] < p["rsi_thr"]
    if p["kind"] == "breakout":
        hi = r[f"hi{p['n']}"]
        vol_ok = not p.get("vol_mult") or (r["vol20"] > 0 and r["volume"] > p["vol_mult"] * r["vol20"])
        return r["close"] > r["ema200"] and hi > 0 and r["close"] > hi and vol_ok
    return False


def _rank(r, p):
    """Lower = better when there are more candidates than free slots."""
    if p["kind"] == "pullback":
        return r["rsi2"]
    if p["kind"] == "breakout":
        return -(r["volume"] / r["vol20"]) if r["vol20"] else 0
    return 0


def _levels(r, p):
    """Stop / target for a new position from the signal-day row (ATR based), relative to the reference close."""
    atr, c = r["atr"], r["close"]
    stop = target = None
    if p["kind"] == "pullback" and p.get("stop_atr"):
        stop = c - p["stop_atr"] * atr
    if p["kind"] == "breakout":
        stop = c - p["atr_k"] * atr
        if p.get("exit") == "rr":
            target = c + 2 * p["atr_k"] * atr
    return stop, target


def _rule_exit(r, pos, p):
    """Exit decided at today's close (sold at the next open)? Returns a reason or None."""
    if pos["days"] >= p["max_hold"]:
        return "time"
    if p["kind"] == "pullback":
        if p["exit"] == "rsi70" and r["rsi2"] > 70:
            return "RSI(2) > 70"
        if p["exit"] in ("ema5", "ema10") and r["close"] > r[p["exit"]]:
            return f"close > {p['exit'].upper()}"
    if p["kind"] == "breakout" and p.get("exit") == "trail":
        pos["stop"] = max(pos["stop"] or 0, pos["high"] - p["atr_k"] * r["atr"])   # ratchet the trailing stop
    return None


def _filters_ok(m, p):
    if p.get("regime") and not m.get("nifty_up"):
        return False
    if p.get("vix_max") is not None and m.get("vix_rank") is not None and not pd.isna(m.get("vix_rank")) \
            and m["vix_rank"] > p["vix_max"]:
        return False
    if p.get("smart_min") is not None and m.get("smart") is not None and not pd.isna(m.get("smart")) \
            and m["smart"] < p["smart_min"]:
        return False
    return True


def prepare(frames, filters=None):
    """Indicator tables for the simulator: {'rows': {sym: {day: row}}, 'days': sorted trading days, 'mkt': {day: row}}."""
    rows, days = {}, set()
    for s, df in frames.items():
        d = indicators(df)
        rows[s] = {r["day"]: r for r in d.to_dict("records")}
        days.update(rows[s])
    mkt = {} if filters is None else {r["day"]: r for r in filters.to_dict("records")}
    return dict(rows=rows, days=sorted(days), mkt=mkt)


def new_account(capital=20000.0, params=FINAL):
    return dict(capital=float(capital), cash=float(capital), params=dict(params), positions=[], pending_buy=[],
                pending_sell=[], closed=[], equity=[], asof=None, n=0)


def _buy_px(o):
    return o * (1 + SLIP)


def _sell_px(o):
    return o * (1 - SLIP)


def _close_pos(st, pos, day, px, reason):
    gross = pos["shares"] * px
    ch = costs(gross, "sell")
    st["cash"] += gross - ch
    pnl = gross - ch - pos["cost"]
    st["closed"].append(dict(sym=pos["sym"], signal_day=pos["signal_day"], entry_day=pos["entry_day"], exit_day=day,
                             entry=round(pos["entry"], 2), exit=round(px, 2), shares=pos["shares"],
                             cost=round(pos["cost"], 2), pnl=round(pnl, 2), ret=round(pnl / pos["cost"], 4),
                             days=pos["days"], reason=reason, why=pos.get("why", "")))


def _equity(st, rows, day):
    v = st["cash"]
    for pos in st["positions"]:
        r = rows[pos["sym"]].get(day)
        v += pos["shares"] * (r["close"] if r else pos["last"])
    return v


def _step(st, day, prep, events=None):
    """Advance the account by one trading day: fills at the open, stops / targets intraday, decisions at the close."""
    p, rows, ev = st["params"], prep["rows"], (events if events is not None else [])
    # 1) next-open orders decided yesterday: sells first (frees cash), then buys
    for o in st["pending_sell"]:
        pos = next((x for x in st["positions"] if x["sym"] == o["sym"]), None)
        r = rows[o["sym"]].get(day)
        if pos and r:
            _close_pos(st, pos, day, _sell_px(r["open"]), o["reason"])
            st["positions"].remove(pos)
            ev.append(dict(type="sell", day=day, **st["closed"][-1]))
    st["pending_sell"] = [o for o in st["pending_sell"] if rows[o["sym"]].get(day) is None]   # retry if no bar
    eq_now = _equity(st, rows, day)
    for o in st["pending_buy"]:
        r = rows[o["sym"]].get(day)
        if r is None or len(st["positions"]) >= p["max_pos"] or any(x["sym"] == o["sym"] for x in st["positions"]):
            continue
        px = _buy_px(r["open"])
        budget = min(st["cash"], eq_now / p["max_pos"])
        sh = int(budget // (px * (1 + STT + STAMP_BUY) + 0.01))
        while sh > 0 and sh * px + costs(sh * px, "buy") > st["cash"]:
            sh -= 1
        if sh <= 0:
            ev.append(dict(type="skip", day=day, sym=o["sym"], reason=f"1 share costs ₹{px:,.0f}: more than the slot"))
            continue
        cost = sh * px + costs(sh * px, "buy")
        st["cash"] -= cost
        gap = px / o["ref"] if o["ref"] else 1.0                    # move stop / target with the opening gap
        pos = dict(sym=o["sym"], signal_day=o["day"], entry_day=day, entry=px, shares=sh, cost=cost, days=0,
                   stop=o["stop"] * gap if o["stop"] else None, target=o["target"] * gap if o["target"] else None,
                   high=r["high"], last=r["close"], why=o["why"])
        st["positions"].append(pos)
        ev.append(dict(type="buy", day=day, sym=o["sym"], price=round(px, 2), shares=sh, cost=round(cost, 2),
                       stop=round(pos["stop"], 2) if pos["stop"] else None,
                       target=round(pos["target"], 2) if pos["target"] else None, why=o["why"]))
    st["pending_buy"] = []
    # 2) intraday stop / target on today's bar (a stop is assumed hit before a target on the same day)
    for pos in list(st["positions"]):
        r = rows[pos["sym"]].get(day)
        if r is None:
            continue
        pos["days"] += 1
        hit = None
        if pos["stop"] and r["low"] <= pos["stop"]:
            hit = (min(r["open"], pos["stop"]), "stop")
        elif pos["target"] and r["high"] >= pos["target"]:
            hit = (max(r["open"], pos["target"]), "target")
        if hit:
            _close_pos(st, pos, day, _sell_px(hit[0]), hit[1])
            st["positions"].remove(pos)
            ev.append(dict(type="sell", day=day, **st["closed"][-1]))
            continue
        pos["high"], pos["last"] = max(pos["high"], r["high"]), r["close"]
    # 3) at the close: rule exits → sell tomorrow; new signals → buy tomorrow
    if p["kind"] == "momentum":
        if st["n"] % p["freq"] == 0:                                  # rebalance day (every `freq` trading days)
            ranked = momentum_rank(prep, day, p)
            ok = _filters_ok(prep["mkt"].get(day, {}), p)
            target = {x["sym"] for x in ranked[:p["max_pos"]]} if ok else set()
            for pos in st["positions"]:
                if pos["sym"] not in target and not any(o["sym"] == pos["sym"] for o in st["pending_sell"]):
                    st["pending_sell"].append(dict(sym=pos["sym"], day=day,
                                                   reason="rebalance" if ok else "market filter (Nifty < EMA200)"))
            held = {x["sym"] for x in st["positions"]}
            for x in ranked[:p["max_pos"]]:
                if ok and x["sym"] not in held:
                    st["pending_buy"].append(dict(sym=x["sym"], day=day, ref=x["ref"], stop=None, target=None, why=x["why"]))
        st["equity"].append((day, round(_equity(st, rows, day), 2)))
        st["asof"], st["n"] = day, st["n"] + 1
        return st
    for pos in st["positions"]:
        r = rows[pos["sym"]].get(day)
        if r is not None:
            why = _rule_exit(r, pos, p)
            if why and not any(o["sym"] == pos["sym"] for o in st["pending_sell"]):
                st["pending_sell"].append(dict(sym=pos["sym"], reason=why, day=day))
    m = prep["mkt"].get(day, {})
    free = p["max_pos"] - len(st["positions"]) + len(st["pending_sell"])
    if free > 0 and _filters_ok(m, p):
        held = {x["sym"] for x in st["positions"]}
        cands = []
        for s, rs in rows.items():
            r = rs.get(day)
            if r is not None and s not in held and _entry_ok(r, p):
                cands.append((_rank(r, p), s, r))
        cands.sort(key=lambda x: x[0])
        for _, s, r in cands[:free]:
            stop, target = _levels(r, p)
            st["pending_buy"].append(dict(sym=s, day=day, ref=r["close"], stop=stop, target=target, why=why_text(r, p)))
    st["equity"].append((day, round(_equity(st, rows, day), 2)))
    st["asof"], st["n"] = day, st["n"] + 1
    return st


def why_text(r, p):
    if p["kind"] == "pullback":
        return (f"uptrend (close {r['close']:,.1f} > EMA200 {r['ema200']:,.1f}) and a sharp dip: RSI(2) {r['rsi2']:.1f} "
                f"< {p['rsi_thr']}")
    if p["kind"] == "breakout":
        hi = r["hi%d" % p["n"]]
        return (f"close {r['close']:,.1f} above the {p['n']}-day high {hi:,.1f} on "
                f"{r['volume'] / r['vol20']:.1f}× average volume, above EMA200")
    return ""


def momentum_rank(prep, day, p):
    """Shares ranked by momentum on `day` (return from 12 or 6 months ago to 1 month ago), only those above their
    EMA 200. Each item: sym, ref (close), mom, why."""
    col = f"mom{p['lookback']}"
    out = []
    for s, rs in prep["rows"].items():
        r = rs.get(day)
        if r is None or pd.isna(r.get(col)) or pd.isna(r.get("ema200")) or not r["close"] > r["ema200"]:
            continue
        out.append(dict(sym=s, ref=round(float(r["close"]), 2), mom=round(100 * float(r[col]), 1),
                        why=f"{int(p['lookback'] / 21)}-month momentum {100 * r[col]:+.0f}% (skipping the last month), above EMA200"))
    out.sort(key=lambda x: -x["mom"])
    return out


def simulate(prep, p, start, end, capital=20000.0):
    """Run the strategy day by day between two dates with a fresh account. Open positions at the end are valued at
    the last close (reported as reason 'open')."""
    st = new_account(capital, p)
    for day in prep["days"]:
        if start <= day <= end:
            _step(st, day, prep)
    for pos in st["positions"]:
        st["closed"].append(dict(sym=pos["sym"], signal_day=pos["signal_day"], entry_day=pos["entry_day"],
                                 exit_day=st["asof"], entry=round(pos["entry"], 2), exit=round(pos["last"], 2),
                                 shares=pos["shares"], cost=round(pos["cost"], 2),
                                 pnl=round(pos["shares"] * pos["last"] - pos["cost"], 2), ret=None, days=pos["days"],
                                 reason="open", why=pos.get("why", "")))
    return st


def stats(st):
    t = [x for x in st["closed"] if x["reason"] != "open"]
    pnl = np.array([x["pnl"] for x in t]) if t else np.array([])
    gw, gl = pnl[pnl > 0].sum(), -pnl[pnl < 0].sum()
    eq = pd.Series([e for _, e in st["equity"]], index=[d for d, _ in st["equity"]], dtype=float)
    if len(eq) < 2:
        return dict(n=len(t), win=None, pf=None, cagr=None, maxdd=None, end=st["capital"])
    yrs = max((eq.index[-1] - eq.index[0]).days / 365.25, 1e-9)
    cagr = (eq.iloc[-1] / st["capital"]) ** (1 / yrs) - 1
    dd = float((eq / eq.cummax() - 1).min())
    return dict(n=len(t), win=round(100 * float((pnl > 0).mean()), 1) if len(t) else None,
                pf=round(float(gw / gl), 2) if gl else None, cagr=round(100 * cagr, 1), maxdd=round(100 * dd, 1),
                end=round(float(eq.iloc[-1])), avg_days=round(float(np.mean([x["days"] for x in t])), 1) if t else None)


def yearly(st):
    eq = pd.Series([e for _, e in st["equity"]], index=pd.to_datetime([d for d, _ in st["equity"]]), dtype=float)
    y = eq.groupby(eq.index.year).last()
    prev = pd.concat([pd.Series([st["capital"]]), y.iloc[:-1]]).to_numpy()
    return {int(k): round(100 * (v / pv - 1), 1) for k, v, pv in zip(y.index, y.to_numpy(), prev)}


# ------------------------------------------------------------------ live use (bot / dashboard)
def build(refresh=True):
    """Fresh data + indicators for tonight (downloads only missing days)."""
    frames = load_all(refresh=refresh)
    return prepare(frames, market_filters(refresh=refresh))


def signals(today=None, prep=None, params=FINAL):
    """Tonight's buy candidates (bought at tomorrow's open): ranked best first, with the reference close, ATR stop /
    target if the rules use them, the exit rule in words, the reason and the strategy's tested win% / PF."""
    prep = prep or build()
    day = today or prep["days"][-1]
    m = prep["mkt"].get(day, {})
    out = []
    if not _filters_ok(m, params):
        return out
    if params["kind"] == "momentum":                                   # the current top-K = what the account holds
        for i, x in enumerate(momentum_rank(prep, day, params)[:params["max_pos"]]):
            out.append(dict(sym=x["sym"], day=day, ref=x["ref"], stop=None, target=None, rank=i + 1, mom=x["mom"],
                            exit_rule=exit_rule_text(params), why=x["why"], strategy=params.get("label"),
                            win=params.get("win"), pf=params.get("pf")))
        return out
    for s, rs in prep["rows"].items():
        r = rs.get(day)
        if r is None or not _entry_ok(r, params):
            continue
        stop, target = _levels(r, params)
        out.append(dict(sym=s, day=day, ref=round(r["close"], 2), stop=round(stop, 2) if stop else None,
                        target=round(target, 2) if target else None, rank=float(_rank(r, params)),
                        exit_rule=exit_rule_text(params), why=why_text(r, params), rsi2=round(float(r["rsi2"]), 1),
                        strategy=params.get("label"), win=params.get("win"), pf=params.get("pf")))
    out.sort(key=lambda x: x["rank"])
    return out


def exit_rule_text(p):
    if p["kind"] == "pullback":
        x = {"rsi70": "RSI(2) closes above 70", "ema5": "close above EMA 5", "ema10": "close above EMA 10"}[p["exit"]]
        s = f"sell at the next open when {x}, or after {p['max_hold']} days"
        return s + (f"; stop {p['stop_atr']}× ATR below entry" if p.get("stop_atr") else "")
    if p["kind"] == "momentum":
        return (f"held while it stays in the top {p['max_pos']} at the monthly rebalance (every {p['freq']} trading days); "
                "everything is sold when Nifty closes a rebalance day below its EMA 200")
    if p["kind"] == "breakout":
        return (f"trailing stop {p['atr_k']}× ATR below the highest high" if p["exit"] == "trail"
                else f"stop {p['atr_k']}× ATR, target {2 * p['atr_k']}× ATR (1:2)") + f", max {p['max_hold']} days"
    return ""


def paper_step(state, today=None, prep=None):
    """Bring a paper account (from new_account) up to `today` (default: last day with data), one day at a time.
    Returns (state, events). Events: buy / sell / skip dicts for Telegram. Safe to call repeatedly."""
    prep = prep or build()
    events = []
    last = today or prep["days"][-1]
    for day in prep["days"]:
        if (state["asof"] is None and day == last) or (state["asof"] is not None and state["asof"] < day <= last):
            _step(state, day, prep, events)
    return state, events
