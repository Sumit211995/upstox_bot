"""Option-chain recorder: builds our own INTRADAY option history (NSE's free files are daily only).

Every 5 minutes in market hours it saves one snapshot of the live option chain (Upstox Analytics token, read-only) for
Nifty and Bank Nifty, the two nearest expiries, strikes within ±8% of the index: price, volume, open interest,
bid/ask and greeks. One gzip CSV per index per day: data/chain/<INDEX>/<YYYYMMDD>.csv.gz (≈ 1–2 MB a day).
After 2–3 months this history lets us test intraday open-interest strategies that could not be tested before.
"""
import csv
import gzip
import logging
import time
from datetime import date

import engine
import live
import market

log = logging.getLogger("recorder")
DIR = market.DATA / "chain"
INDEXES = ("NIFTY", "BANKNIFTY")
EVERY = 300                       # seconds between snapshots
BAND = 0.08                       # keep strikes within ±8% of the index
COLS = ["ts", "expiry", "strike", "type", "spot", "ltp", "volume", "oi", "prev_oi", "bid", "bid_qty", "ask", "ask_qty",
        "close", "iv", "delta", "gamma", "theta", "vega", "pcr"]
_last = {}


def _rows(sym, expiry, ts):
    tk = live.token()
    r = market._session.get("https://api.upstox.com/v2/option/chain", timeout=10,
                            params={"instrument_key": market.INDICES[sym]["key"], "expiry_date": str(expiry)},
                            headers={"Authorization": f"Bearer {tk}", "Accept": "application/json"})
    r.raise_for_status()
    out = []
    for row in r.json().get("data", []):
        spot, k = row.get("underlying_spot_price"), float(row["strike_price"])
        if not spot or abs(k / spot - 1) > BAND:
            continue
        for side, kind in (("call_options", "CE"), ("put_options", "PE")):
            x = row.get(side) or {}
            m, g = x.get("market_data") or {}, x.get("option_greeks") or {}
            out.append([ts, str(expiry), k, kind, spot, m.get("ltp"), m.get("volume"), m.get("oi"), m.get("prev_oi"),
                        m.get("bid_price"), m.get("bid_qty"), m.get("ask_price"), m.get("ask_qty"), m.get("close_price"),
                        g.get("iv"), g.get("delta"), g.get("gamma"), g.get("theta"), g.get("vega"), row.get("pcr")])
    return out


def snapshot(now_ts=None, indexes=INDEXES):
    """Save one snapshot per index (skips if the token is missing). Returns rows written."""
    if not live.token():
        return 0
    ts = int(now_ts or time.time())
    today = date.fromtimestamp(ts)
    n = 0
    for sym in indexes:
        try:
            exps = [e for e in engine.expiries(sym) if e >= today][:2]
            rows = [r for e in exps for r in _rows(sym, e, ts)]
        except Exception as e:                                   # never let recording break the trading loop
            log.warning("chain snapshot %s failed: %s", sym, e)
            continue
        if not rows:
            continue
        path = DIR / sym / f"{today:%Y%m%d}.csv.gz"
        path.parent.mkdir(parents=True, exist_ok=True)
        new = not path.exists()
        with gzip.open(path, "at", newline="") as f:
            w = csv.writer(f)
            if new:
                w.writerow(COLS)
            w.writerows(rows)
        n += len(rows)
    return n


def maybe_snapshot(now_ts=None):
    """Call every loop; records at most once per 5-minute slot (aligned to 09:15, 09:20, …)."""
    ts = int(now_ts or time.time())
    slot = ts // EVERY
    if _last.get("slot") == slot:
        return 0
    _last["slot"] = slot
    return snapshot(ts)
