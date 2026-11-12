"""Live (during market hours) ESTIMATES for the option-buying paper books. No broker login → no free live option
quotes, so: implied volatility is backed out of the option's last REAL NSE close, then the option is re-priced with
the LIVE underlying price (Upstox public 1-minute candles). Shown as "est."; the evening job settles trades on real
NSE prices. Read-only, never places orders."""
import gzip
import io
import json
import os
import time
from datetime import date, datetime, timedelta, timezone

import market
import options

IST = timezone(timedelta(hours=5, minutes=30))
_cache = {}


def eq_keys(max_age_h=24):
    """Upstox instrument key per NSE share symbol (e.g. RELIANCE → NSE_EQ|INE002A01018), cached."""
    path = market.DATA / "eq_keys.json"
    if path.exists() and time.time() - path.stat().st_mtime < max_age_h * 3600:
        return json.loads(path.read_text())
    import fno
    raw = market._session.get("https://assets.upstox.com/market-quote/instruments/exchange/NSE.json.gz", timeout=60)
    raw.raise_for_status()
    out = {}
    for x in json.load(gzip.open(io.BytesIO(raw.content))):
        if x.get("instrument_type") == "EQ" and x.get("trading_symbol") in fno.STOCKS and x.get("segment") == "NSE_EQ":
            out[x["trading_symbol"]] = x["instrument_key"]
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(out))
    return out


def spot(sym, ttl=20):
    """Latest traded price of an index / share from today's 1-minute candles (None before the open)."""
    hit = _cache.get(sym)
    if hit and time.time() - hit[0] < ttl:
        return hit[1]
    key = market.INDICES[sym]["key"] if sym in market.INDICES else eq_keys().get(sym)
    px = None
    if key:
        try:
            d = market.intraday(key, 1)
            px = float(d["close"].iloc[-1]) if len(d) else None
        except Exception:
            px = None
    _cache[sym] = (time.time(), px)
    return px


def _expiry_ts(e):
    d = date.fromisoformat(e) if isinstance(e, str) else e
    return int(datetime(d.year, d.month, d.day, 15, 30, tzinfo=IST).timestamp())


def reprice(sym, kind, strike, expiry, ref_price, ref_spot, ref_day, now_spot, now_ts=None):
    """Option value now, from its real close `ref_price` (with underlying `ref_spot`) on `ref_day`."""
    now_ts = now_ts or int(time.time())
    e = _expiry_ts(expiry)
    d = date.fromisoformat(ref_day) if isinstance(ref_day, str) else ref_day
    t_ref = options.trading_years(int(datetime(d.year, d.month, d.day, 15, 30, tzinfo=IST).timestamp()) - 60, e)
    t_now = options.trading_years(now_ts, e)
    iv = options.implied_vol(ref_price, ref_spot, strike, max(t_ref, 1e-4), kind) or 0.2
    return options.bs_price(now_spot, strike, t_now, iv, kind)


# ------------------------------------------------------------------ optional: real live option prices (free token)
def token():
    """Upstox ANALYTICS token (free, read-only, 1 year) from .env: UPSTOX_ANALYTICS_TOKEN. Empty = estimates only."""
    return os.getenv("UPSTOX_ANALYTICS_TOKEN", "").strip()


def quotes(keys, ttl=1.0):
    """Live last price + today's open/high/low for instrument keys (one Upstox request, needs the token).
    {key: dict(last, open, high, low)} or None without a token / on error. Cached 1 second for all page viewers."""
    tk = token()
    if not tk or not keys:
        return None
    ck = ("quotes", tuple(keys))
    hit = _cache.get(ck)
    if hit and time.time() - hit[0] < ttl:
        return hit[1]
    out = None
    try:
        r = market._session.get("https://api.upstox.com/v3/market-quote/ohlc", timeout=6,
                                params={"instrument_key": ",".join(keys), "interval": "1d"},
                                headers={"Authorization": f"Bearer {tk}", "Accept": "application/json"})
        if r.status_code == 200:
            out = {}
            for x in r.json().get("data", {}).values():
                o = x.get("live_ohlc") or {}
                if x.get("last_price") is not None:
                    out[x.get("instrument_token")] = dict(last=float(x["last_price"]), open=o.get("open"),
                                                          high=o.get("high"), low=o.get("low"))
    except Exception:
        out = None
    _cache[ck] = (time.time(), out)
    return out


def option_chain(sym, expiry, ttl=15):
    """Live option chain for one expiry from Upstox (needs the token). {(strike, 'CE'/'PE'): dict(ltp, oi, delta,
    theta, gamma, vega, iv)} or None. The token is sent only to api.upstox.com, never logged or shown."""
    tk = token()
    if not tk:
        return None
    ck = ("chain", sym, str(expiry))
    hit = _cache.get(ck)
    if hit and time.time() - hit[0] < ttl:
        return hit[1]
    key = market.INDICES[sym]["key"] if sym in market.INDICES else eq_keys().get(sym)
    out = None
    try:
        r = market._session.get("https://api.upstox.com/v2/option/chain", timeout=10,
                                params={"instrument_key": key, "expiry_date": str(expiry)},
                                headers={"Authorization": f"Bearer {tk}", "Accept": "application/json"})
        if r.status_code == 200:
            out = {}
            for row in r.json().get("data", []):
                k = float(row["strike_price"])
                for side, kind in (("call_options", "CE"), ("put_options", "PE")):
                    x = row.get(side) or {}
                    md, gk = x.get("market_data") or {}, x.get("option_greeks") or {}
                    out[(k, kind)] = dict(ltp=md.get("ltp"), oi=md.get("oi"), delta=gk.get("delta"), theta=gk.get("theta"),
                                          gamma=gk.get("gamma"), vega=gk.get("vega"), iv=gk.get("iv"))
    except Exception:
        out = None
    _cache[ck] = (time.time(), out)
    return out


def mark(t, asof, ref_spot):
    """Live estimate for an open paper trade dict (single option or debit spread). Returns dict or None."""
    s = spot(t["sym"])
    ch = option_chain(t["sym"], t["expiry"])
    if ch:                                                 # real live prices (Upstox token)
        a = ch.get((float(t["strike"]), t["kind"])) or {}
        b = ch.get((float(t["strike2"]), t["kind"])) or {} if t.get("strike2") else {}
        if a.get("ltp"):
            val = a["ltp"] - (b.get("ltp") or 0 if t.get("strike2") else 0)
            pnl = (val - t["entry"]) * t["lot"] - t.get("charges", 0)
            th = ((a.get("theta") or 0) - ((b.get("theta") or 0) if t.get("strike2") else 0)) * t["lot"]
            return dict(spot=round(s, 2) if s else None, price=round(val, 2), pnl=round(pnl, 0), source="live",
                        delta=a.get("delta"), theta_day=round(th, 0), hit_stop=val <= t["stop"],
                        hit_target=bool(t.get("target")) and val >= t["target"])
    if s is None or not ref_spot:
        return None
    p1 = reprice(t["sym"], t["kind"], t["strike"], t["expiry"], t.get("last1") or t["exit"], ref_spot, asof, s)
    val = p1
    if t.get("strike2"):
        p2 = reprice(t["sym"], t["kind"], t["strike2"], t["expiry"], t.get("last2") or 0.05, ref_spot, asof, s)
        val = p1 - p2
    pnl = (val - t["entry"]) * t["lot"] - t.get("charges", 0)
    return dict(spot=round(s, 2), price=round(val, 2), pnl=round(pnl, 0), source="est",
                hit_stop=val <= t["stop"], hit_target=bool(t.get("target")) and val >= t["target"])
