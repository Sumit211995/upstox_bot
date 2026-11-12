# Upstox Index Bot · paper trading for Nifty, Bank Nifty, Sensex and Midcap Nifty options (selling + buying)

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

## 2b. Option BUYING with a small account (₹20k): real NSE prices, open interest, FII positions, news

Option selling needs ₹1–2 lakh of margin per lot, so this part answers: *what can you BUY with about ₹20,000?*
Everything here is tested on **real NSE option prices** (daily bhavcopy: open / high / low / close of every strike,
Jan 2022 → Nov 2026), not a model. Script: `research/buy_research.py` → `buy_focus.py` → `buy_account.py` → `buy_final.py`.

**Timeline (no look-ahead):** signal from the day's closing data (NSE publishes OI after the close) → buy at the
**next day's real 09:15 open price** (+0.5% slippage) → stop / exit checked on the option's real daily high / low
(if stop and target are both touched on one day, the stop is assumed first) → Upstox charges on every order.

**Signals tested** (3 indices + 20 large F&O stocks, 72 trade settings each: strike, stop, target, holding days, expiry):

| Signal | Idea | Result |
|---|---|---|
| **OI ratio** (your 30 : 70 idea) | call vs put open interest near the price; buy the side with **less** OI | ✅ **works on Nifty / Bank Nifty** when measured on strikes within ±2% and compared with its own last 60 days |
| Fixed 30 : 70 split | the same, with a fixed cut-off | almost never happens on the index at the close (call share stays 43–65%) |
| Fresh OI (today's change) | where new positions were added | no edge |
| Futures buildup | price ↑ + OI ↑ = long buildup, etc. | no edge (slightly mean-reverting) |
| FII index-futures positions | NSE participant-wise OI: FII long share rising / falling | no edge on its own |
| Trend (EMA) | | no edge |
| **Stock options** (any of the above) | monthly expiry, top-20 F&O stocks | ❌ lost money in 99.6% of 2,736 settings, also as spreads / at half the slippage: bid/ask + time decay |

**The rule that worked** (chosen on 2022–24, then checked on 2025–26):
when the call : put OI on strikes within ±2% of the index is at its most one-sided **10% of the last 60 days**,
buy the side with less OI (few calls → BUY CE, few puts → BUY PE) · ATM · expiry ≥ 15 days away · stop −30% ·
no target · out after 5 trading days. 81% of the neighbouring settings (5-day hold) were profitable in both periods.

| Paper book (`buy_books.json`) | Account | 2022–24 | **2025–26 (unseen)** | Lowest point |
|---|---|---|---|---|
| Nifty · OI-extreme · buy CE/PE (1 lot ≈ ₹16k) | ₹60k | 83 trades · win 36% · PF 1.70 · ₹60k → ₹2.39 L | **54 · 30% · PF 1.76 · ₹60k → ₹2.09 L** | ₹24k (2022) |
| Nifty + Bank Nifty · OI-extreme · **debit spread** (buy ATM, sell 6 strikes out, ≈ ₹7k) | **₹20k** | 87 · 46% · PF 1.01 · ₹20k → ₹22k | **61 · 51% · PF 1.31 · ₹20k → ₹49k** | ₹8.2k |

**Read this before using real money:** wins are rare (≈ 1 in 3 for the naked buy) and come from a few big trades;
a ₹20k account buying a full Nifty lot would have been **wiped out in 2022** (that is why that book uses ₹60k).
2022 was a losing year for the signal; 2023–2026 were profitable. The spread's account size / width was compared on
both periods, so it is less "unseen" than the signal itself. Paper-trade it first. Not financial advice.

**Daily routine (automatic):** after ~18:15 IST the bot downloads the day's NSE F&O file + FII/DII positions, settles
open paper trades at real prices, and sends **tomorrow's plan** to Telegram ("BUY NIFTY 25300 PE at the 09:15 open,
why: OI calls 62% : puts 38%, rank 98…"). The dashboard shows the books, tomorrow's plan, all paper + backtest trades,
an **OI scanner** for every index and stock (call : put OI, 60-day rank, PCR, put / call walls, futures buildup,
FII positions) and **live news** per symbol with a simple mood score (news is context only: there is no free archive
of old headlines to test it on, so it is not part of the rules).

### Big money (FII / DII / Pro / retail) and news: what helps a buyer

`research/fii_research.py`, `dii_focus.py`, `smart_trainonly.py`, `news_research.py`. NSE publishes every evening how many
index futures / options contracts FIIs, DIIs, Pros and retail clients hold (participant-wise OI, free since 2022).

| Measure (buy ATM, stop 30%, ≤ 5 days, real prices) | PF 2022–24 | PF 2025–26 (unseen) |
|---|---|---|
| DII index-futures long share (follow) | 1.32 | **2.17** |
| DII adding futures longs (follow) | 1.35 | 1.77 |
| Retail (Client) index options (do the opposite) | 1.13 | 1.79 |
| FII index options net position (follow) | 1.32 | 1.56 |
| FII index-futures long % (the number most people quote) | **0.70** | 1.27 |
| **Smart-money score** = average of the 5 best measures **chosen on 2022–24 only** | 2.32 | **4.59** (11 trades) |
| OI-extreme + "FII/Pro options agree" filter | 1.9–2.0 | 1.7–2.1 (half the trades, same total) |
| News tone extreme (GDELT, follow; 2022/23/25 only) | 1.48 | 1.75 (right 54%) |
| OI-extreme, skipping trades when news disagrees | 1.31 | 1.13 ← news filter makes it **worse** |

→ New paper book **"Nifty + Bank Nifty · smart money (FII/DII) · buy CE/PE"** (₹20k account, 1 ATM lot, ≈ 1 trade a
month): ₹20k → ₹1.19 L (2022–24) and ₹20k → ₹1.34 L (2025–26), lowest point ₹12.4k. Small sample: paper-trade first.
News is shown as context (7 business channels' RSS, live TV, GDELT tone), not used as a rule. FII/DII cash-market
buy/sell (NSE, latest day only) is saved every evening to `data/fiidii_cash.csv` to build a history.

### All buying strategies (v3): technical indicators, 1:2 exits, combined accounts

`research/tech_research.py` → `combined.py` → `rolling_start.py` → `small_account.py` → `buy_final.py`.
22 signal set-ups (RSI pullback, 20-day breakout, EMA 9/21, MACD, Supertrend, Bollinger squeeze, OI, smart money,
each alone and with a trend filter) × 72 exits (1:2 risk-reward and others, 3–8 days, ATM / 1 ITM, expiry ≥ 15/25 days).
Settings picked on 2022–24 only:

| Strategy | Exit | Win % 22–24 → **25–26** | PF 22–24 → **25–26** |
|---|---|---|---|
| Index smart money (FII/DII) | SL −50%, ≤ 5 days | 67 → **55** | 3.85 → **4.06** |
| Index 20-day breakout | **1:2** (−35% / +70%), 1 ITM | 53 → **50** | 2.27 → **2.03** |
| Index EMA 9/21 cross | **1:2** (−25% / +50%), 1 ITM | 60 → 40 | 3.01 → 1.31 |
| Stocks smart money | SL −30%, 1 ITM | 46 → **43** | 1.87 → **1.51** |
| RSI pullback, MACD, Supertrend, squeeze, OI (grid medians) | | | mostly < 1 or unstable |

**Combined paper accounts** (`buy_books.json` → `accounts`; every signal from the 4 strategies above, best first):

| Account | 2025–26 (unseen) | Start in any month, run 12 months |
|---|---|---|
| ₹20k · up to 2 × ≈ ₹7k (index = debit spread, 6 strikes) | 38 trades · win **58%** · PF 1.99 · ₹20k → ₹56k | 70% of 47 runs in profit · median ₹25.5k |
| ₹50k · up to 2 × ≈ ₹20k (plain option buys) | 53 trades · win 49% · PF 2.41 · ₹50k → ₹2.6 L | 72% in profit · median ₹1.08 L |
| ₹20k buying full lots (rejected) | | only 47% in profit: after one loss the next lot is unaffordable |

The ₹20k sizing (₹7k, spread width 6) was compared on both periods, so it is less "unseen" than the signals.
Selling books: day filters (smart money, ADX, RSI, squeeze) did **not** improve them in both periods → unchanged
(`research/sell_filters.py`). India VIX was tested on both sides (`research/vix_research.py`, `sell_filters.py`):
- **Stock buys skip days when VIX is in the top 20% of its last 60 days** (options too expensive): PF 1.87 → 2.36
  (2022–24) and 1.51 → 1.99 (2025–26), win 43% → 50%, ~70% of trades kept. ₹20k account: 12-month runs in profit
  70% → 74%, runs that fell below half 15% → 9%; ₹50k: 72% → 87%. Index buys: no VIX filter helped in both periods.
- Selling: "skip VIX < 12" raised PF on the expiry books but lowered total profit, so the selling books are unchanged.
- Intraday option buying (ORB, Supertrend, pullback) already lost in both periods on every index (`summary.md`).

During market hours the dashboard shows a **live estimate** for open buy positions
(implied vol from the last real NSE close, re-priced with the live underlying from Upstox candles, `live.py`).

### Greeks and the optional free Upstox token

Every buy signal and open position shows **θ (theta)** = ₹ the position loses per trading day just from time passing
(1 lot) and **Δ (delta)** = how much the option moves per 1-point move of the index / share. They are computed from
the option's real NSE close (implied volatility → Black-Scholes). A debit spread has a much smaller θ than a plain buy
(e.g. Nifty PE: −₹790/day plain vs −₹138/day as a 6-strike spread), which is why the ₹20k account uses spreads.
Add a free **Upstox Analytics token** (read-only, 1 year) as `UPSTOX_ANALYTICS_TOKEN` in `.env` and open positions use
REAL live option prices + Upstox greeks (`live.option_chain`); without it they are estimates. The token can't place orders.

## 3. Run it

```bash
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
cp .env.example .env                 # add TELEGRAM_TOKEN / TELEGRAM_CHAT_ID (optional)
.venv/bin/python -m pytest -q tests  # 26 tests
.venv/bin/python bot.py              # paper bot (sleeps outside market hours)
.venv/bin/python dashboard.py        # http://localhost:8002
.venv/bin/python bot.py --status     # paper results so far (selling + buying books)
.venv/bin/python bot.py --evening    # run the option-buying evening job now (normally automatic after 18:15 IST)
.venv/bin/python fno.py              # download NSE F&O + FII/DII files since 2022 (~700 MB, only for research)
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
| `books.json` | The paper strategy books (option selling, intraday) |
| `buy_books.json` | Option-BUYING strategies + combined paper accounts (evening signal → next-day open) |
| `fno.py` | Free NSE data: daily F&O bhavcopy (all strikes, OI) + FII/DII participant positions + lot sizes |
| `swing.py` | Buying engine: OI / buildup / FII / trend signals, real-price trade simulator, debit spreads, small-account mode |
| `swing_bot.py` | Evening job for the buying books: settle trades, plan for tomorrow, OI scanner, Telegram |
| `live.py` | Live price of indices / shares (Upstox 1-minute candles) and live estimates for open buy positions |
| `news.py` | Live headlines from 7 business channels (ET, Moneycontrol, Business Standard, Mint, CNBC-TV18, NDTV Profit, BusinessLine) + per-symbol Google News, live TV list, GDELT daily news tone, simple mood score |
| `research/` | Calibration, expiry-day validation, walk-forward research + results in `research/out/` (selling: `summary.md`, buying: `buy_summary.md`, `buy_grid.csv`, `buy_history.json`) |
| `tests/` | Option maths, trading clock, charges, simulators (selling + buying rules), FII file parsing, news mood |

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
