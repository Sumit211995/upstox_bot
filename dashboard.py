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
import live
import news
import swing_bot
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
    if hm < 1515:
        return dict(open=True, text="Market open")
    if hm < 1530:                                          # closing auction (from Aug 2026): indices stop updating
        return dict(open=True, auction=True, text="Closing auction · index paused at 15:15, options still trade")
    return dict(open=False, text="Closed · opens 09:15 next trading day")


# ------------------------------------------------------------------ prices
def hist(key):
    return cached(f"hist|{key}", 3600, lambda: market.history(key))


def today(key):
    return cached(f"intra|{key}", 5, lambda: market.intraday(key))       # 5 s: the live candle needs fresh buckets


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
               spark=[num(x) for x in spark[-75:]], asof=asof, prev_close=num(prev_close))
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


# ------------------------------------------------------------------ live ticks (polled every 2 s like delta_bot)
def live_ticks(name, tf):
    """Latest price of every index + VIX and the chart's still-forming candle. With the Upstox token the price is the
    real last traded value (1 s cache); without it, the latest 1-minute candle (≈ 1 minute behind)."""
    names = list(CFG.indices) + ["VIX"]
    keys = {n: market.VIX_KEY if n == "VIX" else market.INDICES[n]["key"] for n in names}
    out = dict(server_time=time.time(), source="estimate", ticks={}, forming=None, market=market_state())
    q = live.quotes(list(keys.values())) if out["market"]["open"] else None
    if q:
        out["source"] = "upstox"
    base = {x["name"]: x for x in quotes()}                  # previous close / sparkline (15 s cache)
    for n, k in keys.items():
        b = base.get(n) or {}
        x = (q or {}).get(k)
        if x is None and out["market"]["open"]:
            try:
                m1 = cached(f"m1|{k}", 5, lambda k=k: market.intraday(k, n=1))
                x = dict(last=float(m1["close"].iloc[-1]), high=float(m1["high"].max()), low=float(m1["low"].min())) if len(m1) else None
            except Exception:
                x = None
        if x is None:
            x = dict(last=b.get("last"), high=b.get("high"), low=b.get("low"))
        last, prev = x.get("last"), b.get("prev_close")
        out["ticks"][n] = dict(last=num(last), high=num(x.get("high") or b.get("high")), low=num(x.get("low") or b.get("low")),
                               chg=num(100 * (last / prev - 1)) if last and prev else b.get("chg"))
    if name in CFG.indices and tf in TF_MIN and out["market"]["open"]:
        try:
            d = today(market.INDICES[name]["key"])
            if len(d):
                bars = d[["time", "open", "high", "low", "close"]] if tf == "5m" else market.resample(d[["time", "open", "high", "low", "close"]], TF_MIN[tf])
                r = bars.iloc[-1]
                last = out["ticks"][name]["last"] or float(r.close)
                out["forming"] = dict(time=int(r.time), open=num(r.open), high=num(max(r.high, last)), low=num(min(r.low, last)), close=num(last))
        except Exception:
            pass
    return out


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


# ------------------------------------------------------------------ option BUYING books (evening job output)
def _jread(path, default):
    try:
        return json.loads(Path(path).read_text())
    except Exception:
        return default


def buying():
    """Option-buying: combined paper accounts (state/buy_<id>.json, live estimate for open positions during market
    hours), every strategy's backtest (research/out/buy_history.json) and tonight's signals (state/buy_signals.json)."""
    def load():
        hist = _jread(RESEARCH / "buy_history.json", {})
        strats, accounts = swing_bot.load_books()
        sig = _jread(HERE / "state" / "buy_signals.json", {})
        live_on = market_state()["open"]
        accts = []
        for ac in accounts:
            st = _jread(HERE / "state" / f"buy_{ac['id']}.json", {})
            tr = st.get("trades", [])
            open_ = [t for t in tr if t.get("reason") == "open"]
            if live_on:
                for t in open_:
                    try:
                        t["live"] = live.mark(t, st.get("asof"), t.get("ref_spot"))
                    except Exception:
                        t["live"] = None
            closed = [t for t in tr if t.get("reason") != "open"]
            h = hist.get("accounts", {}).get(ac["id"], {})
            accts.append(dict(ac, equity=st.get("equity", ac["capital"]), cash=st.get("cash"), asof=st.get("asof"),
                              plan=st.get("plan") or [], open=open_, closed=closed[::-1], n=len(closed),
                              wins=sum(t["pnl"] > 0 for t in closed), net=round(sum(t["pnl"] for t in closed), 0),
                              live_pnl=round(sum((t.get("live") or {}).get("pnl", 0) for t in open_), 0) if live_on else None,
                              bt={k: h.get(k) for k in ("train", "test", "all", "rolling", "years")}, bt_trades=h.get("trades", [])))
        ss = []
        for x in strats:
            h = hist.get("strategies", {}).get(x["id"], {})
            ss.append(dict(id=x["id"], label=x["label"], note=x.get("note", ""), params=x["params"], combined=x.get("combined", True),
                           syms=x["syms"] if len(x["syms"]) < 5 else ["20 stocks"], train=h.get("train"), test=h.get("test"),
                           years=h.get("years", {}), trades=h.get("trades", [])))
        return dict(accounts=accts, strategies=ss, signals=sig.get("signals", []), asof=sig.get("asof"),
                    families=hist.get("families", {}), split=hist.get("split"), live=live_on)
    return cached("buying", 20, load)


def scanner():
    return cached("scan", 30, lambda: _jread(HERE / "state" / "oi_scan.json", {}))


def headlines(sym):
    sym = sym if sym in news.QUERY or sym == "_market" else "_market"
    h = news.headlines(sym)
    return dict(sym=sym, items=h, mood=dict(score=sum(x["mood"] for x in h),
                                            label=("positive" if sum(x["mood"] for x in h) >= 2 else
                                                   "negative" if sum(x["mood"] for x in h) <= -2 else "neutral")))


def bigmoney():
    def load():
        d = _jread(HERE / "state" / "bigmoney.json", {})
        h = _jread(RESEARCH / "buy_history.json", {})
        t = news.tone()
        return dict(d, tone=dict(last=t.get("last"), rank=t.get("rank"), series=(t.get("series") or [])[-60:]),
                    research=h.get("participants", []), news_research=h.get("news", {}))
    return cached("bigmoney", 60, load)


def channel_news(src):
    items = news.channel(src if src in news.CHANNELS else "all")
    sc = sum(x["mood"] for x in items)
    return dict(src=src, items=items, channels={k: v[0] for k, v in news.CHANNELS.items()}, live_tv=news.LIVE_TV,
                mood=dict(score=sc, label="positive" if sc >= 3 else "negative" if sc <= -3 else "neutral"))


def overview():
    return dict(mode="PAPER", market=market_state(), indices=list(CFG.indices), capital=CFG.capital, lots=CFG.lots,
                books=[dict(id=b["id"], label=b["label"], index=b["index"], family=b["family"], params=b["params"],
                            note=b.get("note", "")) for b in CFG.books],
                now=int(time.time()))


# ------------------------------------------------------------------ http
# Read-only page: lock it down. Only our own scripts + the chart library, Google fonts, and YouTube-nocookie frames.
SECURITY_HEADERS = {
    "Content-Security-Policy": "default-src 'self'; script-src 'self' 'unsafe-inline' https://unpkg.com; "
                               "style-src 'self' 'unsafe-inline' https://fonts.googleapis.com; font-src https://fonts.gstatic.com; "
                               "img-src 'self' data:; connect-src 'self'; frame-src https://www.youtube-nocookie.com; "
                               "frame-ancestors 'none'; base-uri 'none'; form-action 'none'",
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
    "Referrer-Policy": "strict-origin-when-cross-origin",
    "Permissions-Policy": "camera=(), microphone=(), geolocation=()",
}
ALLOWED_PARAMS = {"index", "tf", "range", "sym", "src"}


class Handler(BaseHTTPRequestHandler):
    server_version = "dashboard"
    sys_version = ""
    def _send(self, code, body, ctype):
        data = body.encode() if isinstance(body, str) else body
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        for k, v in SECURITY_HEADERS.items():
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        u = urlparse(self.path)
        q = {k: v[0][:40] for k, v in parse_qs(u.query).items() if k in ALLOWED_PARAMS}   # ignore anything else
        try:
            if u.path in ("/", "/index.html"):
                return self._send(200, (HERE / "dashboard.html").read_text(), "text/html; charset=utf-8")
            routes = {"/api/live": lambda: live_ticks(q.get("index", ""), q.get("tf", "")),
                      "/api/overview": overview, "/api/quotes": quotes, "/api/paper": paper,
                      "/api/history": strategy_history, "/api/research": research,
                      "/api/buying": buying, "/api/scan": scanner, "/api/bigmoney": bigmoney,
                      "/api/channels": lambda: channel_news(q.get("src", "all")),
                      "/api/livetv": lambda: cached("livetv", 300, news.live_tv),
                      "/api/news": lambda: headlines(q.get("sym", "_market")),
                      "/api/chart": lambda: chart(q.get("index") if q.get("index") in CFG.indices else CFG.indices[0],
                                                  q.get("tf") if q.get("tf") in TF_MIN else "15m",
                                                  q.get("range") if q.get("range") in RANGE_DAYS else "1M")}
            if u.path in routes:
                return self._send(200, json.dumps(routes[u.path](), default=str), "application/json")
            self._send(404, "not found", "text/plain")
        except Exception as e:                            # no stack traces / paths to the browser
            self._send(500, json.dumps(dict(error=type(e).__name__)), "application/json")

    def do_POST(self):                                    # read-only: nothing can be changed through the page
        self._send(405, "read-only", "text/plain")

    do_PUT = do_DELETE = do_PATCH = do_POST

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
