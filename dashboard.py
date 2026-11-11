"""Read-only web dashboard for the Upstox index-options PAPER bot.  python dashboard.py → http://localhost:8002

It only reads public market data and the bot's own files (state/, logs/); it cannot place orders.
"""
import argparse
import csv
import json
import threading
import time
from datetime import datetime, timedelta
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import numpy as np
import pandas as pd

import engine
import market
from config import HERE, load_config

CFG = load_config()
_cache, _lock = {}, threading.Lock()
RESEARCH = HERE / "research" / "out"


def cached(key, ttl, fn):
    with _lock:
        hit = _cache.get(key)
        if hit and time.time() - hit[0] < ttl:
            return hit[1]
    val = fn()
    with _lock:
        _cache[key] = (time.time(), val)
    return val


def num(x, nd=2):
    try:
        x = float(x)
        return None if np.isnan(x) else round(x, nd)
    except (TypeError, ValueError):
        return None


def now_ist():
    return datetime.now(engine.IST)


def market_state():
    t = now_ist()
    hm = t.hour * 100 + t.minute
    if t.weekday() >= 5:
        return dict(open=False, text="Closed · weekend")
    if hm < 915:
        return dict(open=False, text="Pre-open · opens 09:15")
    if hm < 1530:
        return dict(open=True, text="Market open")
    return dict(open=False, text="Closed · opens 09:15 next trading day")


# ------------------------------------------------------------------ prices
def hist(key):
    return cached(f"hist|{key}", 3600, lambda: market.history(key))


def today(key):
    return cached(f"intra|{key}", 15, lambda: market.intraday(key))


def quote(name):
    key = market.VIX_KEY if name == "VIX" else market.INDICES[name]["key"]
    h, d = hist(key), today(key)
    t_today = now_ist().date()
    hd = engine.prepare(h.assign(), h[["time", "close"]].rename(columns={"close": "vix"}))
    prev_days = hd[hd["day"] < t_today]
    prev_close = float(prev_days["close"].iloc[-1]) if len(prev_days) else None
    if len(d):
        last, hi, lo, spark = float(d["close"].iloc[-1]), float(d["high"].max()), float(d["low"].min()), d["close"].tolist()
        asof = int(d["time"].iloc[-1]) + 300
    else:                                                  # market closed today: show the last session
        lastday = hd[hd["day"] == hd["day"].iloc[-1]]
        last, hi, lo, spark = float(lastday["close"].iloc[-1]), float(lastday["high"].max()), float(lastday["low"].min()), lastday["close"].tolist()
        asof = int(lastday["time"].iloc[-1]) + 300
        prev = hd[hd["day"] < hd["day"].iloc[-1]]
        prev_close = float(prev["close"].iloc[-1]) if len(prev) else None
    out = dict(name=name, label="India VIX" if name == "VIX" else market.INDICES[name]["label"], last=num(last),
               chg=num(100 * (last / prev_close - 1)) if prev_close else None, high=num(hi), low=num(lo),
               spark=[num(x) for x in spark[-75:]], asof=asof)
    if name != "VIX":
        info = market.INDICES[name]
        out.update(lot=info["lot"], step=info["step"], exchange=info["exchange"])
        try:
            exps = [e for e in engine.expiries(name) if e >= t_today]
            out["expiry"] = exps[0].isoformat() if exps else None
            out["weekly"] = name in ("NIFTY", "SENSEX")
        except Exception:
            out["expiry"] = None
    return out


def quotes():
    return cached("quotes", 15, lambda: [quote(n) for n in list(CFG.indices) + ["VIX"]])


# ------------------------------------------------------------------ chart
TF_MIN = {"5m": 5, "15m": 15, "1h": 60, "1D": "D"}
RANGE_DAYS = {"1D": 1, "1W": 7, "1M": 31, "3M": 92, "1Y": 366, "ALL": 2000}


def chart(name, tf="15m", rng="1M"):
    def load():
        key = market.INDICES[name]["key"]
        df = pd.concat([hist(key), today(key)]).drop_duplicates("time", keep="last").sort_values("time")
        df = engine.prepare(df.reset_index(drop=True), df[["time"]].assign(vix=0.0))
        days = sorted(df["day"].unique())
        keep = set(days[-RANGE_DAYS.get(rng, 31):]) if rng in ("1D", "1W") else None
        if keep is None:
            cut = days[-1] - timedelta(days=RANGE_DAYS.get(rng, 31))
            df = df[df["day"] >= cut]
        else:
            df = df[df["day"].isin(keep)]
        bars = df[["time", "open", "high", "low", "close"]] if tf == "5m" else \
            market.resample(df[["time", "open", "high", "low", "close"]], TF_MIN.get(tf, 15))
        c = bars["close"]
        e20, e50 = engine.ema(c, 20), engine.ema(c, 50)
        return dict(name=name, tf=tf, range=rng,
                    candles=[dict(time=int(r.time), open=num(r.open), high=num(r.high), low=num(r.low), close=num(r.close))
                             for r in bars.itertuples()],
                    ema20=[dict(time=int(t), value=num(v)) for t, v in zip(bars["time"], e20)],
                    ema50=[dict(time=int(t), value=num(v)) for t, v in zip(bars["time"], e50)])
    return cached(f"chart|{name}|{tf}|{rng}", 20, load)


# ------------------------------------------------------------------ paper journal
def read_csv(path):
    if not path.exists():
        return []
    with path.open() as f:
        return list(csv.DictReader(f))


def paper():
    out = []
    for b in CFG.books:
        rows = read_csv(HERE / "logs" / f"paper_trades_{b['id']}.csv")
        closed = []
        for r in rows:
            r = {k: (num(v) if k in ("t0", "t1", "spot0", "spot1", "sl", "tp", "prem0", "prem1", "credit", "lot",
                                    "charges", "pnl", "strike", "vix") and v not in ("", None) else v) for k, v in r.items()}
            closed.append(r)
        live = {}
        try:
            live = json.loads((HERE / "state" / f"live_{b['id']}.json").read_text())
        except Exception:
            pass
        open_ = [t for t in live.get("trades", []) if t.get("result") == "open"] if live.get("day") == now_ist().date().isoformat() else []
        today_pnl = sum(t["pnl"] for t in live.get("trades", [])) if live.get("day") == now_ist().date().isoformat() else 0
        pnl = [float(r["pnl"]) for r in closed]
        wins = sum(p > 0 for p in pnl)
        out.append(dict(id=b["id"], label=b["label"], index=b["index"], family=b["family"], params=b["params"],
                        closed=closed[::-1], open=open_, today_pnl=num(today_pnl), live_updated=live.get("updated"),
                        n=len(pnl), wins=wins, win_rate=num(100 * wins / len(pnl), 1) if pnl else None,
                        net=num(sum(pnl)), equity=num(CFG.capital + sum(pnl)), capital=CFG.capital))
    return out


# ------------------------------------------------------------------ strategy history (backtest every book on all data)
_hist = dict(state="idle", done=0, total=0, books={}, updated=None, error=None)


def _hist_book(b):
    df = engine.load(b["index"])
    if b["family"] == "straddle":
        trades = engine.simulate_straddle(df, b["index"], b["params"], lots=CFG.lots)
    else:
        trades = engine.simulate_buy(df, b["index"], engine.SIGNALS[b["family"]](df, b["params"]), b["params"], lots=CFG.lots)
    st = engine.stats(trades, CFG.capital)
    years = {}
    eq = CFG.capital
    for t in trades:
        eq += t["pnl"]
        t["eq"] = round(eq, 0)
        y = datetime.fromtimestamp(t["t0"], engine.IST).year
        d = years.setdefault(str(y), dict(n=0, w=0, pnl=0.0))
        d["n"] += 1
        d["w"] += t["pnl"] > 0
        d["pnl"] += t["pnl"]
    for t in trades:
        t.pop("legs", None)
    return dict(id=b["id"], label=b["label"], index=b["index"], family=b["family"], params=b["params"],
                stats={k: (float(v) if v is not None else None) for k, v in st.items()},
                years={k: dict(n=v["n"], w=v["w"], pnl=round(v["pnl"], 0)) for k, v in years.items()},
                first=trades[0]["t0"] if trades else None, last=trades[-1]["t1"] if trades else None,
                trades=[{k: (float(v) if isinstance(v, (np.floating, np.integer)) else v) for k, v in t.items()} for t in trades])


def _hist_run():
    with _lock:
        _hist.update(state="running", done=0, total=len(CFG.books), error=None)
    for b in CFG.books:
        try:
            res = _hist_book(b)
            with _lock:
                _hist["books"][b["id"]] = res
        except Exception as e:
            with _lock:
                _hist["error"] = f"{b['id']}: {str(e)[:200]}"
        with _lock:
            _hist["done"] += 1
    with _lock:
        _hist.update(state="ready", updated=int(time.time()))


def strategy_history():
    with _lock:
        stale = _hist["state"] == "idle" or (_hist["state"] == "ready" and time.time() - (_hist["updated"] or 0) > 6 * 3600)
        if stale:
            _hist["state"] = "running"
    if stale:
        threading.Thread(target=_hist_run, daemon=True).start()
    with _lock:
        return dict(state=_hist["state"], done=_hist["done"], total=_hist["total"] or len(CFG.books), error=_hist["error"],
                    updated=_hist["updated"], capital=CFG.capital, books=list(_hist["books"].values()))


def research():
    try:
        best = json.loads((RESEARCH / "best.json").read_text())
    except Exception:
        best = {}
    calib = {}
    try:
        e = pd.read_csv(RESEARCH / "calib_err.csv")
        e["a"] = e["err"].abs()
        calib = {k: dict(n=int(len(g)), median_abs=round(float(g["a"].median()) * 100, 1)) for k, g in e.groupby("index")}
    except Exception:
        pass
    return dict(best=best, calib=calib, split="2025-01-01")


def overview():
    return dict(mode="PAPER", market=market_state(), indices=list(CFG.indices), capital=CFG.capital, lots=CFG.lots,
                books=[dict(id=b["id"], label=b["label"], index=b["index"], family=b["family"], params=b["params"],
                            note=b.get("note", "")) for b in CFG.books],
                now=int(time.time()))


# ------------------------------------------------------------------ http
class Handler(BaseHTTPRequestHandler):
    def _send(self, code, body, ctype):
        data = body.encode() if isinstance(body, str) else body
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        u = urlparse(self.path)
        q = {k: v[0] for k, v in parse_qs(u.query).items()}
        try:
            if u.path in ("/", "/index.html"):
                return self._send(200, (HERE / "dashboard.html").read_text(), "text/html; charset=utf-8")
            routes = {"/api/overview": overview, "/api/quotes": quotes, "/api/paper": paper,
                      "/api/history": strategy_history, "/api/research": research,
                      "/api/chart": lambda: chart(q.get("index", CFG.indices[0]), q.get("tf", "15m"), q.get("range", "1M"))}
            if u.path in routes:
                return self._send(200, json.dumps(routes[u.path](), default=str), "application/json")
            self._send(404, "not found", "text/plain")
        except Exception as e:
            self._send(500, json.dumps(dict(error=str(e)[:300])), "application/json")

    def log_message(self, *a):
        pass


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=8002)
    a = ap.parse_args()
    strategy_history()
    print(f"Upstox paper dashboard → http://localhost:{a.port}  (read-only)")
    ThreadingHTTPServer((a.host, a.port), Handler).serve_forever()


if __name__ == "__main__":
    main()
