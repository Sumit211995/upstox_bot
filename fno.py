"""Daily F&O data from NSE (free, no login): real option prices + open interest for every strike, futures OI,
and the participant-wise positions of FII / DII / Pro / Client ("where the big money is").

* NSE F&O bhavcopy (one file per trading day, published ~18:00–20:00 IST): open/high/low/close, open interest and
  change in OI for every index and stock option strike and every future. Cached in data/fo/ (only our universe).
* NSE participant-wise open interest: FII, DII, Pro and Client long/short contracts in index & stock futures/options.
  Cached in data/participant/.

Used by the option-BUYING research (research/buy_research.py), the daily signal job in bot.py and the dashboard.
"""
import io
import zipfile
from datetime import date, timedelta
from pathlib import Path

import numpy as np
import pandas as pd

import market

HERE = Path(__file__).resolve().parent
FO_DIR = market.DATA / "fo"
PART_DIR = market.DATA / "participant"

# NSE indices with options (Sensex trades on BSE and is not in the NSE file)
INDEX_SYMBOLS = ("NIFTY", "BANKNIFTY", "MIDCPNIFTY")
# Large, liquid F&O stocks (monthly expiry only). Lot sizes are refreshed from Upstox's instrument file.
STOCKS = ("RELIANCE", "HDFCBANK", "ICICIBANK", "INFY", "TCS", "SBIN", "AXISBANK", "KOTAKBANK", "LT", "BHARTIARTL",
          "ITC", "BAJFINANCE", "MARUTI", "SUNPHARMA", "HINDUNILVR", "M&M", "TATASTEEL", "HCLTECH", "TITAN", "ADANIENT")
UNIVERSE = INDEX_SYMBOLS + STOCKS
LABELS = {"NIFTY": "Nifty 50", "BANKNIFTY": "Bank Nifty", "MIDCPNIFTY": "Midcap Nifty", "FINNIFTY": "Fin Nifty"}


def _url(day):
    if day >= date(2024, 7, 8):
        return f"https://nsearchives.nseindia.com/content/fo/BhavCopy_NSE_FO_0_0_0_{day:%Y%m%d}_F_0000.csv.zip"
    mon = day.strftime("%b").upper()
    return f"https://nsearchives.nseindia.com/content/historical/DERIVATIVES/{day:%Y}/{mon}/fo{day:%d}{mon}{day:%Y}bhav.csv.zip"


def _parse(raw, day):
    z = zipfile.ZipFile(io.BytesIO(raw))
    df = pd.read_csv(z.open(z.namelist()[0]), low_memory=False)
    if "TckrSymb" in df:                                   # UDiFF format (8 Jul 2024 →)
        kind = df["FinInstrmTp"].map({"IDO": "OPT", "STO": "OPT", "IDF": "FUT", "STF": "FUT"})
        df = df.assign(k=kind).rename(columns={
            "TckrSymb": "sym", "XpryDt": "expiry", "StrkPric": "strike", "OptnTp": "type", "OpnPric": "open",
            "HghPric": "high", "LwPric": "low", "ClsPric": "close", "SttlmPric": "settle", "UndrlygPric": "spot",
            "OpnIntrst": "oi", "ChngInOpnIntrst": "chg_oi", "TtlTradgVol": "volume", "NewBrdLotQty": "lot"})
    else:                                                  # old format
        kind = df["INSTRUMENT"].map({"OPTIDX": "OPT", "OPTSTK": "OPT", "FUTIDX": "FUT", "FUTSTK": "FUT"})
        df = df.assign(k=kind).rename(columns={
            "SYMBOL": "sym", "EXPIRY_DT": "expiry", "STRIKE_PR": "strike", "OPTION_TYP": "type", "OPEN": "open",
            "HIGH": "high", "LOW": "low", "CLOSE": "close", "SETTLE_PR": "settle", "OPEN_INT": "oi",
            "CHG_IN_OI": "chg_oi", "CONTRACTS": "volume"})
        df["spot"], df["lot"] = np.nan, np.nan
    df["sym"] = df["sym"].astype(str).str.strip()
    df = df[df["k"].notna() & df["sym"].isin(UNIVERSE)].copy()
    df["type"] = np.where(df["k"] == "FUT", "FUT", df["type"].astype(str).str.strip())
    df["expiry"] = pd.to_datetime(df["expiry"].astype(str).str.strip(), format="mixed").dt.date
    df["strike"] = df["strike"].fillna(0).astype(float)
    keep = ["sym", "type", "expiry", "strike", "open", "high", "low", "close", "settle", "spot", "oi", "chg_oi",
            "volume", "lot"]
    df = df[keep]
    # keep the 3 nearest expiries + anything expiring within 45 days (weekly index series would otherwise bloat it)
    lim = day + timedelta(days=45)
    near = df.groupby("sym")["expiry"].apply(lambda s: {e for i, e in enumerate(sorted(s.unique())) if i < 3 or e <= lim}).to_dict()
    df = df[[e in near[s] for s, e in zip(df["sym"], df["expiry"])]]
    for c in ("open", "high", "low", "close", "settle", "spot", "oi", "chg_oi", "volume", "lot"):
        df[c] = pd.to_numeric(df[c], errors="coerce").astype("float32")
    df["day"] = day
    return df.reset_index(drop=True)


def bhav(day: date, refresh=False):
    """Our universe's options + futures for one trading day. None if the file is not (yet) published."""
    path = FO_DIR / f"{day:%Y%m%d}.pkl"
    if path.exists() and not refresh:
        return pd.read_pickle(path)
    miss = FO_DIR / f"{day:%Y%m%d}.missing"
    if miss.exists() and not refresh and day < date.today() - timedelta(days=5):
        return None
    r = market._session.get(_url(day), timeout=60)
    if r.status_code != 200 or not r.content.startswith(b"PK"):
        if day < date.today() - timedelta(days=5):
            FO_DIR.mkdir(parents=True, exist_ok=True)
            miss.touch()
        return None
    df = _parse(r.content, day)
    FO_DIR.mkdir(parents=True, exist_ok=True)
    df.to_pickle(path)
    return df


PART_COLS = ["client", "fut_idx_long", "fut_idx_short", "fut_stk_long", "fut_stk_short", "opt_idx_call_long",
             "opt_idx_put_long", "opt_idx_call_short", "opt_idx_put_short", "opt_stk_call_long", "opt_stk_put_long",
             "opt_stk_call_short", "opt_stk_put_short", "total_long", "total_short"]


def participant(day: date, refresh=False):
    """FII / DII / Pro / Client open positions (contracts) at the close of `day`. None if not published."""
    path = PART_DIR / f"{day:%Y%m%d}.csv"
    if not path.exists() or refresh:
        r = market._session.get(f"https://nsearchives.nseindia.com/content/nsccl/fao_participant_oi_{day:%d%m%Y}.csv",
                                timeout=30)
        if r.status_code != 200 or b"Client" not in r.content:
            return None
        PART_DIR.mkdir(parents=True, exist_ok=True)
        path.write_bytes(r.content)
    lines = [l for l in path.read_text().splitlines() if l.split(",")[0].strip() in ("Client", "DII", "FII", "Pro")]
    rows = [[x.strip() for x in l.split(",")[:15]] for l in lines]
    df = pd.DataFrame(rows, columns=PART_COLS).set_index("client").apply(pd.to_numeric, errors="coerce")
    return df


def lots(max_age_h=12):
    """Current lot size per symbol (futures contracts in Upstox's public NSE instrument file), cached."""
    import gzip
    import json
    import time
    path = market.DATA / "lots.json"
    if path.exists() and time.time() - path.stat().st_mtime < max_age_h * 3600:
        return json.loads(path.read_text())
    raw = market._session.get("https://assets.upstox.com/market-quote/instruments/exchange/NSE.json.gz", timeout=60)
    raw.raise_for_status()
    out = {}
    for x in json.load(gzip.open(io.BytesIO(raw.content))):
        u = x.get("underlying_symbol")
        if x.get("instrument_type") == "FUT" and u in UNIVERSE:
            out.setdefault(u, int(x["lot_size"]))
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(out))
    return out


# ------------------------------------------------------------------ bulk load for research / dashboard
def trading_days(start=date(2022, 1, 1), end=None):
    """Trading days from the cached Nifty 5-minute candles (no network if the cache is current)."""
    df = market.history(market.INDICES["NIFTY"]["key"])
    days = pd.to_datetime(df["time"], unit="s", utc=True).dt.tz_convert("Asia/Kolkata").dt.date.unique()
    end = end or date.today()
    return [d for d in sorted(days) if start <= d <= end]


def download(days, workers=6, progress=None):
    """Fetch bhavcopy + participant files for many days in parallel (cached; already-cached days are skipped)."""
    from concurrent.futures import ThreadPoolExecutor

    def one(d):
        ok = bhav(d) is not None
        participant(d)
        return ok
    done = 0
    with ThreadPoolExecutor(workers) as ex:
        for ok in ex.map(one, days):
            done += 1
            if progress and done % 50 == 0:
                progress(done, len(days))
    return done


def load_all(days=None):
    """All cached daily files concatenated (only days on disk)."""
    files = sorted(FO_DIR.glob("*.pkl"))
    if days is not None:
        want = {f"{d:%Y%m%d}" for d in days}
        files = [f for f in files if f.stem in want]
    if not files:
        return pd.DataFrame()
    df = pd.concat([pd.read_pickle(f) for f in files], ignore_index=True)
    return df[df["sym"].isin(UNIVERSE)].reset_index(drop=True)


WHO = ("FII", "DII", "Pro", "Client")


def participant_table(days, win=60):
    """Per day, for FII / DII / Pro / Client: index-futures long share and net, index-options net position
    (calls net long − puts net long), stock-futures long share, their 1-day changes, and 60-day percentile ranks."""
    rows = []
    for d in days:
        p = participant(d)
        if p is None:
            continue
        r = dict(day=d)
        for who in WHO:
            x = p.loc[who]
            L, S = x.fut_idx_long, x.fut_idx_short
            r[f"{who}_fut_share"] = L / (L + S) if L + S else np.nan
            r[f"{who}_fut_net"] = L - S
            r[f"{who}_opt_net"] = (x.opt_idx_call_long - x.opt_idx_call_short) - (x.opt_idx_put_long - x.opt_idx_put_short)
            r[f"{who}_stk_share"] = x.fut_stk_long / (x.fut_stk_long + x.fut_stk_short)
        rows.append(r)
    t = pd.DataFrame(rows)
    if t.empty:
        return t
    t = t.sort_values("day").reset_index(drop=True)
    base = [c for c in t.columns if c != "day"]
    for c in base:
        t[c + "_chg"] = t[c].diff()
    for c in base + [c + "_chg" for c in base]:
        t[c + "_rk"] = t[c].rolling(win, min_periods=30).rank(pct=True)
    return t


def fii_dii_cash():
    """Today's FII/FPI and DII buy / sell / net in the CASH market (₹ crore), from NSE (latest day only).
    Saved to data/fiidii_cash.csv so a history builds up day by day."""
    r = market._session.get("https://www.nseindia.com/api/fiidiiTradeReact", timeout=20,
                            headers={"Referer": "https://www.nseindia.com/reports/fii-dii"})
    r.raise_for_status()
    rows = [dict(day=pd.to_datetime(x["date"], format="%d-%b-%Y").date(), who="FII" if "FII" in x["category"] else "DII",
                 buy=float(x["buyValue"]), sell=float(x["sellValue"]), net=float(x["netValue"])) for x in r.json()]
    path = market.DATA / "fiidii_cash.csv"
    old = pd.read_csv(path, parse_dates=["day"]) if path.exists() else pd.DataFrame()
    if len(old):
        old["day"] = old["day"].dt.date
    allr = pd.concat([old, pd.DataFrame(rows)]).drop_duplicates(["day", "who"], keep="last").sort_values(["day", "who"])
    path.parent.mkdir(parents=True, exist_ok=True)
    allr.to_csv(path, index=False)
    return allr


def participants_all(days):
    out = []
    for d in days:
        p = participant(d)
        if p is None:
            continue
        fii, cl = p.loc["FII"], p.loc["Client"]
        out.append(dict(day=d,
                        fii_fut_long=fii.fut_idx_long, fii_fut_short=fii.fut_idx_short,
                        fii_call_net=fii.opt_idx_call_long - fii.opt_idx_call_short,
                        fii_put_net=fii.opt_idx_put_long - fii.opt_idx_put_short,
                        fii_stk_fut_long=fii.fut_stk_long, fii_stk_fut_short=fii.fut_stk_short,
                        client_fut_long=cl.fut_idx_long, client_fut_short=cl.fut_idx_short))
    return pd.DataFrame(out)


if __name__ == "__main__":                                 # python fno.py → download / top up the cache
    import sys
    ds = trading_days()
    print(f"{len(ds)} trading days {ds[0]} → {ds[-1]}")
    download(ds, progress=lambda a, b: print(f"  {a}/{b}", flush=True))
    print("cached days:", len(list(FO_DIR.glob('*.pkl'))), file=sys.stderr)
