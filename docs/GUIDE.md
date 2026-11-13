# Upstox Bot: Complete Guide

**What it is, how it works, the strategies, the research, and how it is deployed**

Version 1.1 · November 2026 · built by $um!t_K@Ktw@n · for Indian index options, stock options and shares (NSE / BSE) with free Upstox data

> **Current mode: PAPER TRADING ONLY.** This bot has **no order code at all**: it cannot place a trade on Upstox or
> anywhere else. It simulates every trade with real prices and the exact rules it would use with real money, and
> records the results. The optional Upstox token it uses is a read-only *Analytics* token that cannot place orders.

### What changed in version 1.1 (13 Nov 2026)

| Change | Details |
|---|---|
| **Stocks tab: share swing trading** | 12-month momentum, top 3 shares, decided every 21 trading days, −15% stop, ₹20k paper account (chapter 6) |
| **Live calls in market hours** | 09:15 Telegram call for the day's buys; instant alert when an open buy hits its stop or target (chapter 12) |
| **Every call shows Entry · SL · Target · Time** | On every card, table and Telegram message (chapter 7) |
| **Option-chain recorder** | Saves the live Nifty / Bank Nifty option chain every 5 minutes, building our own intraday history (chapter 10) |
| **Live dashboard** | Prices and the forming candle update every 2 s; closing-auction notice after 15:15 (chapter 11) |
| **India VIX filter on stock-option buys** | PF 1.51 → 1.99 on unseen 2025–26 data (chapter 8) |
| **Tested and rejected** | Buying at the close on a live call; a VIX filter for index buys and for selling; stock pullback and breakout rules (chapter 9) |
| **Fixes** | Server candle dates (pandas 3 stored dates in µs → 1970 dates); atomic cache writes shared by the two containers; share news searched by company name |

---

## Contents

1. What the bot does, in one page
2. The big picture: parts and how they connect
3. One day of the bot, hour by hour
4. Selling strategy: hedged short straddle
5. Option buying: four signals for a small account
6. Stocks: share swing trading (momentum)
7. Every call: Entry · SL · Target · Time
8. Research results: what the data says
9. What did not work (tested and rejected)
10. Data sources (all free) and the option-chain recorder
11. The dashboard
12. Live calls and Telegram alerts
13. Money: the paper accounts, with worked examples
14. Security
15. Deployment: Oracle Cloud + Tailscale
16. Daily use: commands
17. Configuration reference (`.env`)
18. The plan: from paper to real money
19. Troubleshooting and FAQ
20. Glossary

---

## 1. What the bot does, in one page

The bot paper-trades three things, because each needs a different amount of money and time.

| | **Selling** (intraday) | **Option buying** (swing, 1–8 days) | **Shares** (swing, 1–3 months) |
|---|---|---|---|
| What | Sell the at-the-money call + put (a **straddle**), buy cheap **wings** 10 strikes away as insurance | Buy a call (CE) or put (PE), or a cheap **debit spread**, when a tested signal fires | Buy the **3 strongest large-cap shares** (12-month momentum) |
| Where | Nifty, Sensex, Bank Nifty, Midcap Nifty | Nifty, Bank Nifty and 20 large stocks (monthly options) | Nifty 50 + 20 large F&O stocks |
| When | Enter 10:00, stop per leg, exit 15:15 | Signal after the close, buy at the next 09:15 open | Decided every 21 trading days at the close, bought at the next open |
| Money | Large margin (₹1 lakh+ per lot even hedged) | From **₹20,000** | From **₹20,000** |
| Why it earns | Premium decays to near zero by expiry; wings cap bad days | Follows big-money (FII/DII) positioning, breakouts, trend starts | Strong shares tend to keep outperforming for months |
| Paper records | 6 books (`books.json`) | ₹20k and ₹50k accounts (`buy_books.json`) | ₹20k account (`stocks.py`) |

The bot does **not** predict the future. Every rule was chosen on older data and then checked on newer data it never
saw. Only rules that made money in **both** periods were kept. Everything stays **paper** until real results match
the research (chapter 18).

---

## 2. The big picture: parts and how they connect

```
  Upstox API (free)                  NSE archives (free)            News (free)
  candles · instruments · VIX        F&O daily file (bhavcopy)      RSS · GDELT tone
  + Analytics token (read-only):     FII/DII/Pro/Client positions   YouTube live TV
  live quotes · option chain
              │                                │                          │
              ▼                                ▼                          ▼
   market.py  live.py  recorder.py           fno.py                    news.py
              │                                │
              ▼                                ▼
          engine.py  options.py ◄────── swing.py / swing_bot.py      stocks.py / stock_bot.py
       (straddle sells, pricing)        (option buying, evening)      (shares, evening)
              │                                │                          │
              └──────────────► bot.py ◄────────┴──────────────────────────┘
                 (24/7 loop: selling every 20 s · alerts.py live calls · recorder every 5 min ·
                  evening jobs from 18:15)
                               │
             state/*.json  logs/*.csv  data/          notifier.py ──► Telegram (your phone)
                               │
                               ▼
                         dashboard.py + dashboard.html ◄── browser / phone (Tailscale link)
                         (read-only, live ticks every 2 s)
```

**Two programs, one set of files.** `bot.py` simulates every trade and writes `state/`, `logs/` and `data/`.
`dashboard.py` only **reads** them plus public prices, so the page can never change anything. On the server each runs
in its own Docker container; both share the same folders, so cache files are written atomically (a reader never sees
a half-written file).

### 2.1 Tech stack at a glance

| Layer | Technology | Where |
|---|---|---|
| Language | **Python 3.11** (bot, research, tests, web server) · HTML/CSS/JavaScript (dashboard page only) | all `*.py`, `dashboard.html` |
| Data and maths | **pandas**, **NumPy** (works on pandas 2 and 3) | everywhere |
| Option pricing | **Black-Scholes**, implied volatility, greeks (Δ, Γ, θ, vega), real Indian F&O charges | `options.py` |
| Market data | **Upstox public API v3** (index and share candles, instrument list); **Upstox Analytics token** for live quotes and the option chain | `market.py`, `live.py`, `stocks.py` |
| Exchange files | **NSE bhavcopy** (daily option prices, OI), **participant-wise OI** (FII/DII/Pro/Client) | `fno.py` |
| News | RSS (Economic Times, Moneycontrol, Business Standard, Mint, CNBC-TV18, NDTV Profit, BusinessLine), **GDELT** tone, YouTube live | `news.py` |
| Storage | **JSON** (state), **CSV** (journals, recorder), **pickle** caches; no database | `state/`, `logs/`, `data/` |
| Web dashboard | Python `http.server` + **TradingView Lightweight Charts**; read-only, strict security headers | `dashboard.py`, `dashboard.html` |
| Alerts | **Telegram Bot API** (same bot as delta_bot) | `notifier.py`, `alerts.py` |
| Tests | **pytest**, 41 tests | `tests/` |
| Hosting | **Docker + Docker Compose** on **Oracle Cloud Always Free** (Mumbai), private access through **Tailscale** | `Dockerfile`, `docker-compose.yml` |
| Source | **Git + GitHub**, `Sumit211995/upstox_bot`; the server pulls from GitHub | |

| File | Lines | Job |
|---|---|---|
| `bot.py` | ~310 | Main loop: selling books every 20 s, live calls, recorder, evening jobs (options, then shares). |
| `engine.py` | ~415 | Selling simulator (hedged straddle, per-leg stop), expiry calendar, intraday research families. |
| `options.py` | ~145 | Black-Scholes, implied volatility, greeks, model volatility from VIX, Indian F&O charges. |
| `market.py` | ~225 | Upstox candles (5-minute since 2022, cached), intraday, VIX daily, instrument list, safe timestamps, atomic cache writes. |
| `live.py` | ~155 | Live quotes and option chain with the token; live estimate for open buys without it. |
| `fno.py` | ~245 | NSE F&O daily files and participant positions, cached. |
| `swing.py` | ~655 | Option-buying features, signal families, trade simulator, combined accounts. |
| `swing_bot.py` | ~355 | Option-buying evening job: tonight's signals and contracts, paper accounts, Telegram plan. |
| `stocks.py` | ~555 | Share data (daily since 2018), indicators, delivery charges, simulator, momentum ranking, paper account. |
| `stock_bot.py` | ~150 | Share evening job: paper step, ranking, news, Telegram calls. |
| `alerts.py` | ~130 | 09:15 calls and the live stop / target watch. |
| `recorder.py` | ~85 | Option-chain snapshots every 5 minutes. |
| `news.py` | ~210 | Headlines per channel / symbol / company, mood score, GDELT tone, live TV ids. |
| `dashboard.py` + `dashboard.html` | ~500 + ~1,350 | The web page and its read-only API. |
| `books.json`, `buy_books.json` | | Selling books; option-buying strategies and accounts. |
| `research/` | | Every research script and its results (`research/out/`). |

---

## 3. One day of the bot, hour by hour

| Time (IST) | What happens |
|---|---|
| 09:15 | Market opens. Prices update every **2 s**. **Live call** on Telegram: today's option buys and share orders (bought at this open). |
| 09:15–15:15 | Every 5 min: option-chain snapshot saved. Every minute: open option buys are priced live; a **stop or target hit is sent at once**. |
| **10:00** | Selling books enter: sell ATM CE + PE, buy wings 10 strikes away (only on the days each book trades). |
| 10:00–15:15 | Every 20 s: each short leg checked against its stop; a stopped leg is closed, the other stays. |
| **15:15** | Selling books close everything. Indices freeze for the **closing auction** (since Aug 2026) until ~15:30. |
| 15:30 | Market closes. Day summary to Telegram. |
| **18:15 →** | Evening jobs (retry every 15 min until NSE publishes, last try 23:45): **shares** (paper step, ranking, calls) and **option buying** (stops / targets / time exits on real NSE prices, tonight's signals and contracts). New calls go to Telegram. |

---

## 4. Selling strategy: hedged short straddle

**The trade.** At 10:00 sell the **at-the-money** call and put of the nearest expiry and buy the call and put **10
strikes further out** (the wings). You collect premium; the wings cap the loss if the index runs far in one direction.

**The exits.** Each sold leg has its own stop: if its premium rises by the book's stop % (60–100%), that leg is bought
back. At 15:15 everything is closed. **Intraday only.**

| Book | Days traded | Stop per sold leg |
|---|---|---|
| Nifty · expiry day | Weekly expiry (Tuesday) | 80% |
| Nifty · every day | Every day of the weekly cycle | 100% |
| Sensex · expiry day | Weekly expiry (Thursday, BSE) | 60% |
| Sensex · every day | Every day of the weekly cycle | 80% |
| Bank Nifty · expiry day | Monthly expiry only (≈ 1 trade a month) | 100% |
| Midcap Nifty · expiry day | Monthly expiry only | 100% |

---

## 5. Option buying: four signals for a small account

All option-buying signals are computed **after the close** from the day's final data, and bought at the **next
morning's open**. Buying at the close on a live call instead was tested and lost money (chapter 9).

| # | Signal | Rule | Contract | SL · Target · Time |
|---|---|---|---|---|
| 1 | **Index smart money** | Average 60-day rank of five NSE participant measures (DII futures share, DII futures change, FII options net, *against* DII options net, *against* retail futures change). Top 20% → CE, bottom 20% → PE. | Nifty / Bank Nifty ATM, ≥ 25 days to expiry | −50% · none (exit day 5) · swing ≤ 5 days |
| 2 | **Index 20-day breakout (1:2)** | Close above the 20-day high → CE; below the 20-day low → PE. | 1 strike in the money | −35% · +70% · swing ≤ 8 days |
| 3 | **Index EMA 9/21 cross (1:2)** | EMA 9 crosses EMA 21 in the direction of EMA 50. | 1 strike in the money | −25% · +50% · swing ≤ 3 days |
| 4 | **Stock smart money** | Same market-wide score on 20 large F&O stocks; **skipped when India VIX is in its top 20% of 60 days**. | Monthly stock option, 1 strike ITM | −30% · none (exit day 5) · swing ≤ 5 days |

Shown but **not traded**: the **OI-extreme** signal (the "30:70 open interest" idea as a 60-day extreme of call:put OI
within ±2% of the price). It works only with one exact setting, so it is not trusted.

**Why smart money works.** NSE publishes every evening how many futures and options contracts **FII** (foreign
funds), **DII** (Indian funds), **Pro** (brokers' own) and **Client** (retail) hold. Following DII futures and FII
options, and going *against* retail futures, pointed the right way. One group alone did not (FII futures alone:
PF 0.70).

**Greeks.** Cards show **θ** (₹ lost per day from time passing) and **Δ** (move per 1 index point) as information.
The ₹20k account uses **debit spreads** because they cut θ (e.g. −₹790/day plain vs −₹138/day as a 6-strike spread).

---

## 6. Stocks: share swing trading (momentum)

**The rule (`stocks.FINAL`).**

1. Universe: the Nifty 50 + the 20 large F&O stocks, daily prices since 2018 (already split / bonus adjusted).
2. Every **21 trading days** at the close, rank the shares by their return from **12 months ago to 1 month ago**,
   keeping only shares above their **200-day EMA**.
3. Hold the **top 3**, equal weight, whole shares only. Sell anything that dropped out of the top 3.
4. **Market filter:** if Nifty is below its own 200-day EMA on a decision day, sell everything and stay in cash.
5. **Stop loss −15%** below the entry. **No fixed target**: a winner is held while it stays in the top 3.
6. Orders decided at the close are filled at the **next 09:15 open**. Delivery charges (STT, stamp, exchange, GST,
   ₹20 DP charge per sale) and 0.1% slippage are included.

**Why it works.** Shares that have risen strongly for a year tend to keep outperforming for several more months
(momentum). Skipping the last month avoids buying a short-term spike; the market filter avoids holding shares through
a broad bear market.

**Read the limits.** Most of the gain came in 2021 and 2024; it trailed Nifty in 4 of 8 years; the share list is
**today's** Nifty 50, which includes past winners and flatters history; only 26 trades in the unseen period.

---

## 7. Every call: Entry · SL · Target · Time

Every buy or sell call shows the same four fields: on every dashboard card, in the signals tables and in every
Telegram message.

| Call | Entry | SL | Target | Time |
|---|---|---|---|---|
| Index option buy | next 09:15 open (≈ last close price) | option price −50 / −35 / −25% | +70 / +50%, or none (exit on day N) | Swing · ≤ 5 / 8 / 3 days |
| Stock option buy | next 09:15 open | −30% | none (exit on day 5) | Swing · ≤ 5 days |
| Straddle sell | 10:00 · credit ₹ | each sold leg's stop price | time decay · exit 15:15 | **Intraday** |
| Share buy | next 09:15 open (ref = last close) | −15% from entry | none fixed (top-3 rule) | **Swing · 1–3 months** |
| Open holdings | your entry price and date | live stop | target | days held so far |

Example Telegram call:

```
📈 STOCK CALL · tomorrow's open (paper, ₹20,000 momentum account)
• BUY SHRIRAMFIN
  Entry: tomorrow's open (ref ₹1,003.80)
  SL: ₹853.23 (−15% from entry)
  Target: none fixed: held while in the top 3 (reviewed every 21 trading days)
  Time: Swing · about 1–3 months
```

---

## 8. Research results: what the data says

**Method.** Real NSE daily option prices for option buying; modelled premiums checked against NSE prices for intraday
selling; real daily share prices for shares. Lot sizes, brokerage, STT, exchange + SEBI charges, stamp duty, GST and
slippage are included. **Settings chosen on the older period, judged on the newer one.** If a stop and a target were
both touched on the same day, the **stop is assumed first**.

### 8.1 Selling books (1 lot; tuned 2022–24, unseen 2025–26)

| Book | 2022–24: trades · win · PF | **2025–26: trades · win · PF · net** |
|---|---|---|
| Nifty · expiry day | 151 · 60% · 2.02 | **101 · 58% · 2.50 · +₹1.25 L** |
| Nifty · every day | 723 · 69% · 1.91 | **461 · 66% · 1.96 · +₹2.56 L** |
| Sensex · expiry day | 80 · 70% · 2.37 | **91 · 76% · 1.66 · +₹65k** |
| Sensex · every day | 405 · 60% · 1.79 | **461 · 56% · 1.44 · +₹95k** |
| Bank Nifty · expiry day | 155 · 52% · 2.47 | **29 · 59% · 3.48 · +₹62k** |
| Midcap Nifty · expiry day | 149 · 68% · 4.75 | **24 · 71% · 2.23 · +₹39k** |

### 8.2 Option-buying signals (each alone; tuned 2022–24, unseen 2025–26)

| Signal | 2022–24: trades · win · PF | **2025–26: trades · win · PF** |
|---|---|---|
| Index smart money | 18 · 67% · 3.85 | **11 · 55% · 4.06** |
| Index 20-day breakout (1:2) | 36 · 53% · 2.27 | **20 · 50% · 2.03** |
| Index EMA cross (1:2) | 15 · 60% · 3.01 | **15 · 40% · 1.31** |
| Stock smart money (with VIX filter) | 192 · 51% · 2.36 | **117 · 50% · 1.95** |
| OI-extreme (shown only) | 149 · 37% · 1.92 | 73 · 32% · 1.67 |

**India VIX filter (stock options only):** skipping stock-option buys when VIX is in its top 20% of 60 days raised PF
1.87 → 2.36 (2022–24) and 1.51 → 1.99 (2025–26), keeping ~70% of trades.

### 8.3 Combined option accounts

| Account | 2025–26 (unseen) | Start in any month, run 12 months (47 runs) |
|---|---|---|
| ₹20k (spreads) | 37 trades · 60% win · PF 2.1 · ₹20k → ₹58k · lowest ₹15.3k | profit in **74%** · median end ₹27.4k · fell below half 9% |
| ₹50k | 46 trades · 48% win · PF 2.4 · ₹50k → ₹2.38 L | profit in **87%** · median end ₹1.18 L · fell below half 6% |

### 8.4 Shares (₹20k account, max 3 positions; tuned 2019–23, unseen 2024–Nov 2026)

| Family | 2019–23: trades · win · PF · CAGR · worst drop | **2024–26: trades · win · PF · CAGR · worst drop** |
|---|---|---|
| Trend pullback (RSI 2 dip above EMA 200) | 295 · 26% · 0.26 · −50% · −97% | 206 · 24% · 0.14 · −44% · −81% |
| Breakout (20-day high, volume, ATR stop, 1:2) | 191 · 44% · 1.06 · +3% · −48% | 110 · 36% · 0.63 · −15% · −43% |
| **Momentum, top 3, Nifty > EMA 200, −15% stop** | **64 · 50% · 1.85 · +18.9% · −28%** | **26 · 50% · 4.55 · +23.0% · −18%** |
| Nifty 50 buy and hold | +14.8% a year · −38% | +5.4% a year · −16% |

**Stop loss test (momentum):** none → PF 1.70 / 4.55; **−15% → 1.85 / 4.55 (kept: better on 2019–23, same on
2024–26)**; −10% → 1.99 / 3.78 (rejected: worse on 2024–26); −20% / −25% → no gain on 2024–26.

| Year | 2019 | 2020 | 2021 | 2022 | 2023 | 2024 | 2025 | 2026* |
|---|---|---|---|---|---|---|---|---|
| Momentum (₹20k) | −4.5% | +4.1% | +73.5% | +19.9% | +7.9% | +96.4% | −2.9% | +2.3% |
| Nifty 50 | +12.0% | +14.9% | +24.1% | +4.3% | +20.0% | +8.8% | +10.5% | −3.2% |

\*2026 to mid-November.

> **Read these numbers honestly.** Index smart money has only **11** unseen trades and the share strategy **26**:
> real results could be much lower. One in four 12-month runs of the ₹20k option account still lost money. Paper first.

---

## 9. What did not work (tested and rejected)

| Idea | Result |
|---|---|
| Intraday option **buying** (opening-range breakout, Supertrend, pullback) on any index | Lost in both periods on all 4 indices (unseen PF 0.5–0.8) |
| **Buying at the close on a live call** (breakout / EMA signals seen from the live price at ~15:10) | Lost: breakout PF 2.03 → 0.91, EMA 1.31 → 0.50 (2025–26). Entry stays at the next open. |
| Plain stock option buying on price signals | Lost in ~99.6% of settings |
| A fixed **30:70 OI ratio** | Almost never happens on indices; the relative version is only shown |
| **FII futures long %** alone | PF 0.70 |
| **News** as a filter | Made results worse (PF 1.44 → 1.13); shown as context only |
| RSI, MACD, Supertrend, Bollinger squeeze as option signals | Failed on unseen data |
| **VIX** filter on index option buys | No setting improved both periods |
| Day filters for selling (ADX, RSI, smart money, squeeze, VIX) | None improved both periods without cutting total profit ("skip VIX < 12" raised PF but lowered profit) |
| Share **pullback** and **breakout** rules | Lost after delivery charges in both periods |
| Share momentum + VIX filter / + smart-money filter / without the market filter | Worse or no better on unseen data |
| Full-lot option buying with ₹20k | Only 47% of 12-month runs profitable → debit spreads instead |

---

## 10. Data sources (all free) and the option-chain recorder

| Data | Source | Used for |
|---|---|---|
| Index candles (5-minute, since Jan 2022) | Upstox public historical API | Selling simulator, charts |
| Share candles (daily, since 2018) | Upstox public historical API | Share research, Stocks tab, charts |
| Live quotes, option chain + greeks | Upstox **Analytics token** (read-only, 1 year) | Live ticks, live marks, stop / target alerts, recorder |
| Daily option prices + open interest | NSE F&O bhavcopy | Option-buying research and evening job |
| FII / DII / Pro / Client positions | NSE participant-wise OI | Smart-money score |
| FII / DII cash buying | NSE (latest day) | Big money panel |
| India VIX | Upstox | Stock-option filter, option model |
| Company names | Upstox instrument file | Share news ("Titan Company", not "Titan Mining") |
| Headlines | RSS feeds of 7 business outlets + Google News | News panel, share news |
| News tone | GDELT | Context only |
| Live business TV | YouTube (CNBC-TV18, ET Now, NDTV Profit, Zee Business, CNBC Awaaz) | News & TV tab |

**Option-chain recorder (`recorder.py`).** NSE's free files are daily only, so intraday option strategies could never be
tested. Every 5 minutes in market hours the bot now saves the live Nifty and Bank Nifty option chain (2 nearest expiries,
strikes within ±8%: price, volume, open interest, bid/ask, IV and greeks) to `data/chain/<INDEX>/<day>.csv.gz`
(≈ 2 MB a day). After 2–3 months this becomes our own intraday history for open-interest research.

**How to get the Upstox Analytics token:** Upstox developer portal → *Apps* → **Analytics token** → generate (valid one
year) → put it in `.env` as `UPSTOX_ANALYTICS_TOKEN=` on the server and restart. Free and read-only.

---

## 11. The dashboard

Tabs: **Overview · Buying · Selling · Stocks · Big money · News & TV.**

- **Price tiles** update every 2 s and flash on each change. The pill shows *Live · Upstox* (real quotes) or
  *Live · ~1 min* (no token). From 15:15 to 15:30 it shows the closing-auction pause.
- **Chart** (5m / 15m / 1h / 1D): the last candle moves with the live price. Buying tab: buy arrows of the top 3
  buying strategies; Selling tab: straddle trades of the top selling books.
- **Signal cards** (Overview, Buying, Selling): every call with its Entry · SL · Target · Time block and the strategy's
  success rate, best first. Share calls appear in the Overview strip too.
- **Buying tab:** ₹20k / ₹50k accounts, tonight's signals, ranked strategies, all trades, OI scanner.
- **Stocks tab:** account, calls for the next open, holdings with live prices (SL, target, days held), today's
  momentum top 10 (tap a share for its 2-year chart with EMA 50 / 200, buy / sell arrows and news), the research by
  year vs Nifty, closed trades.
- **Big money:** FII / DII / Pro / Client positions and ranks; FII / DII cash.
- **News & TV:** headlines per channel and live TV inside the page.

---

## 12. Live calls and Telegram alerts

| Message | When |
|---|---|
| 🇮🇳 PAPER ENTRY / EXIT (straddle sells) | Live: 10:00 entry, a leg's stop at any moment, 15:15 exit |
| 📋 PLAN (option buys) | Evening, for the next 09:15 open |
| 📈 STOCK CALL (share buys / sells) | Evening, for the next 09:15 open |
| 🔔 LIVE CALL · today's buys | 09:15, repeats the evening's option and share calls |
| 🛑 STOP hit → exit / 🎯 TARGET hit → book profit | Live, the minute an open option buy reaches its level (real Upstox price) |
| Fills, exits, day summary | As they happen / after the close |

Each alert is sent once (remembered across restarts). Paper accounts are settled each evening on NSE's official
prices; the live alerts are the "act now" calls for anyone trading the same idea for real.

---

## 13. Money: the paper accounts, with worked examples

| Account | Capital | Per trade | Max at once |
|---|---|---|---|
| Option buying ₹20k (recommended start) | ₹20,000 | ≈ ₹7,000 (index signals as debit spreads) | 2 |
| Option buying ₹50k | ₹50,000 | ≈ ₹15–20k (plain CE / PE) | 2 |
| Shares ₹20k | ₹20,000 | ≈ ₹6,600 (equity ÷ 3, whole shares) | 3 |

**Option example (₹20k, illustrative).** Smart-money score hits its top 20% → buy Nifty 25,300 CE and sell 25,600 CE.
Spread ≈ ₹110 × 65 = ₹7,150. SL −50% (≈ ₹3,600 loss + charges). Exit by day 5 at the latest. Maximum value = width
300 × 65 = ₹19,500.

**Share example (₹20k).** Call: BUY Shriram Finance, ref ₹1,003.80. At the next open the account spends ≈ ₹6,600:
6 shares at ≈ ₹1,004 (+ charges). SL ₹853 (−15%). Held while it stays in the top 3; reviewed every 21 trading days.

---

## 14. Security

| Rule | How |
|---|---|
| No trading possible | There is no order code; the token is read-only |
| Secrets stay private | `.env` is never committed (`.gitignore`), `chmod 600` on the server; the token is only sent to `api.upstox.com` |
| Public repo, private server | The GitHub repo contains no IPs, tokens or keys |
| Page cannot be attacked from outside | Bound to `127.0.0.1`; reachable only through your private Tailscale network |
| Page hardening | Content-Security-Policy, `X-Frame-Options: DENY`, `nosniff`, only GET allowed, parameter allowlist, http(s)-only links |

---

## 15. Deployment: Oracle Cloud + Tailscale

The bot runs next to delta_bot on the same **Oracle Cloud Always Free** VM (Mumbai, 1 GB RAM + 2 GB swap).

| | delta_bot | upstox_bot |
|---|---|---|
| Folder | `~/delta_bot` | `~/upstox_bot` (a clone of GitHub) |
| Containers | `delta-bot`, `dashboard` | `upstox-bot`, `upstox-dashboard` |
| Dashboard (on the server) | `127.0.0.1:8000` | `127.0.0.1:8002` |
| **Link (phone / laptop with Tailscale)** | `https://<your-server>.<tailnet>.ts.net` | **`https://<your-server>.<tailnet>.ts.net:8443`** |

**Open it on your phone:** install the Tailscale app, sign in with the same account, open the link.

**Update after a code change:**

```
# on the laptop
git push
# on the server
ssh -i ~/Downloads/oracle.key ubuntu@<server-ip>
cd ~/upstox_bot && git pull && docker compose up -d --build
```

`.env`, `state/`, `logs/` and `data/` are not in Git and are never touched by an update.

**Memory and disk.** The two containers use ≈ 230 MB. The option evening job briefly needs more and runs ~10–15
minutes on the small server (swap); the share job takes ~20 s. Never start a second evening job by hand while the
bot's own one is running. The recorder adds ≈ 2 MB a day (~0.5 GB a year).

**Where to check it is running:** the Oracle console only shows the VM (CPU / memory). The bots: the dashboard link,
Telegram "bot started", or on the server `docker compose ps` and `docker compose logs --tail 30`.

---

## 16. Daily use: commands

| Command | What it does |
|---|---|
| `python bot.py` | Run the bot (loop) |
| `python bot.py --status` | Paper results so far |
| `python bot.py --evening` | Run the option-buying evening job now (only if the bot is not already running it) |
| `python -c "import stock_bot; stock_bot.run()"` | Run the share job now (also only when the bot is not running it) |
| `python dashboard.py` | Dashboard on http://localhost:8002 |
| `python -m pytest -q` | Run the 41 tests |
| `python research/buy_final.py` | Rebuild the option-buying history shown on the dashboard |
| `python research/stock_final.py` | Rebuild the share research shown on the Stocks tab |
| `python docs/build_pdf.py` | Rebuild this PDF |

---

## 17. Configuration reference (`.env`)

| Key | Default | Meaning |
|---|---|---|
| `INDICES` | `NIFTY,BANKNIFTY,SENSEX,MIDCPNIFTY` | Indices shown and traded by the selling books |
| `PAPER_CAPITAL` | `200000` | Paper capital per selling book (₹) |
| `LOTS` | `1` | Lots per selling trade |
| `POLL_SECONDS` | `20` | Check interval in market hours |
| `TELEGRAM_TOKEN`, `TELEGRAM_CHAT_ID` | empty | Telegram alerts (optional) |
| `UPSTOX_ANALYTICS_TOKEN` | empty | Live quotes, option chain, live alerts, recorder (optional, read-only) |

Put comments on **their own line** in `.env`, never after a value (`KEY=   # note`): Docker reads the comment as the
value.

---

## 18. The plan: from paper to real money

1. **Paper for 1–2 months** (now). Watch the option accounts, the selling books and the share account.
2. **Compare with the research.** Go further only if paper results look roughly like chapter 8: at least ~15 option
   buys and a few share rebalances, profit factor above ~1.3, no rule surprises.
3. **Start small.** Real option buying with ₹20k and/or the share account with ₹20k, only for the parts that held up
   on paper; selling only with enough margin.
4. **Intraday open-interest research** once the recorder has 2–3 months of option-chain history.
5. **Real orders are not built.** They would need Upstox's order API with a separate trading token, plus a safety
   layer like delta_bot's (daily loss limit, kill switch). That is a deliberate later step.

---

## 19. Troubleshooting and FAQ

| Question | Answer |
|---|---|
| Index price stops moving at 15:15 | Normal since Aug 2026: closing auction. VIX keeps moving. |
| No option buy signals tonight | Normal: they fire a few times a month. |
| Stocks tab says "Next decision: tonight" / no calls | The share account decides every 21 trading days (or when the market filter flips); calls appear after that evening's run. |
| Why does a share call have no target? | The tested rule keeps a winner while it stays in the top 3; a fixed target cut the big winners. It has a −15% stop. |
| Why no option buys on Sensex / Midcap? | Option-buying research used NSE data (Nifty, Bank Nifty); Sensex is on BSE. |
| Dashboard says "Live · ~1 min" | The Upstox token is missing or expired (one year); generate a new one. |
| An evening job did not run | NSE publishes late some days; the bot retries every 15 minutes until 23:45. |
| Server CPU near 100% in the evening | The option evening job; it finishes in ~15 minutes. |
| Paper trades are not in my Upstox app | Correct: they are simulated; see the dashboard and Telegram. |
| Old dashboard on the laptop shows no trades | The bot runs on the server now; use the Tailscale link. |

---

## 20. Glossary

| Term | Meaning |
|---|---|
| CE / PE | Call option (profits when the price rises) / put option (profits when it falls) |
| ATM / ITM / OTM | Strike at / inside / outside the current price |
| Straddle | Selling (or buying) a CE and a PE at the same strike |
| Wings | Far-away options bought as insurance on a short straddle |
| Debit spread | Buy one option, sell a cheaper one further out: lower cost and decay, capped profit |
| Momentum | Ranking shares by how much they rose over the past 12 months (skipping the last month) |
| EMA 200 | 200-day exponential moving average: above it = long-term uptrend |
| Rebalance / decision day | Every 21 trading days the share account re-ranks and swaps holdings |
| Delivery | Buying shares to hold for days or months (not intraday) |
| Expiry | Last day of an option contract |
| OI | Open interest: contracts still open |
| FII / DII / Pro / Client | Foreign funds / Indian funds / brokers' own trading / retail |
| PF (profit factor) | Total won ÷ total lost. Above 1 = profitable |
| CAGR | Average yearly growth of the account |
| Walk-forward | Choose settings on old data, judge on newer unseen data |
| VIX | India's volatility index: high = options expensive |
| θ theta / Δ delta | Daily time decay / move per 1 point of the underlying |
| Bhavcopy | NSE's daily file of every contract's prices and open interest |
| Closing auction | Since Aug 2026, NSE/BSE fix closing prices in an auction after 15:15; indices pause until ~15:30 |
