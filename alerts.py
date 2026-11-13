"""Live market-hours alerts for the BUYING side (the selling books already alert live from bot.py).

* 09:15 — today's buy calls: the evening plan of every paper account (bought at today's open).
* Every minute — open buy positions are priced live (real option prices with the Upstox token, an estimate without);
  when one reaches its stop or target an alert is sent at once. The paper account itself is settled in the evening
  on NSE's official prices; the alert is the "act now" call for anyone trading it for real.
Alerts already sent are remembered in state/alerts_<day>.json, so a restart never repeats them.
Other modules (e.g. the stocks tab) can add their own positions through `watch()`.
"""
import json
import logging
import time

from config import HERE

log = logging.getLogger("alerts")
STATE = HERE / "state"
_last = {}


def _path(day):
    return STATE / f"alerts_{day.isoformat()}.json"


def _seen(day):
    try:
        return set(json.loads(_path(day).read_text()))
    except Exception:
        return set()


def _remember(day, seen):
    STATE.mkdir(exist_ok=True)
    _path(day).write_text(json.dumps(sorted(seen)))


def _contract(c):
    leg = f"{c['sym']} {c['strike']:g} {c['kind']}"
    if c.get("strike2"):
        leg += f" / SELL {c['strike2']:g} {c['kind']} (spread)"
    return leg + f" exp {c.get('expiry', '')}"


def morning_calls(send, today, accounts):
    """accounts: [(label, state dict)]. Sends today's planned buys once. Returns the message or None."""
    seen = _seen(today)
    if "morning" in seen:
        return None
    lines, done = [], set()
    for label, st in accounts:
        for c in st.get("plan") or []:
            key = (c["sym"], c["kind"], c.get("strike"), c.get("strike2"))
            if key in done:
                continue
            done.add(key)
            lines.append(f"• BUY {_contract(c)} at the open (ref ₹{c.get('price')}) · stop ₹{c.get('stop')}"
                         + (f" · target ₹{c['target']}" if c.get("target") else "") + f"\n  {c.get('strategy_label', '')} · {label}")
    seen.add("morning")
    _remember(today, seen)
    if not lines:
        return None
    msg = "🔔 LIVE CALL · today's option buys (paper)\n" + "\n".join(lines)
    send(msg)
    return msg


def watch(send, today, positions):
    """positions: [dict(key, title, price, stop, target, hit_stop, hit_target, pnl, source)] priced live.
    Sends one alert per position per level. Returns the messages sent."""
    seen, out = _seen(today), []
    for p in positions:
        for level, hit, icon, word in (("stop", p.get("hit_stop"), "🛑", "STOP hit → exit"),
                                       ("target", p.get("hit_target"), "🎯", "TARGET hit → book profit")):
            k = f"{p['key']}|{level}"
            if hit and k not in seen:
                seen.add(k)
                msg = (f"{icon} LIVE CALL · {word}\n{p['title']}\nnow ₹{p.get('price')} · stop ₹{p.get('stop')}"
                       + (f" · target ₹{p['target']}" if p.get("target") else "")
                       + (f" · P&L ₹{p['pnl']:,.0f}" if p.get("pnl") is not None else "")
                       + (" · live price" if p.get("source") == "live" else " · estimated price"))
                send(msg)
                out.append(msg)
    if out:
        _remember(today, seen)
    return out


def option_positions(accounts, mark):
    """Open option-buying trades of every account, priced with live.mark (one entry per distinct position)."""
    out, done = [], set()
    for label, st in accounts:
        for t in st.get("trades", []):
            if t.get("reason") != "open":
                continue
            key = f"opt|{t['sym']}|{t['kind']}|{t['strike']}|{t.get('strike2')}|{t['entry_day']}"
            if key in done:
                continue
            done.add(key)
            try:
                m = mark(t, st.get("asof"), t.get("ref_spot"))
            except Exception as e:
                log.warning("live mark failed for %s: %s", key, e)
                m = None
            if not m:
                continue
            out.append(dict(key=key, title=f"{_contract(t)} · {label}", price=m.get("price"), stop=t.get("stop"),
                            target=t.get("target"), hit_stop=m.get("hit_stop"), hit_target=m.get("hit_target"),
                            pnl=m.get("pnl"), source=m.get("source")))
    return out


def every(name, seconds):
    """True at most once per `seconds` for this name (simple in-process throttle)."""
    now = time.time()
    if now - _last.get(name, 0) < seconds:
        return False
    _last[name] = now
    return True
