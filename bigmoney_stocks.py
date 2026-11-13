"""Per-share "big money" data from NSE archives: daily DELIVERY volume / % (how much of the day's volume was taken
into demat — real buyers, not intraday churn) and bulk / block deals.

* Delivery: NSE "security-wise full bhavcopy" (sec_bhavdata_full_DDMMYYYY.csv), free, available from Oct 2019.
  Cached per day in data/deliv/<YYYYMMDD>.pkl (EQ series of stocks.UNIVERSE only). A day that is missing upstream
  (holiday) is remembered with an empty marker so it is not fetched again.
* Bulk / block deals: NSE archives keep ONLY today's file (content/equities/bulk.csv, block.csv); there is no free
  historical archive outside the website API, so they cannot be back-tested. `collect_deals()` saves today's file
  so a history can be built going forward (not scheduled).

Research (research/bigmoney_stock_research.py) decides whether any delivery rule is used; `features()` is always
available for display on the Stocks tab.
"""
import io
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, timedelta

import numpy as np
import pandas as pd

import market
import stocks

DIR = market.DATA / "deliv"
DEALS_DIR = market.DATA / "deals"
FIRST = date(2019, 10, 1)                       # earliest sec_bhavdata_full file on the archives (Sep 2019: 404)
URL = "https://nsearchives.nseindia.com/products/content/sec_bhavdata_full_{:%d%m%Y}.csv"
# symbols renamed since 2019: delivery files use the name valid on that day
ALIASES = {"TMPV": ("TATAMOTORS",), "ETERNAL": ("ZOMATO",)}
COLS = ["sym", "day", "close", "qty", "deliv_qty", "deliv_pct", "trades"]


def parse(text, day=None, universe=None):
    """sec_bhavdata_full CSV text → DataFrame[sym, day, close, qty, deliv_qty, deliv_pct, trades] (EQ series only)."""
    df = pd.read_csv(io.StringIO(text), skipinitialspace=True)
    df.columns = [c.strip() for c in df.columns]
    for c in ("SYMBOL", "SERIES", "DATE1"):
        df[c] = df[c].astype(str).str.strip()
    df = df[df["SERIES"] == "EQ"]
    rev = {}
    for s in (universe or stocks.UNIVERSE):
        rev[s] = s
        for a in ALIASES.get(s, ()):
            rev[a] = s
    df = df[df["SYMBOL"].isin(rev)]
    num = lambda c: pd.to_numeric(df[c].astype(str).str.strip().replace({"-": None, "": None}), errors="coerce")
    out = pd.DataFrame(dict(sym=df["SYMBOL"].map(rev), close=num("CLOSE_PRICE"), qty=num("TTL_TRD_QNTY"),
                            deliv_qty=num("DELIV_QTY"), deliv_pct=num("DELIV_PER"), trades=num("NO_OF_TRADES")))
    out["day"] = day if day is not None else pd.to_datetime(df["DATE1"], format="%d-%b-%Y").dt.date
    return out[COLS].reset_index(drop=True)


def fetch_day(day, tries=3):
    """One day (cached). Returns the DataFrame, or None for a day NSE has no file (holiday / not yet published)."""
    path = DIR / f"{day:%Y%m%d}.pkl"
    if path.exists():
        df = pd.read_pickle(path)
        return None if df.empty else df
    for i in range(tries):
        try:
            r = market._session.get(URL.format(day), timeout=30)
            if r.status_code == 404 or not r.text.lstrip().startswith("SYMBOL"):
                if day < date.today() - timedelta(days=3):          # missing for good: remember it
                    market.save_pickle(pd.DataFrame(columns=COLS), path)
                return None
            r.raise_for_status()
            df = parse(r.text, day)
            market.save_pickle(df, path)
            return df
        except Exception:
            time.sleep(2 * (i + 1))
    return None


def download(days, workers=4, progress=None):
    """Fetch many days politely (≤ 4 in parallel, cached ones skipped). Returns the number of days with data."""
    todo = [d for d in days if not (DIR / f"{d:%Y%m%d}.pkl").exists()]
    n = 0
    with ThreadPoolExecutor(workers) as ex:
        for i, df in enumerate(ex.map(fetch_day, todo)):
            n += df is not None
            if progress and i % 100 == 0:
                progress(i, len(todo))
    return n


def trading_days(start=FIRST, end=None):
    """Trading days from the cached Nifty daily candles (no network)."""
    n = stocks.nifty_daily(refresh=False)
    end = end or datetime.now(market.IST).date()
    return [d for d in n["day"] if start <= d <= end]


def load(start=FIRST, end=None, refresh=False):
    """All cached delivery rows between two dates (optionally downloading missing days first)."""
    days = trading_days(start, end)
    if refresh:
        download(days)
    parts = []
    for d in days:
        p = DIR / f"{d:%Y%m%d}.pkl"
        if p.exists():
            x = pd.read_pickle(p)
            if not x.empty:
                parts.append(x)
    return pd.concat(parts, ignore_index=True) if parts else pd.DataFrame(columns=COLS)


def add_features(d):
    """Per-share delivery features, each known at that day's close (rolling windows only look back):
    deliv_pct, deliv_avg20 (20-day mean delivery %), deliv_rank (deliv_avg20's percentile vs its own last 250 days),
    deliv_x (today's delivery qty ÷ its previous 20-day average), accum (close up AND delivery % above its 60-day
    average), deliv_trend (20-day avg delivery qty ÷ 60-day avg delivery qty), accum20 (share of accumulation days in
    the last 20)."""
    out = []
    for s, g in d.sort_values("day").groupby("sym", sort=False):
        g = g.copy()
        g["deliv_avg20"] = g["deliv_pct"].rolling(20, min_periods=10).mean()
        g["deliv_rank"] = g["deliv_avg20"].rolling(250, min_periods=60).rank(pct=True)
        dq = g["deliv_qty"]
        g["deliv_x"] = dq / dq.rolling(20, min_periods=10).mean().shift(1)
        g["accum"] = (g["close"] > g["close"].shift(1)) & (g["deliv_pct"] > g["deliv_pct"].rolling(60, min_periods=20).mean().shift(1))
        g["deliv_trend"] = dq.rolling(20, min_periods=10).mean() / dq.rolling(60, min_periods=30).mean()
        g["accum20"] = g["accum"].astype(float).rolling(20, min_periods=10).mean()
        out.append(g)
    return pd.concat(out, ignore_index=True) if out else d


def features(day=None, refresh=True):
    """Display data for the Stocks tab: {sym: dict(deliv_pct, deliv_avg20, deliv_rank, deliv_x, accum, deliv_trend, accum20)}
    for the last day with data (≈ 1 year of history is enough for the ranks)."""
    end = day or datetime.now(market.IST).date()
    d = load(end - timedelta(days=420), end, refresh=refresh)
    if d.empty:
        return {}
    f = add_features(d)
    last = f["day"].max()
    out = {}
    for r in f[f["day"] == last].itertuples(index=False):
        out[r.sym] = dict(day=str(last), deliv_pct=_n(r.deliv_pct), deliv_avg20=_n(r.deliv_avg20),
                          deliv_rank=_n(r.deliv_rank, 3), deliv_x=_n(r.deliv_x), accum=bool(r.accum),
                          deliv_trend=_n(r.deliv_trend), accum20=_n(r.accum20))
    return out


def _n(x, nd=2):
    return None if x is None or (isinstance(x, float) and np.isnan(x)) else round(float(x), nd)


def collect_deals():
    """Save today's bulk + block deals (NSE archives keep only the current day). For building a history going
    forward; not scheduled. Returns rows saved."""
    n = 0
    for kind in ("bulk", "block"):
        r = market._session.get(f"https://nsearchives.nseindia.com/content/equities/{kind}.csv", timeout=30)
        if r.status_code != 200 or not r.text.startswith("Date"):
            continue
        df = pd.read_csv(io.StringIO(r.text))
        df.columns = [c.strip() for c in df.columns]
        if df.empty:
            continue
        day = pd.to_datetime(df["Date"].iloc[0], format="%d-%b-%Y").date()
        market.save_pickle(df, DEALS_DIR / f"{kind}_{day:%Y%m%d}.pkl")
        n += len(df)
    return n
