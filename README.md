# Upstox Index Bot · paper trading for Nifty, Bank Nifty, Sensex and Midcap Nifty options

> **PAPER TRADING ONLY.** This bot never places an order and needs no Upstox login.
> It watches the indices with free public data, simulates option trades with real lot sizes and Indian
> charges, and records every result so you can judge the strategy before risking money. Not financial advice.

Built by **$um!t_K@Ktw@n** · sister project of [delta_bot](https://github.com/Sumit211995/delta_bot) (same design, same dashboard style).

## 1. What it does

| Part | What |
|---|---|
| **Data** | Upstox **public** API: 5-minute candles since Jan 2022 for Nifty 50, Bank Nifty, Sensex, Midcap Select and India VIX, plus live candles every market minute. Contract list (lot sizes, strikes, expiries) from Upstox's public instrument file. Real daily option prices from NSE **bhavcopy** files (used to check the price model). |
| **Option prices** | Live option prices need a broker login, so premiums are **estimated** with Black-Scholes on a *trading-hours clock*, India VIX scaled per index, and an expiry-day correction. Checked against **≈ 41,000 real NSE closing prices**: median error ≈ 9%; on expiry days the model is calibrated to real 09:15 prices and **under**-states the seller's profit (conservative). |
| **Strategy** | Chosen by walk-forward research (below): **sell a hedged ATM straddle on expiry day**, stop each sold leg if its premium rises 60%, close everything at 15:15. |
| **Paper bot** | `bot.py` replays the *same simulator used in the backtest* on today's live candles every 20 s (09:15–15:30 IST, Mon–Fri). Entries, exits and P&L go to Telegram and `logs/paper_trades_<book>.csv`. |
| **Dashboard** | `dashboard.py` → http://localhost:8002 · live index tiles, chart with every past trade, today's positions (legs, stops, live P&L), paper journal, strategy research table and full trade history since 2022. Read-only. |

## 2. Research: what works on Indian index options (2022 → Nov 2026)

Method: every strategy × index × setting was simulated as a real option trade (lot size, premium in/out, brokerage,
STT, exchange + SEBI charges, stamp duty, GST, 0.5% slippage per order). Settings were **chosen on 2022–2024 only**
and then **judged on 2025–2026** (data they never saw). Script: `research/run_research.py`.

| Strategy family | Share of settings profitable in **both** periods |
|---|---|
| Opening-range breakout (buy CE/PE) | **0%** on all 4 indices |
| Supertrend (buy CE/PE) | **0%** |
| Trend pullback (buy CE/PE) | **0%** |
| **Short straddle (hedged, per-leg stop)** | **most settings** on all 4 indices; best results on **expiry days** |

Why buying fails: an option buyer must beat time decay *and* ~₹40–50 of charges per round trip; intraday moves on
the indices are usually too small. Why expiry-day selling works: the ATM premium collapses to near zero by the close,
and stops + wings cap the bad days. **Real NSE prices confirm it**: selling the ATM straddle at 09:15 and buying it back
at the close made money on 68% (Nifty, 249 expiries), 69% (Bank Nifty, 169) and 77% (Midcap, 95) of expiry days
(`research/validate_expiry.py`).

### The paper books (`books.json`): hedged straddle, entry 10:00, wings 10 strikes away, exit 15:15, 1 lot

| Book | Stop per leg | 2022–24 (tuning): trades · win · PF · net | **2025–26 (unseen)**: trades · win · PF · net | Worst day (test) |
|---|---|---|---|---|
| Nifty · expiry day | 80% | 151 · 60% · 2.02 · +₹1.29 L | **101 · 58% · 2.50 · +₹1.25 L** | −₹9.2k |
| Nifty · every day | 100% | 723 · 69% · 1.91 · +₹3.44 L | **461 · 66% · 1.96 · +₹2.56 L** | −₹10.2k |
| Sensex · expiry day | 60% | 80 · 70% · 2.37 · +₹86k | **91 · 76% · 1.66 · +₹65k** | −₹12.1k |
| Sensex · every day | 80% | 405 · 60% · 1.79 · +₹1.24 L | **461 · 56% · 1.44 · +₹95k** | −₹12.1k |
| Bank Nifty · expiry day | 100% | 155 · 52% · 2.47 · +₹2.14 L | **29 · 59% · 3.48 · +₹62k** | −₹4.7k |
| Midcap Nifty · expiry day | 100% | 149 · 68% · 4.75 · +₹3.60 L | **24 · 71% · 2.23 · +₹39k** | −₹10.4k |

Midcap "every day" was dropped: it lost money in 2025–26 (PF 0.98). The full grid (all strategies, all settings) is
in `research/out/grid.csv`, the summary in `research/out/summary.md`, and both are on the dashboard.

**Risks you must know:** option selling earns often and loses rarely but sharply (big gap or one-way trend day).
The wings (hedges 10 strikes away) cap the worst case; the 60% stop limits each leg. Paper results use modelled
premiums, so real fills will differ. Bank Nifty and Midcap only have **monthly** expiries now (1 trade a month).

## 3. Run it

```bash
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
cp .env.example .env                 # add TELEGRAM_TOKEN / TELEGRAM_CHAT_ID (optional)
.venv/bin/python -m pytest -q tests  # 13 tests
.venv/bin/python bot.py              # paper bot (sleeps outside market hours)
.venv/bin/python dashboard.py        # http://localhost:8002
.venv/bin/python bot.py --status     # paper results so far
```

Research (optional, ~15 min): `python research/calibrate.py`, `python research/validate_expiry.py`, `python research/run_research.py`.

Strategy books (which index / strategy / settings are paper-traded) are in **`books.json`**.

## 4. Files

| File | Job |
|---|---|
| `market.py` | Free data: Upstox candles (cached in `data/`), live intraday candles, instrument list, NSE bhavcopy |
| `options.py` | Black-Scholes, trading-hours clock, implied vol, VIX→IV calibration, strikes, Indian F&O charges |
| `engine.py` | Strategy signals (ORB, supertrend, pullback), straddle seller, option-trade simulator, stats |
| `bot.py` | Paper bot loop, Telegram alerts, journal |
| `dashboard.py` + `dashboard.html` | Read-only dashboard (same design as delta_bot) |
| `books.json` | The paper strategy books |
| `research/` | Calibration, expiry-day validation, walk-forward research + results in `research/out/` |
| `tests/test_core.py` | Option maths, trading clock, charges, simulator, safety checks |

## 5. Deploy next to delta_bot (Oracle server)

```bash
# on the server
cd ~ && git clone https://github.com/Sumit211995/upstox_bot.git && cd upstox_bot && mkdir -p state logs data
# from your Mac: copy settings
scp -i ~/Downloads/oracle.key ~/Documents/Project/upstox_bot/.env ubuntu@<SERVER_IP>:~/upstox_bot/.env
# on the server
chmod 600 .env && docker compose up -d --build
sudo tailscale serve --bg --https=8443 8002     # dashboard at https://<your-server>.ts.net:8443
```
No static IP / API key is needed while it is paper-only. Going live later would need an Upstox app, the
static-IP registration SEBI requires (the Oracle IP works) and new order code: not built, on purpose.
