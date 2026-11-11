"""Reality check of the option model on EXPIRY DAYS, using REAL NSE prices (bhavcopy open / close).

For every expiry day of NIFTY / BANKNIFTY / MIDCPNIFTY: take the ATM strike at 09:15, and compare
  real   = (CE open + PE open) − (CE close + PE close)      ← what a 09:15 → close straddle seller really earned
  model  = same thing priced by options.py from the index + VIX
If the model's expiry-day decay is too optimistic, the model column will be clearly larger than the real one.
Run: python research/validate_expiry.py
"""
import sys
from datetime import date, datetime
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import engine  # noqa: E402
import market  # noqa: E402
import options  # noqa: E402


def main():
    rows = []
    for ix in ("NIFTY", "BANKNIFTY", "MIDCPNIFTY"):
        df = engine.load(ix)
        days = set(df["day"])
        step = market.INDICES[ix]["step"]
        for e in engine.expiries(ix):
            if e not in days or e > date.today():
                continue
            g = df[df["day"] == e]
            first, last = g.iloc[0], g.iloc[-1]
            bc = market.bhavcopy(e)
            if bc is None:
                continue
            x = bc[(bc["symbol"] == ix) & (bc["expiry"] == e)]
            k = options.atm_strike(first["open"], step)
            ce, pe = x[(x["strike"] == k) & (x["type"] == "CE")], x[(x["strike"] == k) & (x["type"] == "PE")]
            if ce.empty or pe.empty or min(ce["open"].iloc[0], pe["open"].iloc[0]) <= 0:
                continue
            real_open = ce["open"].iloc[0] + pe["open"].iloc[0]
            real_close = ce["close"].iloc[0] + pe["close"].iloc[0]
            ets = engine.expiry_ts(e)
            t0, t1 = int(first["time"]), int(last["time"]) + 300
            m_open = sum(options.premium(ix, first["open"], k, kd, ets, t0, first["vix"]) for kd in ("CE", "PE"))
            m_close = sum(options.premium(ix, last["close"], k, kd, ets, t1, last["vix"]) for kd in ("CE", "PE"))
            rows.append(dict(index=ix, day=e, strike=k, real_open=real_open, model_open=m_open, real_close=real_close,
                             model_close=m_close, real_pnl_pts=real_open - real_close, model_pnl_pts=m_open - m_close))
        print(ix, "done", flush=True)
    R = pd.DataFrame(rows)
    out = Path(__file__).resolve().parent / "out"
    R.to_csv(out / "validate_expiry.csv", index=False)
    R["open_err"] = (R["model_open"] / R["real_open"] - 1)
    g = R.groupby("index").agg(days=("day", "size"), real_open=("real_open", "median"), model_open=("model_open", "median"),
                               open_err_median=("open_err", "median"), real_pnl=("real_pnl_pts", "mean"),
                               model_pnl=("model_pnl_pts", "mean"),
                               real_win=("real_pnl_pts", lambda s: (s > 0).mean()),
                               corr=("real_pnl_pts", lambda s: np.corrcoef(s, R.loc[s.index, "model_pnl_pts"])[0, 1]))
    print(g.round(3).to_string())


if __name__ == "__main__":
    main()
