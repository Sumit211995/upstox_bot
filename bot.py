"""Upstox index-options PAPER bot (no orders, no login needed).

Every POLL_SECONDS during market hours it rebuilds today's 5-minute candles (free Upstox public API) and replays
each strategy "book" with the SAME simulator used for the backtest. New trades / exits are sent to Telegram and
saved to logs/paper_trades_<book>.csv; the live view (open trades + P&L) goes to state/live_<book>.json.

Run:  python bot.py            (loop)
      python bot.py --once     (one pass, then exit)
      python bot.py --status   (paper results so far)
"""
import argparse
import csv
import json
import logging
import time
from datetime import datetime, timedelta

import pandas as pd

import engine
import market
from config import HERE, load_config
from notifier import send

log = logging.getLogger("bot")
STATE, LOGS = HERE / "state", HERE / "logs"
WARMUP_DAYS = 40                      # history kept in front of today so indicators are warmed up
CSV_COLS = ["day", "index", "book", "side", "strike", "kind", "expiry", "t0", "t1", "spot0", "spot1", "sl", "tp",
            "prem0", "prem1", "credit", "lot", "charges", "pnl", "result", "stops", "why", "vix"]


def now_ist():
    return datetime.now(engine.IST)


def market_open(t=None):
    t = t or now_ist()
    hm = t.hour * 100 + t.minute
    return t.weekday() < 5 and 914 <= hm <= 1535


# ------------------------------------------------------------------ data for today (+ warm-up history)
_hist_cache = {}


def frame(index, now_ts):
    """History (cached, refreshed once a day) + today's COMPLETED 5-minute candles, with VIX."""
    key = market.INDICES[index]["key"]
    today = datetime.fromtimestamp(now_ts, engine.IST).date()
    if _hist_cache.get(index, (None,))[0] != today:
        h = market.history(key)
        v = market.history(market.VIX_KEY)
        cut = now_ts - WARMUP_DAYS * 86400
        _hist_cache[index] = (today, h[h["time"] >= cut], v[v["time"] >= cut])
    _, h, v = _hist_cache[index]
    di, dv = market.intraday(key), market.intraday(market.VIX_KEY)
    di = di[di["time"] + 300 <= now_ts]                     # completed candles only (no peeking at a forming bar)
    dv = dv[dv["time"] + 300 <= now_ts]
    df = pd.concat([h, di]).drop_duplicates("time", keep="last").sort_values("time")
    vx = pd.concat([v, dv]).drop_duplicates("time", keep="last")[["time", "close"]].rename(columns={"close": "vix"})
    return engine.prepare(df.reset_index(drop=True), vx), len(di)


def run_book(book, df, today, lots):
    """Today's trades for one book, replayed with the backtest simulator (live=True keeps open trades)."""
    p = book["params"]
    if book["family"] == "straddle":
        trades = engine.simulate_straddle(df[df["day"] == today], book["index"], p, lots=lots, live=True)
    else:
        sig = engine.SIGNALS[book["family"]](df, p)
        trades = engine.simulate_buy(df, book["index"], sig, p, lots=lots, live=True)
    return [t for t in trades if datetime.fromtimestamp(t["t0"], engine.IST).date() == today]


# ------------------------------------------------------------------ state, journal, alerts
def _read(path, default):
    try:
        return json.loads(path.read_text())
    except Exception:
        return default


def journal_path(book_id):
    return LOGS / f"paper_trades_{book_id}.csv"


def append_trade(book, t, day):
    path = journal_path(book["id"])
    path.parent.mkdir(exist_ok=True)
    new = not path.exists()
    with path.open("a", newline="") as f:
        w = csv.DictWriter(f, fieldnames=CSV_COLS, extrasaction="ignore")
        if new:
            w.writeheader()
        w.writerow({**t, "day": day.isoformat(), "book": book["id"]})


def fmt_inr(x):
    return f"₹{x:,.0f}" if x >= 0 else f"-₹{-x:,.0f}"


def describe(book, t):
    ix = market.INDICES[book["index"]]["label"]
    if book["family"] == "straddle":
        legs = " · ".join(f"{l['side']} {l['strike']:.0f}{l['kind']} @{l['entry']:.1f}" for l in t.get("legs", []))
        return f"{ix} {t['side']} (exp {t['expiry']}) · {legs} · credit {fmt_inr(t['credit'])}"
    return (f"{ix} {t['side']} {t['strike']:.0f}{t['kind']} exp {t['expiry']} @₹{t['prem0']:.1f} × {t['lot']} · "
            f"index {t['spot0']:.1f}, stop {t['sl']:.1f}" + (f", target {t['tp']:.1f}" if t.get("tp") else ""))


def process(cfg, book, trades, today):
    """Compare with what we knew last poll: alert new entries / exits, write closed trades to the journal."""
    sp = STATE / f"paper_{book['id']}.json"
    st = _read(sp, {})
    if st.get("day") != today.isoformat():
        st = {"day": today.isoformat(), "seen": {}}
    for t in trades:
        k = str(t["t0"])
        before = st["seen"].get(k)
        if before is None:
            send(cfg, f"🇮🇳 PAPER ENTRY · {book['label']}\n{describe(book, t)}\nwhy: {t.get('why', '')}")
        if t["result"] != "open" and before != "closed":
            append_trade(book, t, today)
            send(cfg, f"🇮🇳 PAPER EXIT · {book['label']} · {t['result']}\n{describe(book, t)}\n"
                      f"P&L {fmt_inr(t['pnl'])} (after ₹{t['charges']:.0f} charges)")
            st["seen"][k] = "closed"
        elif t["result"] == "open":
            st["seen"][k] = "open"
    STATE.mkdir(exist_ok=True)
    sp.write_text(json.dumps(st))
    (STATE / f"live_{book['id']}.json").write_text(json.dumps(
        dict(day=today.isoformat(), updated=int(time.time()), trades=trades), default=float))


def poll(cfg):
    now = now_ist()
    ts, today = int(now.timestamp()), now.date()
    frames, out = {}, {}
    for book in cfg.books:
        ix = book["index"]
        if ix not in frames:
            frames[ix] = frame(ix, ts)
        df, n_today = frames[ix]
        if not n_today:
            continue                                       # no candles yet today (pre-open or holiday)
        trades = run_book(book, df, today, cfg.lots)
        process(cfg, book, trades, today)
        out[book["id"]] = trades
    return out


def day_summary(cfg, today):
    lines = []
    for book in cfg.books:
        live = _read(STATE / f"live_{book['id']}.json", {})
        if live.get("day") != today.isoformat():
            continue
        pnl = sum(t["pnl"] for t in live.get("trades", []))
        lines.append(f"{book['label']}: {len(live.get('trades', []))} trade(s), {fmt_inr(pnl)}")
    if lines:
        send(cfg, "🇮🇳 PAPER day summary " + today.strftime("%d %b") + "\n" + "\n".join(lines))


def status(cfg):
    for book in cfg.books:
        path = journal_path(book["id"])
        rows = list(csv.DictReader(path.open())) if path.exists() else []
        pnl = sum(float(r["pnl"]) for r in rows)
        wins = sum(float(r["pnl"]) > 0 for r in rows)
        print(f"{book['label']:<40} trades {len(rows):>4}  wins {wins:>4}  P&L {fmt_inr(pnl)}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--once", action="store_true")
    ap.add_argument("--status", action="store_true")
    a = ap.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    cfg = load_config()
    if not cfg.books:
        raise SystemExit("No strategy books configured (books.json)")
    if a.status:
        return status(cfg)
    if a.once:
        res = poll(cfg)
        for k, v in res.items():
            print(k, [(t["side"], t["result"], t["pnl"]) for t in v])
        return
    send(cfg, "🇮🇳 Upstox paper bot started · " + ", ".join(b["label"] for b in cfg.books) + " · no real orders")
    summary_sent, errors = None, 0
    while True:
        try:
            now = now_ist()
            if market_open(now):
                poll(cfg)
                errors = 0
            elif now.weekday() < 5 and now.hour * 100 + now.minute > 1535 and summary_sent != now.date():
                day_summary(cfg, now.date())
                summary_sent = now.date()
            time.sleep(cfg.poll_seconds if market_open(now) else 60)
        except KeyboardInterrupt:
            send(cfg, "🇮🇳 Upstox paper bot stopped")
            break
        except Exception as e:                                # keep running; tell the phone if it keeps failing
            errors += 1
            log.exception("poll failed")
            if errors in (3, 30):
                send(cfg, f"🇮🇳 Upstox paper bot error ({errors}x): {str(e)[:300]}")
            time.sleep(30)


if __name__ == "__main__":
    main()
