"""Live calls: can the price-based buying signals be called just BEFORE the close (from the live price at ~15:10)
and bought at that day's close, instead of waiting for the next morning's open?
Compares entry at the signal day's real NSE close vs the next day's real open, same exits, train 2022–24 / test 2025–26.
Smart-money signals cannot move: NSE publishes the FII/DII positions only after the close."""
import json, sys
from datetime import date
from pathlib import Path
import pandas as pd
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import fno, swing, swing_bot  # noqa
OUT = Path(__file__).resolve().parent / "out"; SPLIT = date(2025, 1, 1)
f = swing.add_vix(pd.read_pickle(OUT / "buy_features_tech.pkl"))
fo = fno.load_all(); lots = fno.lots(); chain = swing.Chain(fo)
strats = {s["id"]: s for s in swing_bot.load_books()[0]}
rows = []
for sid in ("idx-breakout", "idx-emax"):
    st = strats[sid]
    for spread in (0, 6):
        for at_close in (False, True):
            p = dict(st["params"], at_close=at_close, **({"spread": spread} if spread else {}))
            tr = [t for t in swing.simulate(f, chain, st["signal"], p, lots, budget=20000 if not spread else 7000, syms=st["syms"]) if t["reason"] != "open"]
            a = swing.stats([t for t in tr if t["signal_day"] < SPLIT]); b = swing.stats([t for t in tr if t["signal_day"] >= SPLIT])
            rows.append(dict(strategy=sid, spread=spread, entry="close (live call)" if at_close else "next open",
                             tr_n=a["n"], tr_win=a["win"], tr_pf=a["pf"], tr_net=a["net"], te_n=b["n"], te_win=b["win"], te_pf=b["pf"], te_net=b["net"]))
            print(rows[-1], flush=True)
pd.DataFrame(rows).to_csv(OUT / "close_entry.csv", index=False)
