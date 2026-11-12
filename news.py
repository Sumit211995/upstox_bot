"""Live market news from free RSS feeds (no key), with a simple keyword mood score.

Sources: Google News (search per index / stock, India edition) and Economic Times Markets. The mood score is a
plain word count (positive words − negative words in the headline); it is shown next to each signal as context.
It is NOT part of the tested strategy rules: there is no free archive of past headlines to backtest it on.
"""
import re
import time
import xml.etree.ElementTree as ET
from email.utils import parsedate_to_datetime
from urllib.parse import quote_plus

import market

FEEDS = {"_market": ["https://economictimes.indiatimes.com/markets/rssfeeds/1977021501.cms",
                     "https://news.google.com/rss/search?q=Nifty+OR+Sensex+stock+market+India+when:1d&hl=en-IN&gl=IN&ceid=IN:en"]}
QUERY = {"NIFTY": "Nifty 50", "BANKNIFTY": "Bank Nifty", "MIDCPNIFTY": "Nifty Midcap", "FINNIFTY": "Nifty Financial",
         "SENSEX": "Sensex", "M&M": "Mahindra and Mahindra", "LT": "Larsen Toubro", "ITC": "ITC share",
         "SBIN": "SBI share", "HINDUNILVR": "Hindustan Unilever", "BAJFINANCE": "Bajaj Finance",
         "BHARTIARTL": "Bharti Airtel", "KOTAKBANK": "Kotak Mahindra Bank", "ADANIENT": "Adani Enterprises",
         "SUNPHARMA": "Sun Pharma", "TATASTEEL": "Tata Steel", "HCLTECH": "HCLTech", "AXISBANK": "Axis Bank",
         "ICICIBANK": "ICICI Bank", "HDFCBANK": "HDFC Bank", "RELIANCE": "Reliance Industries", "INFY": "Infosys",
         "TCS": "TCS share", "MARUTI": "Maruti Suzuki", "TITAN": "Titan share"}

# words a headline must contain to count as news about that symbol (first word of QUERY otherwise)
ALIASES = {"LT": ["larsen", "l&t"], "M&M": ["mahindra", "m&m"], "BAJFINANCE": ["bajaj finance"], "HINDUNILVR": ["hindustan unilever", "hul"],
           "BHARTIARTL": ["airtel"], "KOTAKBANK": ["kotak"], "HCLTECH": ["hcl"], "SUNPHARMA": ["sun pharma"],
           "TATASTEEL": ["tata steel"], "ADANIENT": ["adani"], "BANKNIFTY": ["bank nifty", "banknifty", "bank stocks"],
           "MIDCPNIFTY": ["midcap"], "NIFTY": ["nifty", "sensex"], "SENSEX": ["sensex", "nifty"]}

POS = {"surge", "surges", "rally", "rallies", "jump", "jumps", "gain", "gains", "soar", "soars", "rise", "rises",
       "record", "high", "beat", "beats", "upgrade", "upgrades", "buy", "bullish", "profit", "growth", "strong",
       "outperform", "positive", "boost", "boosts", "wins", "win", "order", "orders", "approval", "recovers",
       "rebound", "rebounds", "upbeat", "higher", "climbs", "climb", "advance", "advances"}
NEG = {"fall", "falls", "drop", "drops", "slump", "slumps", "crash", "crashes", "plunge", "plunges", "decline",
       "declines", "loss", "losses", "miss", "misses", "downgrade", "downgrades", "sell", "bearish", "weak", "cut",
       "cuts", "probe", "penalty", "fraud", "raid", "lower", "slips", "slip", "tumble", "tumbles", "sinks", "sink",
       "concern", "concerns", "fears", "fear", "selloff", "sell-off", "ban", "default", "resigns", "negative"}

# Business news channels with free RSS feeds (checked Nov 2026). Zee Business / Financial Express block or break RSS.
CHANNELS = {
    "et": ("Economic Times", "https://economictimes.indiatimes.com/markets/rssfeeds/1977021501.cms"),
    "mc": ("Moneycontrol", "https://www.moneycontrol.com/rss/latestnews.xml"),
    "bs": ("Business Standard", "https://www.business-standard.com/rss/markets-106.rss"),
    "mint": ("Livemint", "https://www.livemint.com/rss/markets"),
    "cnbc": ("CNBC-TV18", "https://www.cnbctv18.com/commonfeeds/v1/cne/rss/market.xml"),
    "ndtv": ("NDTV Profit", "https://news.google.com/rss/search?q=site:ndtvprofit.com+(Sensex+OR+Nifty+OR+shares+OR+stocks+OR+market)+when:2d&hl=en-IN&gl=IN&ceid=IN:en"),
    "bl": ("BusinessLine", "https://www.thehindubusinessline.com/markets/feeder/default.rss"),
}
# Live business TV on YouTube (embed works only while the channel is live; the page also links to YouTube)
LIVE_TV = [dict(name="CNBC-TV18", channel="UCmRbHAgG2k2vDUvb3xsEunQ", handle="CNBC-TV18"),
           dict(name="ET Now", channel="UCI_mwTKUhicNzFrhm33MzBQ", handle="ETNOW"),
           dict(name="NDTV Profit", channel="UCZFMm1mMw0F81Z37aaEzTUA", handle="NDTVProfitIndia"),
           dict(name="Zee Business", channel="UCkXopQ3ubd-rnXnStZqCl2w", handle="ZeeBusiness"),
           dict(name="CNBC Awaaz", channel="UCQIycDaLsBpMKjOCeaKUYVg", handle="CNBCAwaaz")]

_cache = {}


def score(title):
    w = re.findall(r"[a-z\-]+", title.lower())
    return sum(x in POS for x in w) - sum(x in NEG for x in w)


def _fetch(url):
    r = market._session.get(url, timeout=12)
    r.raise_for_status()
    out = []
    for it in ET.fromstring(r.content).iter("item"):
        title = (it.findtext("title") or "").strip()
        if not title:
            continue
        try:
            ts = int(parsedate_to_datetime(it.findtext("pubDate")).timestamp())
        except Exception:
            ts = None
        src = it.find("source")
        link = (it.findtext("link") or "").strip()
        if not link.startswith(("https://", "http://")):    # never pass javascript: / data: links to the page
            link = ""
        out.append(dict(title=title[:300], link=link, ts=ts,
                        source=(src.text if src is not None else url.split("/")[2]), mood=score(title)))
    return out


def headlines(sym="_market", limit=12, ttl=600):
    """Latest headlines for an index / stock symbol (or the whole market), newest first. Cached 10 minutes."""
    hit = _cache.get(sym)
    if hit and time.time() - hit[0] < ttl:
        return hit[1]
    urls = FEEDS.get(sym) or [f"https://news.google.com/rss/search?q={quote_plus(QUERY.get(sym, sym) + ' share price when:3d')}"
                              f"&hl=en-IN&gl=IN&ceid=IN:en"]
    items = []
    for u in urls:
        try:
            items += _fetch(u)
        except Exception:
            continue
    seen, out = set(), []
    for x in sorted(items, key=lambda x: -(x["ts"] or 0)):
        k = x["title"][:80].lower()
        if k not in seen:
            seen.add(k)
            out.append(x)
    if sym != "_market":                                   # drop headlines that only matched loosely
        keys = ALIASES.get(sym) or [QUERY.get(sym, sym).split()[0].lower()]
        rel = [x for x in out if any(k in x["title"].lower() for k in keys)]
        out = rel if len(rel) >= 3 else out
    out = out[:limit]
    _cache[sym] = (time.time(), out)
    return out


def mood(sym):
    """Net mood of the latest headlines: sum of scores, plus a label."""
    h = headlines(sym)
    s = sum(x["mood"] for x in h)
    return dict(score=s, label="positive" if s >= 2 else "negative" if s <= -2 else "neutral", n=len(h))


def channel(key, limit=25, ttl=300):
    """Latest headlines from one business channel ('all' = every channel merged, newest first). Cached 5 minutes."""
    ck = "ch|" + key
    hit = _cache.get(ck)
    if hit and time.time() - hit[0] < ttl:
        return hit[1]
    keys = list(CHANNELS) if key == "all" else [key]
    items = []
    for k in keys:
        if k not in CHANNELS:
            continue
        name, url = CHANNELS[k]
        try:
            for x in _fetch(url):
                x["source"] = name
                x["title"] = re.sub(r"\s+-\s+" + re.escape(name) + r"$", "", x["title"])
                items.append(x)
        except Exception:
            continue
    seen, out = set(), []
    for x in sorted(items, key=lambda x: -(x["ts"] or 0)):
        k = x["title"][:70].lower()
        if k not in seen:
            seen.add(k)
            out.append(x)
    out = out[:limit if key != "all" else limit * 2]
    _cache[ck] = (time.time(), out)
    return out


def tone(days=120):
    """Daily tone of Indian market news (GDELT, free; >0 = positive coverage) for the last `days` days, with the
    60-day percentile rank of the latest day. Research: when the tone sat at a 60-day extreme, the index moved the
    same way over the next 3 days 54% of the time (2022/2023/2025). Context only — not a trading rule.
    Cached in data/news_tone.json (GDELT allows ~1 request every few seconds; we ask at most every 6 hours)."""
    import json
    path = market.DATA / "news_tone.json"
    try:
        old = json.loads(path.read_text())
    except Exception:
        old = {}
    if old and time.time() - old.get("fetched", 0) < 6 * 3600:
        return old
    try:
        r = market._session.get("https://api.gdeltproject.org/api/v2/doc/doc?query=(Nifty%20OR%20Sensex%20OR%20%22stock%20market%22)"
                                f"%20sourcecountry:IN&mode=timelinetone&timespan={days}d&format=json", timeout=40)
        r.raise_for_status()
        data = [dict(day=x["date"][:8], tone=round(x["value"], 3)) for x in r.json()["timeline"][0]["data"]]
        vals = [x["tone"] for x in data[-60:]]
        rank = sum(v <= vals[-1] for v in vals) / len(vals) if vals else None
        out = dict(fetched=time.time(), series=data, last=data[-1] if data else None, rank=rank)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(out))
        return out
    except Exception:                                      # rate-limited / offline: keep old data, retry in 30 min
        old = old or {}
        old["fetched"] = time.time() - 6 * 3600 + 1800
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps(old))
        except Exception:
            pass
        return old


def live_tv(ttl=600):
    """Current live video id per business channel (YouTube changes it whenever a stream restarts), and whether
    the video may be embedded (YouTube oEmbed answers 200 only for embeddable videos). Cached 10 minutes."""
    hit = _cache.get("tv")
    if hit and time.time() - hit[0] < ttl:
        return hit[1]
    out = []
    for t in LIVE_TV:
        x = dict(t, video=None, live=False, embeddable=False)
        try:
            r = market._session.get(f"https://www.youtube.com/@{t['handle']}/live", timeout=12)
            m = re.search(r'<link rel="canonical" href="https://www\.youtube\.com/watch\?v=([\w-]{11})"', r.text)
            if m:
                x["video"] = m.group(1)
                x["live"] = '"isLiveNow":true' in r.text or '"isLive":true' in r.text
                o = market._session.get("https://www.youtube.com/oembed?format=json&url=https://www.youtube.com/watch?v="
                                        + x["video"], timeout=12)
                x["embeddable"] = o.status_code == 200
        except Exception:
            pass
        out.append(x)
    _cache["tv"] = (time.time(), out)
    return out
