"""Free market data for Indian indices (no login needed).

* Index candles: Upstox public historical-candle API v3 (5-minute data since Jan 2022), cached in data/candles/.
* Today's candles: Upstox public intraday endpoint (used live by the paper bot).
* Contract list (lot size, strikes, expiries): Upstox public instrument file, refreshed daily.
* Real daily option prices: NSE bhavcopy files (used to check the option-price model).
"""
import gzip
import io
import json
import threading
import time
import zipfile
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import quote

import pandas as pd
import requests

HERE = Path(__file__).resolve().parent
DATA = HERE / "data"
IST = timezone(timedelta(hours=5, minutes=30))
API = "https://api.upstox.com/v3/historical-candle"
HIST_START = date(2022, 1, 1)                      # Upstox minute data starts January 2022
UA = {"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 Chrome/130.0 Safari/537.36",
      "Accept": "application/json"}

# index name -> Upstox keys and option details (lot sizes / strike steps are refreshed from the instrument file)
INDICES = {
    "NIFTY":      dict(key="NSE_INDEX|Nifty 50", exchange="NSE", label="Nifty 50", short="NIFTY", step=50, lot=65),
    "BANKNIFTY":  dict(key="NSE_INDEX|Nifty Bank", exchange="NSE", label="Bank Nifty", short="BANKNIFTY", step=100, lot=30),
    "SENSEX":     dict(key="BSE_INDEX|SENSEX", exchange="BSE", label="Sensex", short="SENSEX", step=100, lot=20),
    "MIDCPNIFTY": dict(key="NSE_INDEX|NIFTY MID SELECT", exchange="NSE", label="Midcap Nifty", short="MIDCPNIFTY", step=25, lot=120),
}
VIX_KEY = "NSE_INDEX|India VIX"
_session = requests.Session()
_session.headers.update(UA)
_lock = threading.Lock()


def _get(url, tries=4):
    for i in range(tries):
        try:
            r = _session.get(url, timeout=25)
            if r.status_code == 429:
                time.sleep(5 * (i + 1))
                continue
            r.raise_for_status()
            return r.json()
        except requests.RequestException:
            if i == tries - 1:
                raise
            time.sleep(2 * (i + 1))


def _frame(candles):
    if not candles:
        return pd.DataFrame(columns=["time", "open", "high", "low", "close"])
    df = pd.DataFrame([c[:5] for c in candles], columns=["ts", "open", "high", "low", "close"])
    df["time"] = pd.to_datetime(df["ts"]).astype("int64") // 10**9      # epoch seconds (candle start)
    return df[["time", "open", "high", "low", "close"]].astype({"open": float, "high": float, "low": float, "close": float})


def fetch_range(key, start: date, end: date, unit="minutes", n=5):
    """Candles between two dates (inclusive); splits into 1-month requests as the API requires."""
    parts, cur = [], start
    while cur <= end:
        nxt = min((cur.replace(day=1) + timedelta(days=32)).replace(day=1) - timedelta(days=1), end)
        url = f"{API}/{quote(key, safe='')}/{unit}/{n}/{nxt.isoformat()}/{cur.isoformat()}"
        parts.append(_frame(_get(url)["data"]["candles"]))
        cur = nxt + timedelta(days=1)
        time.sleep(0.25)
    df = pd.concat(parts) if parts else _frame([])
    return df.drop_duplicates("time").sort_values("time").reset_index(drop=True)


def intraday(key, n=5):
    """Today's candles so far (the last one may still be forming)."""
    url = f"{API}/intraday/{quote(key, safe='')}/minutes/{n}"
    # Upstox returns today's candles NEWEST FIRST: sort oldest → newest so iloc[-1] is the latest candle
    return _frame(_get(url)["data"]["candles"]).sort_values("time").reset_index(drop=True)


def history(key, n=5, progress=None):
    """All 5-minute candles since Jan 2022, cached on disk and topped up with new days only."""
    name = key.split("|")[1].replace(" ", "_")
    path = DATA / "candles" / f"{name}_{n}m.pkl"
    with _lock:
        df = pd.read_pickle(path) if path.exists() else None
        today = datetime.now(IST).date()
        start = HIST_START if df is None or df.empty else \
            datetime.fromtimestamp(int(df["time"].iloc[-1]), IST).date()
        if df is None or start <= today:
            new = fetch_range(key, start, today, n=n)
            df = new if df is None else pd.concat([df, new])
            df = df.drop_duplicates("time", keep="last").sort_values("time").reset_index(drop=True)
            path.parent.mkdir(parents=True, exist_ok=True)
            df.to_pickle(path)
        if progress:
            progress(len(df))
        return df


def resample(df5, minutes):
    """Resample 5-minute index candles into 15m / 30m / 60m (session-aligned, 09:15 start) or daily ('D')."""
    if df5.empty:
        return df5
    t = pd.to_datetime(df5["time"], unit="s", utc=True).dt.tz_convert("Asia/Kolkata")
    x = df5.assign(t=t).set_index("t")
    agg = dict(open="first", high="max", low="min", close="last", time="first")
    if minutes == "D":
        out = x.resample("1D").agg(agg)
    else:
        out = x.resample(f"{minutes}min", origin="start_day", offset="15min").agg(agg)
    out = out.dropna(subset=["open"]).reset_index(drop=True)
    out["time"] = out["time"].astype("int64")
    return out


def vix_daily():
    """India VIX daily closes since Sep 2021 (cached in data/vix_daily.pkl, topped up with new days). Columns: day, close."""
    path = DATA / "vix_daily.pkl"
    with _lock:
        df = pd.read_pickle(path) if path.exists() else None
        today = datetime.now(IST).date()
        start = date(2021, 9, 1) if df is None or df.empty else df["day"].iloc[-1]
        if df is None or start < today:
            new = fetch_range(VIX_KEY, start, today, unit="days", n=1)
            new["day"] = pd.to_datetime(new["time"], unit="s", utc=True).dt.tz_convert("Asia/Kolkata").dt.date
            df = new if df is None else pd.concat([df, new])
            df = df.drop_duplicates("day", keep="last").sort_values("day").reset_index(drop=True)
            path.parent.mkdir(parents=True, exist_ok=True)
            df.to_pickle(path)
        return df


# ------------------------------------------------------------------ contract list (lot size, expiries, strikes)
def instruments(max_age_h=12):
    """Index option contracts from Upstox's public instrument files (cached, refreshed twice a day)."""
    path = DATA / "instruments.json"
    if path.exists() and time.time() - path.stat().st_mtime < max_age_h * 3600:
        return json.loads(path.read_text())
    out = {}
    for ex in ("NSE", "BSE"):
        raw = _session.get(f"https://assets.upstox.com/market-quote/instruments/exchange/{ex}.json.gz", timeout=60)
        raw.raise_for_status()
        for x in json.load(gzip.open(io.BytesIO(raw.content))):
            u = x.get("underlying_symbol")
            if x.get("instrument_type") in ("CE", "PE") and u in INDICES:
                d = out.setdefault(u, dict(lot=x["lot_size"], expiries=set(), strikes={}))
                e = int(x["expiry"] // 1000)
                d["expiries"].add(e)
                d["strikes"].setdefault(str(e), set()).add(float(x["strike_price"]))
    for u, d in out.items():
        d["expiries"] = sorted(d["expiries"])
        d["strikes"] = {k: sorted(v) for k, v in d["strikes"].items()}
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(out))
    return out


def next_expiry(name, now_ts, inst=None):
    """Nearest expiry (epoch seconds, 15:30 IST) that is still at least today, from the live contract list."""
    inst = inst or instruments()
    exps = inst.get(name, {}).get("expiries", [])
    for e in exps:
        if e >= now_ts:
            return e
    return None


# ------------------------------------------------------------------ NSE bhavcopy (real daily option prices)
def bhavcopy(day: date):
    """NSE F&O daily file for one day (index options only). Returns None on holidays / missing days."""
    path = DATA / "bhav" / f"fo2_{day:%Y%m%d}.pkl"
    if path.exists():
        return pd.read_pickle(path)
    if day >= date(2024, 7, 8):
        url = f"https://nsearchives.nseindia.com/content/fo/BhavCopy_NSE_FO_0_0_0_{day:%Y%m%d}_F_0000.csv.zip"
    else:
        mon = day.strftime("%b").upper()
        url = (f"https://nsearchives.nseindia.com/content/historical/DERIVATIVES/{day:%Y}/{mon}/"
               f"fo{day:%d}{mon}{day:%Y}bhav.csv.zip")
    r = _session.get(url, timeout=40)
    if r.status_code != 200 or not r.content.startswith(b"PK"):
        return None
    z = zipfile.ZipFile(io.BytesIO(r.content))
    df = pd.read_csv(z.open(z.namelist()[0]))
    if "TckrSymb" in df:                                  # new UDiFF format (July 2024 →)
        df = df[(df["FinInstrmTp"] == "IDO")].rename(columns={
            "TckrSymb": "symbol", "XpryDt": "expiry", "StrkPric": "strike", "OptnTp": "type",
            "OpnPric": "open", "HghPric": "high", "LwPric": "low", "ClsPric": "close", "SttlmPric": "settle", "UndrlygPric": "spot", "OpnIntrst": "oi", "TtlTradgVol": "volume"})
    else:                                                 # old format
        df = df[df["INSTRUMENT"] == "OPTIDX"].rename(columns={
            "SYMBOL": "symbol", "EXPIRY_DT": "expiry", "STRIKE_PR": "strike", "OPTION_TYP": "type",
            "OPEN": "open", "HIGH": "high", "LOW": "low", "CLOSE": "close", "SETTLE_PR": "settle", "OPEN_INT": "oi", "CONTRACTS": "volume"})
        df["spot"] = float("nan")
    df = df[[c for c in ("symbol", "expiry", "strike", "type", "open", "high", "low", "close", "settle", "spot", "oi", "volume") if c in df]]
    df["expiry"] = pd.to_datetime(df["expiry"], format="mixed").dt.date
    df["day"] = day
    path.parent.mkdir(parents=True, exist_ok=True)
    df.to_pickle(path)
    return df
