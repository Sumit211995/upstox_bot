"""Download / top up daily candles for the stock universe + Nifty (data/eq/), and report split-like gaps."""
import sys, time
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import stocks  # noqa
t = time.time()
k = stocks.keys(max_age_h=0)
print("keys", len(k), "missing", [s for s in stocks.UNIVERSE if s not in k], flush=True)
n = stocks.nifty_daily(); print("NIFTY", len(n), n.day.iloc[0], n.day.iloc[-1], flush=True)
for s in stocks.UNIVERSE:
    df = stocks.candles(s)
    if df is None:
        print(s, "NO DATA"); continue
    g = stocks.split_gaps(df)
    print(s, len(df), df.day.iloc[0], df.day.iloc[-1], "gaps", g[:3], flush=True)
print("done", round(time.time() - t), "s")
