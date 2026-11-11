"""Calibrate the option-price model on REAL NSE closing prices (bhavcopy) and measure its error.

For ~120 sample days (2022 → 2026) and NIFTY / BANKNIFTY / MIDCPNIFTY:
  * implied vol of the ATM option (from the real closing price) ÷ India VIX  → IV_FACTOR per index
  * how much the model premium differs from the real premium for near-the-money options
Run:  python research/calibrate.py
"""
import sys
import time
from datetime import date, datetime, timedelta
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import market  # noqa: E402
import options  # noqa: E402

NAMES = {"NIFTY": "NIFTY", "BANKNIFTY": "BANKNIFTY", "MIDCPNIFTY": "MIDCPNIFTY"}


def daily_close(key):
    d = market.resample(market.history(key), "D")
    d["day"] = pd.to_datetime(d["time"], unit="s", utc=True).dt.tz_convert("Asia/Kolkata").dt.date
    return dict(zip(d["day"], d["close"]))


def main():
    closes = {n: daily_close(market.INDICES[n]["key"]) for n in NAMES}
    vix = daily_close(market.VIX_KEY)
    days = sorted(vix)
    sample = days[20::9]                                  # ~every 9th trading day
    rows, errs = [], []
    t0 = time.time()
    for i, day in enumerate(sample):
        bc = market.bhavcopy(day)
        if bc is None or bc.empty:
            continue
        for n in NAMES:
            spot = closes[n].get(day)
            if not spot:
                continue
            x = bc[bc["symbol"] == n]
            if x.empty:
                continue
            exp_ts = {e: datetime(e.year, e.month, e.day, 15, 30).timestamp() for e in x["expiry"].unique()}
            now = datetime(day.year, day.month, day.day, 15, 30).timestamp() - 1
            for e in sorted(exp_ts)[:3]:
                t = options.trading_years(now, exp_ts[e])
                if t <= 0.9 / options.TRADING_DAYS:
                    continue
                xe = x[x["expiry"] == e]
                k = xe.iloc[(xe["strike"] - spot).abs().argsort()[:1]]["strike"].iloc[0]
                ivs = []
                for kind in ("CE", "PE"):
                    p = xe[(xe["strike"] == k) & (xe["type"] == kind)]["close"]
                    if len(p) and p.iloc[0] > 0:
                        iv = options.implied_vol(float(p.iloc[0]), spot, k, t, kind)
                        if iv:
                            ivs.append(iv)
                if ivs:
                    rows.append(dict(day=day, index=n, dte=round(t * options.TRADING_DAYS), iv=np.mean(ivs), vix=vix[day],
                                     ratio=np.mean(ivs) / (vix[day] / 100)))
                # model error on near-the-money options with volume
                near = xe[((xe["strike"] - spot).abs() / spot < 0.03) & (xe["close"] > 2) & (xe["volume"] > 0)]
                for _, r in near.iterrows():
                    m = options.premium(n, spot, r["strike"], r["type"], exp_ts[e], now, vix[day])
                    errs.append(dict(index=n, dte=round(t * options.TRADING_DAYS), mny=(r["strike"] - spot) / spot, kind=r["type"],
                                     real=r["close"], model=m, err=(m - r["close"]) / r["close"]))
        if i % 20 == 0:
            print(f"{i}/{len(sample)} {day} {time.time() - t0:.0f}s", flush=True)
    R, E = pd.DataFrame(rows), pd.DataFrame(errs)
    out = Path(__file__).resolve().parent / "out"
    out.mkdir(exist_ok=True)
    R.to_csv(out / "calib_iv.csv", index=False)
    E.to_csv(out / "calib_err.csv", index=False)
    print("\nATM implied vol ÷ VIX (median), by index and days to expiry:")
    R["dteb"] = pd.cut(R["dte"], [0, 2, 5, 14, 28, 300])
    print(R.groupby(["index", "dteb"], observed=True)["ratio"].median().unstack().round(2))
    print("\nby year:")
    R["year"] = [d.year for d in R["day"]]
    print(R.groupby(["index", "year"])["ratio"].median().unstack().round(2))
    print("\nModel premium error vs real close (near-the-money, |strike-spot| < 3%):")
    E["abserr"] = E["err"].abs()
    print(E.groupby("index").agg(n=("err", "size"), median_err=("err", "median"), median_abs=("abserr", "median"),
                                 p80_abs=("abserr", lambda s: s.quantile(.8))).round(3))


if __name__ == "__main__":
    main()
