"""Option BUYING engine (small capital, e.g. ₹20,000): end-of-day signals → buy CE/PE next morning → exit on
stop / target / holding-day limit / expiry. Valued on REAL NSE option prices (daily bhavcopy), not a model.

Timeline (no look-ahead):
  day t, after the close   NSE publishes OI, prices and FII/DII positions → signals are computed
  day t+1, 09:15           buy at the REAL opening price of that option (+ slippage)
  each following day       stop / target checked on the option's REAL high / low; if both are touched on the same
                           day the STOP is assumed first (conservative); a gap through a level fills at the open
  exit                     at the level, at the close of the last holding day, or on expiry day's close

Signal families (each gives CE = bullish or PE = bearish):
  oi        option open-interest ratio near the money. The user's observation: when OI is ~30:70, the 30 side wins.
            call share ≤ thr (put writers dominate → support below) → BUY CE; put share ≤ thr → BUY PE.
  oichg     the same ratio on FRESH positions only (today's OI added)
  buildup   futures: price ↑ & OI ↑ (long buildup) → CE; price ↓ & OI ↑ (short buildup) → PE
  fii       FII index-futures positions: their long share rising by ≥ thr points → CE, falling → PE (indices only)
  trend     futures close above its EMA and rising → CE; below and falling → PE
  combos    "a+b" = both families agree on the same day
"""
import math
from datetime import date, timedelta

import numpy as np
import pandas as pd

import options

SLIP = {"index": 0.005, "stock": 0.015}       # slippage per order (stock options have wider bid/ask)
BAND = {"index": 0.05, "stock": 0.10}         # strikes within ±5% / ±10% of spot count for the OI ratio
BANDS = (0.01, 0.02, 0.03, 0.05, 0.10, 1.0)  # call share kept as cs1/cs2/cs3/cs5/cs10/cs100 (+ as.. = fresh OI)
PCT_WIN = 60                                   # "extreme vs its own last 60 days" window


def is_index(sym):
    return sym in ("NIFTY", "BANKNIFTY", "MIDCPNIFTY", "FINNIFTY", "SENSEX")


def ema(s, n):
    return s.ewm(span=n, adjust=False).mean()


# ------------------------------------------------------------------ daily features per symbol
def _spot(chain, fut):
    """Spot from put-call parity at the strike where CE ≈ PE, using only strikes that TRADED that day (an untraded
    strike's 'close' can be days old). Falls back to the futures price if parity is unavailable or > 3% away."""
    t = chain[chain["volume"] > 0]
    c = t[t["type"] == "CE"].set_index("strike")["close"]
    p = t[t["type"] == "PE"].set_index("strike")["close"]
    both = pd.concat([c, p], axis=1, keys=["c", "p"]).dropna()
    both = both[(both["c"] > 0) & (both["p"] > 0)]
    if both.empty:
        return fut
    k = (both["c"] - both["p"]).abs().idxmin()
    s = float(k + both.loc[k, "c"] - both.loc[k, "p"])
    return s if abs(s / fut - 1) < 0.03 else fut


def index_closes(syms=("NIFTY", "BANKNIFTY", "MIDCPNIFTY")):
    """Real daily index closes from the cached Upstox 5-minute candles: {sym: {day: close}}."""
    import market
    out = {}
    for s in syms:
        c = market.history(market.INDICES[s]["key"])
        d = pd.to_datetime(c["time"], unit="s", utc=True).dt.tz_convert("Asia/Kolkata").dt.date
        out[s] = c.assign(day=d).groupby("day")["close"].last().to_dict()
    return out


def features(fo, part=None, closes=None):
    """One row per (symbol, day) with spot, futures and option-OI features. `fo` = fno.load_all().
    closes = index_closes(): the real index close replaces the option-implied spot for the indices."""
    closes = closes or {}
    rows = []
    for (sym, day), g in fo.groupby(["sym", "day"], sort=True):
        fut = g[g["type"] == "FUT"].sort_values("expiry")
        opt = g[g["type"] != "FUT"]
        if fut.empty or opt.empty:
            continue
        exps = sorted(e for e in opt["expiry"].unique() if e >= day)
        if not exps:
            continue
        near = exps[0] if (exps[0] - day).days >= 1 or len(exps) == 1 else exps[1]   # skip same-day expiry
        ch = opt[opt["expiry"] == near]
        futpx = float(fut["close"].iloc[0])
        spot = closes.get(sym, {}).get(day)
        if spot is None:
            spot = float(g["spot"].dropna().iloc[0]) if g["spot"].notna().any() else _spot(ch, futpx)
        if not spot or math.isnan(spot):
            continue
        extra = {}
        for bn in BANDS:                                   # call share of OI for several strike bands
            zb = ch[(ch["strike"] >= spot * (1 - bn)) & (ch["strike"] <= spot * (1 + bn))]
            a, b_ = float(zb[zb["type"] == "CE"]["oi"].sum()), float(zb[zb["type"] == "PE"]["oi"].sum())
            ca = float(zb[zb["type"] == "CE"]["chg_oi"].clip(lower=0).sum())
            pa = float(zb[zb["type"] == "PE"]["chg_oi"].clip(lower=0).sum())
            extra[f"cs{int(bn*100)}"] = a / (a + b_) if a + b_ else np.nan
            extra[f"as{int(bn*100)}"] = ca / (ca + pa) if ca + pa else np.nan
        band = BAND["index" if is_index(sym) else "stock"]
        z = ch[(ch["strike"] >= spot * (1 - band)) & (ch["strike"] <= spot * (1 + band))]
        ce, pe = z[z["type"] == "CE"], z[z["type"] == "PE"]
        ce_oi, pe_oi = float(ce["oi"].sum()), float(pe["oi"].sum())
        ce_add, pe_add = float(ce["chg_oi"].clip(lower=0).sum()), float(pe["chg_oi"].clip(lower=0).sum())
        # strike with the most call / put OI = where writers defend (resistance / support)
        cu, pd_ = ce[ce["strike"] >= spot], pe[pe["strike"] <= spot]          # walls above / below the price
        res = float(cu.loc[cu["oi"].idxmax(), "strike"]) if len(cu) else np.nan
        sup = float(pd_.loc[pd_["oi"].idxmax(), "strike"]) if len(pd_) else np.nan
        rows.append(dict(sym=sym, day=day, spot=spot, fut=float(fut["close"].iloc[0]),
                         fut_oi=float(fut["oi"].sum()), fut_chg_oi=float(fut["chg_oi"].sum()),
                         ce_oi=ce_oi, pe_oi=pe_oi, call_share=ce_oi / (ce_oi + pe_oi) if ce_oi + pe_oi else np.nan,
                         ce_add=ce_add, pe_add=pe_add,
                         add_share=ce_add / (ce_add + pe_add) if ce_add + pe_add else np.nan,
                         pcr=pe_oi / ce_oi if ce_oi else np.nan, resistance=res, support=sup,
                         lot=float(g["lot"].dropna().iloc[0]) if g["lot"].notna().any() else np.nan, **extra))
    f = pd.DataFrame(rows).sort_values(["sym", "day"]).reset_index(drop=True)
    out = []
    for sym, g in f.groupby("sym"):
        g = g.copy()
        # splits / bonus issues (e.g. Reliance 1:1 Oct 2024): rescale the older prices so returns and EMAs are not
        # broken (a one-day move beyond -35% / +55% that the futures confirm is treated as a corporate action)
        r = (g["spot"] / g["spot"].shift()).to_numpy()
        rf = (g["fut"] / g["fut"].shift()).to_numpy()
        adj = np.ones(len(g))
        for i in range(1, len(g)):
            if (r[i] < 0.65 or r[i] > 1.55) and abs(rf[i] / r[i] - 1) < 0.05:
                adj[:i] *= r[i]
        g["px"] = g["spot"] * adj
        g["ret"] = g["px"].pct_change()
        g["fut_oi_pct"] = g["fut_chg_oi"] / (g["fut_oi"] - g["fut_chg_oi"]).replace(0, np.nan)
        for n in (10, 20, 50):
            g[f"ema{n}"] = ema(g["px"], n) / adj
        for c in [c for c in g.columns if c[:2] in ("cs", "as") and c[2:].isdigit()]:
            g[c + "_pct"] = g[c].rolling(PCT_WIN, min_periods=30).rank(pct=True)
        out.append(g)
    f = pd.concat(out, ignore_index=True)
    if part is not None and len(part):
        if "FII_fut_share" in part:                        # full participant table (fno.participant_table)
            p = part.copy()
            p["fii_long_share"], p["fii_share_chg"] = p["FII_fut_share"], p["FII_fut_share_chg"]
            f = f.merge(p, on="day", how="left")
        else:                                              # older short form (fno.participants_all)
            p = part.sort_values("day").copy()
            p["fii_long_share"] = p["fii_fut_long"] / (p["fii_fut_long"] + p["fii_fut_short"])
            p["fii_share_chg"] = p["fii_long_share"].diff()
            f = f.merge(p[["day", "fii_long_share", "fii_share_chg", "fii_call_net", "fii_put_net"]], on="day", how="left")
    if "DII_fut_share_rk" in f:
        f["smart"] = smart_score(f)
    return f


# "Smart money" score: the 5 participant measures that were the best stand-alone buy signals on 2022–24 ONLY
# (research/fii_research.py → research/smart_trainonly.py). Each is a 60-day percentile rank, oriented so that
# high = bullish; the score is their average. 1 = everything bullish, 0 = everything bearish.
SMART_PARTS = (("DII_opt_net_rk", -1),           # DII index options net position — fade
               ("Client_fut_net_chg_rk", -1),    # retail (Client) adding index-futures longs — fade
               ("DII_fut_net_chg_rk", 1),        # DII adding index-futures longs — follow
               ("FII_opt_net_rk", 1),            # FII index options net position — follow
               ("DII_fut_share_rk", 1))          # DII index-futures long share — follow


def smart_score(f):
    return sum((f[c] if d > 0 else 1 - f[c]) for c, d in SMART_PARTS) / len(SMART_PARTS)


# ------------------------------------------------------------------ signals: +1 = buy CE, -1 = buy PE, 0 = nothing
def _share_signal(f, p, prefix):
    """mode 'abs': share ≤ thr → +1, ≥ 1-thr → -1 (e.g. 30:70).  mode 'pct': share in the lowest / highest
    `thr` of its own last 60 days."""
    col = f"{prefix}{p.get('band', 5)}"
    thr = p.get("thr", 0.30)
    if p.get("mode", "abs") == "pct":
        x = f[col + "_pct"]
        s = np.where(x <= thr, 1, np.where(x >= 1 - thr, -1, 0))
    else:
        x = f[col]
        s = np.where(x <= thr, 1, np.where(x >= 1 - thr, -1, 0))
    return s


def _sig_oi(f, p):
    return _share_signal(f, p, "cs")


def _sig_oichg(f, p):
    return _share_signal(f, p, "as")


def _sig_buildup(f, p):
    m = p.get("oi_min", 0.03)                                  # futures OI must grow ≥ 3% that day
    up, dn = f["ret"] > 0, f["ret"] < 0
    grow = f["fut_oi_pct"] >= m
    return np.where(up & grow, 1, np.where(dn & grow, -1, 0))


def _sig_fii(f, p):
    thr = p.get("fii_thr", 0.03)
    c = f.get("fii_share_chg", pd.Series(np.nan, index=f.index))
    s = np.where(c >= thr, 1, np.where(c <= -thr, -1, 0))
    return np.where(f["sym"].map(is_index), s, 0)


def _sig_trend(f, p):
    n = p.get("ema", 20)
    e = f[f"ema{n}"]
    return np.where((f["spot"] > e) & (f["ret"] > 0), 1, np.where((f["spot"] < e) & (f["ret"] < 0), -1, 0))


def _sig_smart(f, p):
    t = p.get("smart_thr", 0.2)
    x = f["smart"] if "smart" in f else pd.Series(np.nan, index=f.index)
    return np.where(x >= 1 - t, 1, np.where(x <= t, -1, 0))


FAMILIES = {"smart": _sig_smart, "oi": _sig_oi, "oichg": _sig_oichg, "buildup": _sig_buildup, "fii": _sig_fii, "trend": _sig_trend}
FAMILY_TEXT = {"smart": "smart money", "oi": "OI ratio", "oichg": "fresh-OI ratio", "buildup": "futures buildup", "fii": "FII futures",
               "trend": "trend"}


def signal(f, family, p):
    """Signal series for a family name like 'oi' or a combo 'oi+trend' (all parts must agree)."""
    parts = family.split("+")
    s = np.asarray(FAMILIES[parts[0]](f, p))
    for x in parts[1:]:
        o = np.asarray(FAMILIES[x](f, p))
        s = np.where(s == o, s, 0)
    if p.get("contra"):                                        # test the opposite reading of the same signal
        s = -s
    if p.get("vix_max_rank") is not None and "vix_rank" in f:  # options too expensive: skip when VIX is near its 60-day high
        s = np.where(f["vix_rank"].to_numpy(dtype=float) > p["vix_max_rank"], 0, s)
    return pd.Series(s, index=f.index)


def vix_rank(vix):
    """60-day percentile rank of India VIX per day (1.0 = highest of the last 60 days). vix: DataFrame day, close."""
    v = vix.set_index("day")["close"]
    return v.rolling(60).apply(lambda s: (s <= s.iloc[-1]).mean(), raw=False).rename("vix_rank")


def add_vix(f, vix=None):
    """Join India VIX and its 60-day rank on the signal day. Research (research/vix_research.py): skipping STOCK buys
    when the rank is above 80% raised PF 1.87→2.36 (2022–24) and 1.51→1.99 (2025–26); index buys did not improve."""
    import market
    try:
        vix = market.vix_daily() if vix is None else vix
    except Exception:
        return f                                               # offline: no filter rather than no signals
    r = vix_rank(vix)
    out = f.drop(columns=[c for c in ("vix", "vix_rank") if c in f]).merge(
        pd.DataFrame(dict(day=r.index, vix=vix.set_index("day")["close"].values, vix_rank=r.values)), on="day", how="left")
    return out


def why(row, family, p):
    """Plain-English reason for a signal row (Telegram / dashboard)."""
    bits = []
    for x in family.split("+"):
        if x in ("oi", "oichg"):
            col = ("cs" if x == "oi" else "as") + str(p.get("band", 5))
            v = row.get(col, np.nan)
            name = "OI" if x == "oi" else "fresh OI"
            if v == v:
                bits.append(f"{name} calls {v*100:.0f}% : puts {(1-v)*100:.0f}% (±{p.get('band', 5)}% strikes)")
        elif x == "buildup":
            kind = "long" if row["ret"] > 0 else "short"
            bits.append(f"{kind} buildup (price {row['ret']*100:+.1f}%, fut OI {row['fut_oi_pct']*100:+.1f}%)")
        elif x == "fii":
            bits.append(f"FII index-futures long share {row['fii_long_share']*100:.0f}% ({row['fii_share_chg']*100:+.1f} pts)")
        elif x == "smart":
            bits.append(f"smart-money score {row['smart']*100:.0f}/100 (DII futures long {row['DII_fut_share']*100:.0f}%, "
                        f"FII options {'net long' if row['FII_opt_net'] > 0 else 'net short'}, "
                        f"retail {'adding' if row['Client_fut_net_chg'] > 0 else 'cutting'} futures longs)")
        elif x == "trend":
            n = p.get("ema", 20)
            side = "above" if row["spot"] > row["ema%d" % n] else "below"
            bits.append(f"price {side} EMA{n}")
    if not math.isnan(row.get("support", np.nan)):
        bits.append(f"put wall {row['support']:.0f} / call wall {row['resistance']:.0f}")
    return " · ".join(bits)


# ------------------------------------------------------------------ option chain lookup (real prices)
class Chain:
    """Fast lookup of real daily option bars, built per symbol on first use.
    bars[(day, expiry, strike, type)] = (open, high, low, close, volume); menu[day] = {(expiry, type): strikes}."""

    def __init__(self, fo):
        self.opt = {s: g for s, g in fo[fo["type"] != "FUT"].groupby("sym", sort=False)}
        self.days = sorted(fo["day"].unique())
        self.next = {d: n for d, n in zip(self.days, self.days[1:])}
        self._sym = {}

    def sym(self, sym):
        if sym not in self._sym:
            g = self.opt.get(sym)
            bars, menu = {}, {}
            if g is not None:
                cols = [g[c].to_numpy() for c in ("day", "expiry", "strike", "type", "open", "high", "low", "close", "volume")]
                for d, e, k, t, o, h, l, c, v in zip(*cols):
                    bars[(d, e, float(k), t)] = (float(o), float(h), float(l), float(c), float(v))
                    menu.setdefault(d, {}).setdefault((e, t), []).append(float(k))
                for d in menu:
                    for key in menu[d]:
                        menu[d][key] = np.array(sorted(set(menu[d][key])))
            self._sym[sym] = (bars, menu)
        return self._sym[sym]


def pick_contract(menu_day, day, spot, kind, offset, min_dte):
    """Expiry: nearest with ≥ min_dte calendar days left. Strike: ATM ± offset strikes (offset > 0 = ITM)."""
    exps = sorted(e for (e, t) in menu_day if t == kind and (e - day).days >= min_dte)
    if not exps:
        return None
    e = exps[0]
    ks = menu_day[(e, kind)]
    i = int(np.abs(ks - spot).argmin())
    i = i - offset if kind == "CE" else i + offset
    if not 0 <= i < len(ks):
        return None
    return e, float(ks[i])


def simulate(f, chain, family, p, lots_map, budget=20000.0, start=None, end=None, syms=None, sig=None, capital=None):
    """Backtest one signal family on real option prices. Returns a list of trades (closed, or 'open' at the end).

    p: thr / mode / band / contra / oi_min / fii_thr / ema (signal), offset (strike), min_dte, sl, tp (fractions
       of premium; tp None = no target), hold (max trading days incl. entry day), afford (step OTM until 1 lot
       fits), spread (debit spread: also SELL the option `spread` strikes further out of the money).
    capital: None = every symbol trades independently (one position per symbol). A number = ONE small account:
       one position at a time across all symbols, a trade is taken only if it costs ≤ min(budget, equity)."""
    if sig is None:
        sig = signal(f, family, p)
    ff = f.assign(sig=np.asarray(sig))
    ff = ff[ff["sig"] != 0]
    if start:
        ff = ff[ff["day"] >= start]
    if end:
        ff = ff[ff["day"] <= end]
    if syms:
        ff = ff[ff["sym"].isin(syms)]
    ff = ff[ff["sym"].isin([k for k, v in lots_map.items() if v])]
    trades, busy, eq = [], {}, capital
    order = ff.sort_values(["day", "sym"]) if capital is not None else ff.sort_values(["sym", "day"])
    for r in order.itertuples(index=False):
        key = "_all" if capital is not None else r.sym
        if busy.get(key) is not None and r.day <= busy[key]:
            continue                                                # one position per symbol (or per account)
        bars, menu = chain.sym(r.sym)
        b = budget if capital is None else min(budget, eq)
        if b <= 0:
            break
        t = trade(chain, bars, menu, r.sym, r.day, r.spot, "CE" if r.sig > 0 else "PE", p, lots_map[r.sym],
                  SLIP["index" if is_index(r.sym) else "stock"], b)
        if t is None:
            continue
        t["why"] = why(r._asdict(), family, p)
        if capital is not None:
            eq += t["pnl"]
            t["equity"] = round(eq, 0)
        trades.append(t)
        busy[key] = t["exit_day"]
    trades.sort(key=lambda t: (t["entry_day"], t["sym"]))
    return trades


def trade(chain, bars, menu, sym, sig_day, spot, kind, p, lot, slip, budget):
    """One trade: signal on sig_day's close → buy at the next day's open → manage with daily high/low."""
    sl, tp, hold = p.get("sl", 0.4), p.get("tp", 1.0), p.get("hold", 3)
    d1 = chain.next.get(sig_day)
    if d1 is None or d1 not in menu:
        return None
    offset = p.get("offset", 0)
    for step in range(4 if p.get("afford", True) else 1):           # step out of the money until 1 lot fits
        c = pick_contract(menu[d1], d1, spot, kind, offset - step, p.get("min_dte", 7))
        if c is None:
            return None
        e, k = c
        b = bars.get((d1, e, k, kind))
        if b is None or not b[0] > 0 or not b[4] > 0:
            return None
        entry = b[0] * (1 + slip)
        if entry * lot <= budget:
            break
    if p.get("spread"):
        return _spread(chain, bars, menu, sym, sig_day, spot, kind, p, lot, slip, budget, d1, e, k)
    if entry * lot > budget or entry < 1:
        return None
    stop, target = entry * (1 - sl), (entry * (1 + tp) if tp else None)
    day, n, exit_px, reason, last = d1, 0, None, None, None
    while day is not None:
        b = bars.get((day, e, k, kind))
        n += 1
        if b is not None:
            o, h, l, cl = b[:4]
            last = (day, cl)
            if day != d1 and 0 < o <= stop:
                exit_px, reason = o, "stop (gap)"
            elif day != d1 and target and o >= target:
                exit_px, reason = o, "target (gap)"
            elif 0 < l <= stop:
                exit_px, reason = stop, "stop"
            elif target and h >= target:
                exit_px, reason = target, "target"
            elif day >= e:
                exit_px, reason = cl, "expiry"
            elif n >= hold:
                exit_px, reason = cl, "time"
            if exit_px is not None:
                break
        elif day >= e:
            break
        day = chain.next.get(day)
    if exit_px is None:
        if last is None:
            return None
        day, exit_px, reason = last[0], last[1], "open"            # still running at the end of the data
    exit_net = max(exit_px * (1 - slip), 0.0)
    chg = options.charges(entry * lot, "buy") + options.charges(exit_net * lot, "sell")
    pnl = (exit_net - entry) * lot - chg
    return dict(sym=sym, signal_day=sig_day, entry_day=d1, exit_day=day, kind=kind, strike=k, expiry=e,
                spot=round(float(spot), 2), entry=round(entry, 2), exit=round(exit_net, 2), stop=round(stop, 2),
                target=round(target, 2) if target else None, lot=int(lot), cost=round(entry * lot, 0),
                charges=round(chg, 2), pnl=round(pnl, 2), ret=round(pnl / (entry * lot), 4), reason=reason, days=n)


def _spread(chain, bars, menu, sym, sig_day, spot, kind, p, lot, slip, budget, d1, e, k):
    """Debit spread: BUY strike k, SELL the strike `spread` steps further out of the money (same expiry).
    The spread's daily value is checked on CLOSING prices (a spread's intraday high/low is not in the data)."""
    ks = menu[d1][(e, kind)]
    i = int(np.where(ks == k)[0][0]) + (p["spread"] if kind == "CE" else -p["spread"])
    if not 0 <= i < len(ks):
        return None
    k2 = float(ks[i])
    b1, b2 = bars.get((d1, e, k, kind)), bars.get((d1, e, k2, kind))
    if b2 is None or not b2[0] > 0 or not b2[4] > 0:
        return None
    debit = b1[0] * (1 + slip) - b2[0] * (1 - slip)
    if debit < 1 or debit * lot > budget:
        return None
    sl, tp, hold = p.get("sl", 0.4), p.get("tp"), p.get("hold", 3)
    stop, target = debit * (1 - sl), (debit * (1 + tp) if tp else None)
    day, n, val, reason, last = d1, 0, None, None, None
    while day is not None:
        x1, x2 = bars.get((day, e, k, kind)), bars.get((day, e, k2, kind))
        n += 1
        if x1 is not None and x2 is not None:
            v = x1[3] - x2[3]
            last = (day, v)
            if v <= stop:
                val, reason = v, "stop (close)"
            elif target and v >= target:
                val, reason = v, "target (close)"
            elif day >= e:
                val, reason = v, "expiry"
            elif n >= hold:
                val, reason = v, "time"
            if val is not None:
                break
        elif day >= e:
            break
        day = chain.next.get(day)
    if val is None:
        if last is None:
            return None
        day, val, reason = last[0], last[1], "open"
    x1, x2 = bars.get((day, e, k, kind)), bars.get((day, e, k2, kind))
    out = max(x1[3] * (1 - slip) - x2[3] * (1 + slip), 0.0)
    chg = (options.charges(b1[0] * lot, "buy") + options.charges(b2[0] * lot, "sell") +
           options.charges(x1[3] * lot, "sell") + options.charges(x2[3] * lot, "buy"))
    pnl = (out - debit) * lot - chg
    return dict(sym=sym, signal_day=sig_day, entry_day=d1, exit_day=day, kind=kind, strike=k, strike2=k2, expiry=e,
                spot=round(float(spot), 2), entry=round(debit, 2), exit=round(out, 2), stop=round(stop, 2),
                target=round(target, 2) if target else None, lot=int(lot), cost=round(debit * lot, 0),
                charges=round(chg, 2), pnl=round(pnl, 2), ret=round(pnl / (debit * lot), 4), reason=reason, days=n)


def stats(trades):
    if not trades:
        return dict(n=0, win=None, pf=None, net=0.0, avg=None, worst=None, best=None, maxdd=None)
    p = np.array([t["pnl"] for t in trades])
    gw, gl = p[p > 0].sum(), -p[p < 0].sum()
    eq = np.cumsum(p)
    dd = float((np.maximum.accumulate(np.concatenate([[0], eq])) - np.concatenate([[0], eq])).max())
    return dict(n=len(p), win=round(float((p > 0).mean()) * 100, 1), pf=round(float(gw / gl), 2) if gl else None,
                net=round(float(p.sum()), 0), avg=round(float(p.mean()), 0), worst=round(float(p.min()), 0),
                best=round(float(p.max()), 0), maxdd=round(dd, 0))


# ------------------------------------------------------------------ technical indicators (free, from daily bars)
def daily_bars(fo, index_syms=("NIFTY", "BANKNIFTY", "MIDCPNIFTY")):
    """Daily OHLC + volume per symbol. Indices: real index candles (Upstox). Stocks: the futures contract with the
    most open interest that day (front month; rolls automatically), whose prices track the share closely."""
    import market
    out = []
    for s in index_syms:
        c = market.history(market.INDICES[s]["key"])
        d = c.assign(day=pd.to_datetime(c["time"], unit="s", utc=True).dt.tz_convert("Asia/Kolkata").dt.date)
        g = d.groupby("day").agg(o=("open", "first"), h=("high", "max"), l=("low", "min"), c=("close", "last")).reset_index()
        g["v"] = np.nan
        out.append(g.assign(sym=s))
    fut = fo[(fo["type"] == "FUT") & ~fo["sym"].isin(index_syms)]
    fut = fut.sort_values("oi").groupby(["sym", "day"]).tail(1)
    out.append(fut.rename(columns=dict(open="o", high="h", low="l", close="c", volume="v"))[["sym", "day", "o", "h", "l", "c", "v"]])
    b = pd.concat(out, ignore_index=True).sort_values(["sym", "day"]).reset_index(drop=True)
    b = b[(b["o"] > 0) & (b["c"] > 0)]
    return b


def _rsi(c, n):
    d = c.diff()
    up, dn = d.clip(lower=0).ewm(alpha=1 / n, adjust=False).mean(), (-d.clip(upper=0)).ewm(alpha=1 / n, adjust=False).mean()
    return 100 - 100 / (1 + up / dn.replace(0, np.nan))


def technicals(bars):
    """Adds EMA 9/21/50/200, RSI 2/14, MACD, ATR 14, ADX 14, Supertrend(10,3), Bollinger %B / width, 20-day high /
    low, relative volume — all computed only from data up to and including each day (no look-ahead)."""
    res = []
    for s, g in bars.groupby("sym", sort=False):
        g = g.copy()
        # split / bonus adjustment (same rule as features): rescale older bars
        r = (g["c"] / g["c"].shift()).to_numpy()
        adj = np.ones(len(g))
        for i in range(1, len(g)):
            if r[i] < 0.65 or r[i] > 1.55:
                adj[:i] *= r[i]
        for k in "ohlc":
            g[k] = g[k] * adj
        c, h, l = g["c"], g["h"], g["l"]
        for n in (9, 21, 50, 200):
            g[f"e{n}"] = ema(c, n)
        g["rsi2"], g["rsi14"] = _rsi(c, 2), _rsi(c, 14)
        m = ema(c, 12) - ema(c, 26)
        g["macd"], g["macd_sig"] = m, ema(m, 9)
        tr = pd.concat([h - l, (h - c.shift()).abs(), (l - c.shift()).abs()], axis=1).max(axis=1)
        g["atr"] = tr.ewm(alpha=1 / 14, adjust=False).mean()
        upm, dnm = h.diff(), -l.diff()
        pdm = pd.Series(np.where((upm > dnm) & (upm > 0), upm, 0.0), index=g.index)
        ndm = pd.Series(np.where((dnm > upm) & (dnm > 0), dnm, 0.0), index=g.index)
        pdi = 100 * pdm.ewm(alpha=1 / 14, adjust=False).mean() / g["atr"]
        ndi = 100 * ndm.ewm(alpha=1 / 14, adjust=False).mean() / g["atr"]
        g["adx"] = (100 * (pdi - ndi).abs() / (pdi + ndi).replace(0, np.nan)).ewm(alpha=1 / 14, adjust=False).mean()
        g["pdi"], g["ndi"] = pdi, ndi
        # supertrend (10, 3)
        atr10 = tr.ewm(alpha=1 / 10, adjust=False).mean()
        mid = (h + l) / 2
        ub, lb = (mid + 3 * atr10).to_numpy(), (mid - 3 * atr10).to_numpy()
        cc = c.to_numpy()
        st = np.ones(len(g))
        fu, fl = ub.copy(), lb.copy()
        for i in range(1, len(g)):
            fu[i] = ub[i] if ub[i] < fu[i - 1] or cc[i - 1] > fu[i - 1] else fu[i - 1]
            fl[i] = lb[i] if lb[i] > fl[i - 1] or cc[i - 1] < fl[i - 1] else fl[i - 1]
            st[i] = 1 if cc[i] > fu[i - 1] else -1 if cc[i] < fl[i - 1] else st[i - 1]
        g["st"] = st
        sma, sd = c.rolling(20).mean(), c.rolling(20).std()
        g["bb_pb"] = (c - (sma - 2 * sd)) / (4 * sd)
        g["bb_w"] = 4 * sd / sma
        g["bb_w_rk"] = g["bb_w"].rolling(120, min_periods=60).rank(pct=True)
        g["hi20"], g["lo20"] = h.rolling(20).max().shift(), l.rolling(20).min().shift()
        g["rvol"] = g["v"] / g["v"].rolling(20).mean().shift()
        g["ret1"] = c.pct_change()
        res.append(g)
    return pd.concat(res, ignore_index=True)


def _t(f, c):
    return f[c] if c in f else pd.Series(np.nan, index=f.index)


def _sig_rsipb(f, p):
    """RSI pullback in a trend: above EMA50 and RSI(2) ≤ lo → CE; below EMA50 and RSI(2) ≥ 100-lo → PE."""
    lo = p.get("rsi_lo", 10)
    up, dn = _t(f, "c") > _t(f, "e50"), _t(f, "c") < _t(f, "e50")
    return np.where(up & (_t(f, "rsi2") <= lo), 1, np.where(dn & (_t(f, "rsi2") >= 100 - lo), -1, 0))


def _sig_breakout(f, p):
    """20-day breakout with a trending ADX: close above the prior 20-day high → CE, below the low → PE."""
    a = _t(f, "adx") >= p.get("adx", 20)
    return np.where(a & (_t(f, "c") > _t(f, "hi20")), 1, np.where(a & (_t(f, "c") < _t(f, "lo20")), -1, 0))


def _sig_emax(f, p):
    """EMA 9 crosses EMA 21 (today), in the direction of EMA 50."""
    e9, e21, e50, c = _t(f, "e9"), _t(f, "e21"), _t(f, "e50"), _t(f, "c")
    x = np.sign(e9 - e21)
    prev = x.groupby(f["sym"]).shift() if "sym" in f else x.shift()
    return np.where((x > 0) & (prev <= 0) & (c > e50), 1, np.where((x < 0) & (prev >= 0) & (c < e50), -1, 0))


def _sig_supertrend(f, p):
    st = _t(f, "st")
    prev = st.groupby(f["sym"]).shift() if "sym" in f else st.shift()
    return np.where((st > 0) & (prev < 0), 1, np.where((st < 0) & (prev > 0), -1, 0))


def _sig_macd(f, p):
    m, sg, c, e50 = _t(f, "macd"), _t(f, "macd_sig"), _t(f, "c"), _t(f, "e50")
    x = np.sign(m - sg)
    prev = x.groupby(f["sym"]).shift() if "sym" in f else x.shift()
    return np.where((x > 0) & (prev <= 0) & (c > e50), 1, np.where((x < 0) & (prev >= 0) & (c < e50), -1, 0))


def _sig_squeeze(f, p):
    """Bollinger squeeze breakout: bands were among the narrowest 20% of 120 days, close leaves the band."""
    sq = _t(f, "bb_w_rk").groupby(f["sym"]).shift() <= p.get("sq", 0.2) if "sym" in f else _t(f, "bb_w_rk").shift() <= 0.2
    return np.where(sq & (_t(f, "bb_pb") > 1), 1, np.where(sq & (_t(f, "bb_pb") < 0), -1, 0))


def _sig_trendfilter(f, p):
    """Direction of the bigger trend only (used in combos): above EMA50 and EMA50 above EMA200 → +1."""
    c, e50, e200 = _t(f, "c"), _t(f, "e50"), _t(f, "e200")
    return np.where((c > e50) & (e50 > e200), 1, np.where((c < e50) & (e50 < e200), -1, 0))


FAMILIES.update({"rsipb": _sig_rsipb, "breakout": _sig_breakout, "emax": _sig_emax, "supertrend": _sig_supertrend,
                 "macd": _sig_macd, "squeeze": _sig_squeeze, "tf": _sig_trendfilter})
FAMILY_TEXT.update({"rsipb": "RSI pullback", "breakout": "20-day breakout", "emax": "EMA 9/21 cross",
                    "supertrend": "Supertrend flip", "macd": "MACD cross", "squeeze": "Bollinger squeeze", "tf": "trend"})


def combine(cands, capital, max_pos=1, start=None):
    """ONE account fed by several strategies. `cands` = list of (priority, trade) from each strategy's own
    simulate(). On each entry day the best-priority signals are taken while the account has a free slot, no other
    position in that symbol, and the trade costs ≤ (cash available now) / (free slots). Cash available = start
    capital + P&L of trades already closed BEFORE that day − cost of positions still open. Returns trades taken,
    each with the account equity after its exit."""
    cands = sorted(cands, key=lambda x: (x[1]["entry_day"], x[0]))
    taken = []
    for pr, t in cands:
        if start and t["signal_day"] < start:
            continue
        d = t["entry_day"]
        open_ = [o for o in taken if o["exit_day"] >= d]
        if len(open_) >= max_pos or any(o["sym"] == t["sym"] for o in open_):
            continue
        realized = sum(o["pnl"] for o in taken if o["exit_day"] < d and o["reason"] != "open")
        cash = capital + realized - sum(o["cost"] for o in open_)
        if cash <= 0 or t["cost"] > cash / (max_pos - len(open_)):
            continue
        taken.append(dict(t))
    eq = float(capital)
    for t in sorted(taken, key=lambda t: (t["exit_day"], t["entry_day"])):
        if t["reason"] != "open":
            eq += t["pnl"]
        t["equity"] = round(eq, 0)
    taken.sort(key=lambda t: (t["entry_day"], t["sym"]))
    return taken
