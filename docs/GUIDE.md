# Upstox Bot: Complete Guide

**What it is, how it works, the strategies, the research, and how it is deployed**

Version 1.0 · November 2026 · built by $um!t_K@Ktw@n · for Indian index and stock options (NSE / BSE) with free Upstox data

> **Current mode: PAPER TRADING ONLY.** This bot has **no order code at all**: it cannot place a trade on Upstox or
> anywhere else. It simulates every trade with real prices and the exact rules it would use with real money, and
> records the results. The optional Upstox token it uses is a read-only *Analytics* token that cannot place orders.

---

## Contents

1. What the bot does, in one page
2. The big picture: parts and how they connect
3. One day of the bot, hour by hour
4. Selling strategy: hedged short straddle
5. Buying strategy: four signals for a small account
6. Money: the ₹20k and ₹50k paper accounts, with a worked example
7. Research results: what the data says
8. What did not work (tested and rejected)
9. Data sources (all free)
10. The dashboard
11. Telegram alerts
12. Security
13. Deployment: Oracle Cloud + Tailscale
14. Daily use: commands
15. Configuration reference (`.env`)
16. The plan: from paper to real money
17. Troubleshooting and FAQ
18. Glossary

---

## 1. What the bot does, in one page

The bot paper-trades Indian options in **two separate ways**, because they need very different amounts of money.

| | **Selling** (intraday) | **Buying** (swing, 1–8 days) |
|---|---|---|
| What | Sell the at-the-money call + put (a **straddle**) and buy cheap **wings** 10 strikes away as insurance | Buy a call (CE) or put (PE), or a cheap **debit spread**, when one of four tested signals fires |
| Where | Nifty, Sensex, Bank Nifty, Midcap Nifty | Nifty and Bank Nifty (+ 20 large stocks with monthly options) |
| When | Enter 10:00, stop per leg, exit 15:15 the same day | Signal after the close (evening), buy at the next morning's open, exit on stop / target / time |
| Money needed | Large margin (₹1 lakh+ per lot even hedged) | Starts at **₹20,000** |
| Why it earns | Option premium decays to near zero by expiry; wings cap the bad days | Follows where the big money (FII/DII) is positioned, plus breakouts / trend starts |
| Records | 6 paper "books" (`books.json`) | 2 paper accounts, ₹20k and ₹50k (`buy_books.json`) |

The bot does **not** predict the future. Each rule was chosen on 2022–2024 data and then checked on 2025–2026 data it
never saw. Only rules that made money in **both** periods were kept. Everything is still **paper** until real results
match the research (see chapter 16).

---

## 2. The big picture: parts and how they connect

```
  Upstox API (free)                  NSE archives (free)            News (free)
  candles · instruments · VIX        F&O daily file (bhavcopy)      RSS · GDELT tone
  + Analytics token (read-only):     FII/DII/Pro/Client positions   YouTube live TV
  live quotes · option chain
              │                                │                          │
              ▼                                ▼                          ▼
   market.py  live.py                        fno.py                    news.py
              │                                             │
              ├──────────────┐                              │
              ▼              ▼                              ▼
          engine.py      options.py ◄──────────────── swing.py (buying signals, simulator)
     (straddle simulator, (Black-Scholes, greeks,        swing_bot.py (evening job, accounts)
      expiry calendar)     Indian F&O charges)               │
              │                                             │
              └──────────────► bot.py ◄─────────────────────┘
                     (24/7 loop: intraday selling every 20 s, evening buying job after 18:15)
                               │
             state/*.json  logs/*.csv          notifier.py ──► Telegram (your phone)
                               │
                               ▼
                         dashboard.py + dashboard.html ◄── your browser / phone (Tailscale link)
                         (read-only web page, live ticks every 2 s)
```

**Two programs, one set of files.** `bot.py` simulates the trades and writes `state/` and `logs/`. `dashboard.py` only
**reads** those files plus public prices, so the web page can never change anything. On the server each runs in its
own Docker container and both share the same folders.

### 2.1 Tech stack at a glance

| Layer | Technology | Where |
|---|---|---|
| Language | **Python 3.11** (bot, research, tests, web server) · HTML/CSS/JavaScript (dashboard page only) | all `*.py`, `dashboard.html` |
| Data and maths | **pandas**, **NumPy** | everywhere |
| Option pricing | **Black-Scholes** model, implied volatility, greeks (Δ, Γ, θ, vega); real Indian F&O charges | `options.py` |
| Market data | **Upstox public API v3** (candles, instrument list); **Upstox Analytics token** for live quotes + option chain | `market.py`, `live.py` |
| Exchange files | **NSE bhavcopy** (daily option prices, OI), **participant-wise OI** (FII/DII/Pro/Client) | `fno.py` |
| News | RSS (Economic Times, Moneycontrol, Business Standard, Mint, CNBC-TV18, NDTV Profit, BusinessLine), **GDELT** tone, YouTube live | `news.py` |
| Storage | Plain **JSON** (state) and **CSV** (journals) + **pickle** caches; no database | `state/`, `logs/`, `data/` |
| Web dashboard | Python's built-in `http.server` + **TradingView Lightweight Charts**; read-only, strict security headers | `dashboard.py`, `dashboard.html` |
| Alerts | **Telegram Bot API** (same bot as delta_bot) | `notifier.py` |
| Tests | **pytest**, 28 tests | `tests/` |
| Hosting | **Docker + Docker Compose** on **Oracle Cloud Always Free** (Mumbai), private access through **Tailscale** | `Dockerfile`, `docker-compose.yml` |
| Source | **Git + GitHub** (private repo `upstox_bot`) | |

| File | Lines | Job |
|---|---|---|
| `bot.py` | ~255 | Main loop. Every 20 s in market hours replays each selling book on today's candles; after 18:15 runs the buying evening job. |
| `engine.py` | ~415 | Selling simulator (hedged straddle, per-leg stop), expiry calendar, older intraday buying families (research). |
| `options.py` | ~145 | Black-Scholes, implied volatility, greeks, model volatility from VIX, Indian F&O charges. |
| `market.py` | ~205 | Upstox candles (5-minute history since 2022, cached), intraday, instrument list, India VIX daily. |
| `live.py` | ~160 | Live quotes and option chain with the Analytics token; live estimate for open buy positions without it. |
| `fno.py` | ~245 | NSE F&O daily files and participant positions (FII/DII/Pro/Client), cached. |
| `swing.py` | ~650 | Buying features (OI, smart money, EMA, RSI, ADX, breakouts, VIX), the signal families, the trade simulator, accounts. |
| `swing_bot.py` | ~350 | The evening job: builds tonight's signals and contracts, updates the paper accounts, journals, Telegram. |
| `news.py` | ~210 | Headlines per channel / symbol, mood score, GDELT tone, live TV video ids. |
| `dashboard.py` + `dashboard.html` | ~440 + ~1,210 | The web page and its read-only API (`/api/live`, `/api/buying`, `/api/chart`, …). |
| `config.py`, `notifier.py` | | Settings from `.env`; Telegram. |
| `books.json`, `buy_books.json` | | The selling books and the buying strategies / accounts. |
| `research/` | | Every research script and its results (`research/out/`). |

---

## 3. One day of the bot, hour by hour

| Time (IST) | What happens |
|---|---|
| 09:15 | Market opens. Dashboard prices update every **2 seconds**. Yesterday's buy signals are bought at the **opening price**. |
| 09:15–15:15 | Open buy positions are marked live (real option price with the token, an estimate without it). |
| **10:00** | Selling books enter: sell ATM CE + PE, buy wings 10 strikes away (only on the days each book trades). |
| 10:00–15:15 | Every 20 s: check each short leg against its stop. A stopped leg is closed; the other leg stays. |
| **15:15** | Selling books close everything. Indices freeze for the **closing auction** (since Aug 2026) until ~15:30. |
| 15:30 | Market closes. |
| **18:15 →** | Evening job (retries every 15 minutes until NSE has published the day's files, last try 23:45): downloads the F&O file and FII/DII positions, updates the buying accounts (stops, targets, time exits on real prices), computes **tonight's signals** and the exact contracts to buy tomorrow, sends them to Telegram. |

---

## 4. Selling strategy: hedged short straddle

**The trade.** At 10:00 sell the **at-the-money** call and put of the nearest expiry and buy the call and put **10
strikes further out** (the wings). You collect premium; the wings cap the loss if the index runs far in one direction.

**The exits.** Each short leg has its own stop: if its premium rises by the book's stop % (60–100%), that leg is bought
back. At 15:15 everything is closed.

**Why it works.** An option's time value melts fastest on expiry day. On most days the index moves less than the
premium collected. Stops + wings keep the rare big trend day from wiping out many good days.

| Book | Days traded | Stop per leg |
|---|---|---|
| Nifty · expiry day | Weekly expiry (Tuesday) | 80% |
| Nifty · every day | Every day of the weekly cycle | 100% |
| Sensex · expiry day | Weekly expiry (Thursday, BSE) | 60% |
| Sensex · every day | Every day of the weekly cycle | 80% |
| Bank Nifty · expiry day | Monthly expiry only (≈ 1 trade a month) | 100% |
| Midcap Nifty · expiry day | Monthly expiry only | 100% |

---

## 5. Buying strategy: four signals for a small account

All buying signals are computed **after the close** from the day's final data, and bought at the **next morning's
open**. This removes any chance of using information that was not yet available.

### 5.1 The signals (in priority order)

| # | Signal | Rule | Contract | Stop · target · max days |
|---|---|---|---|---|
| 1 | **Index smart money** | Average 60-day rank of five NSE participant measures (DII futures share, DII futures change, FII options net long, *against* DII options net, *against* retail futures change). Top 20% → CE, bottom 20% → PE. | Nifty / Bank Nifty, ATM, ≥ 25 days to expiry | −50% · none · 5 |
| 2 | **Index 20-day breakout (1:2)** | Close above the highest high of 20 days → CE; below the lowest low → PE. | 1 strike in the money | −35% · +70% · 8 |
| 3 | **Index EMA 9/21 cross (1:2)** | EMA 9 crosses EMA 21 in the direction of EMA 50. | 1 strike in the money | −25% · +50% · 3 |
| 4 | **Stock smart money** | The same market-wide score applied to 20 large F&O stocks. **Skipped when India VIX is in the top 20% of its last 60 days** (options too expensive). | Monthly stock option, 1 strike ITM | −30% · none · 5 |

Shown on the dashboard but **not traded**: the **OI-extreme** signal (the original "30:70 open-interest" idea, measured
as a 60-day extreme of call:put OI within ±2% of the price). It works only with one exact setting, so it is not trusted.

### 5.2 Why "smart money" works

NSE publishes every evening how many futures and options contracts each group holds: **FII** (foreign funds),
**DII** (Indian funds), **Pro** (brokers' own trading) and **Client** (retail). Research found that following DII
futures and FII options, and going *against* retail futures, pointed the right way over the next days. One group alone
did not work (FII futures alone: PF 0.70); the five-part score did (PF 3.85 tuning, 4.06 unseen).

### 5.3 Greeks: do we need them?

The cards show **θ (theta)**, what the position loses per day just from time passing, and **Δ (delta)**, how much it
moves per 1 point of the index. They are information, not rules. The ₹20k account uses **debit spreads** mainly
because they cut theta: e.g. a Nifty PE lost −₹790/day as a plain buy vs −₹138/day as a 6-strike spread.

---

## 6. Money: the ₹20k and ₹50k paper accounts, with a worked example

Both accounts take signals in priority order, at most **2 positions at once**, and only if the cash is there.

| Account | Capital | Per trade | Index signals bought as |
|---|---|---|---|
| **₹20k (recommended start)** | ₹20,000 | ≈ ₹7,000 | **Debit spread**: buy ATM/ITM, sell the option 6 strikes further out |
| ₹50k | ₹50,000 | ≈ ₹15–20k | Plain CE / PE |

**Worked example (₹20k, illustrative numbers).** Smart-money score hits its top 20% on Nifty in the evening →
tomorrow buy Nifty 25,300 CE and sell 25,600 CE (6 strikes × 50). Say the spread costs ₹110 × 65 units = ₹7,150. The
stop is −50% (spread worth ₹55 → loss ≈ ₹3,600 + charges). The trade is closed after 5 trading days at the latest. The
maximum the spread can ever be worth is the width, 300 points × 65 = ₹19,500.

---

## 7. Research results: what the data says

**Method.** Every trade was simulated as a real option trade: real NSE daily option prices (OHLC) for buying,
modelled premiums checked against NSE prices for intraday selling, lot sizes, brokerage, STT, exchange + SEBI charges,
stamp duty, GST and slippage (0.5% index, 1.5% stocks). **Settings chosen on 2022–2024, judged on 2025–2026.**
If both stop and target were touched on the same day, the **stop is assumed first** (pessimistic).

### 7.1 Selling books (1 lot)

| Book | 2022–24: trades · win · PF | **2025–26 (unseen): trades · win · PF · net** |
|---|---|---|
| Nifty · expiry day | 151 · 60% · 2.02 | **101 · 58% · 2.50 · +₹1.25 L** |
| Nifty · every day | 723 · 69% · 1.91 | **461 · 66% · 1.96 · +₹2.56 L** |
| Sensex · expiry day | 80 · 70% · 2.37 | **91 · 76% · 1.66 · +₹65k** |
| Sensex · every day | 405 · 60% · 1.79 | **461 · 56% · 1.44 · +₹95k** |
| Bank Nifty · expiry day | 155 · 52% · 2.47 | **29 · 59% · 3.48 · +₹62k** |
| Midcap Nifty · expiry day | 149 · 68% · 4.75 | **24 · 71% · 2.23 · +₹39k** |

### 7.2 Buying signals (each alone)

| Signal | 2022–24: trades · win · PF | **2025–26: trades · win · PF** |
|---|---|---|
| Index smart money | 18 · 67% · 3.85 | **11 · 55% · 4.06** |
| Index 20-day breakout (1:2) | 36 · 53% · 2.27 | **20 · 50% · 2.03** |
| Index EMA cross (1:2) | 15 · 60% · 3.01 | **15 · 40% · 1.31** |
| Stock smart money (VIX filter) | 192 · 51% · 2.36 | **117 · 50% · 1.95** |
| OI-extreme (shown only) | 149 · 37% · 1.92 | 73 · 32% · 1.67 |

### 7.3 Combined accounts

| Account | 2025–26 (unseen) | Start in any month, run 12 months (47 runs) |
|---|---|---|
| ₹20k (spreads) | 37 trades · 60% win · PF 2.1 · ₹20k → ₹58k · lowest ₹15.3k | profit in **74%** · median end ₹27.4k · fell below half 9% |
| ₹50k | 46 trades · 48% win · PF 2.4 · ₹50k → ₹2.38 L | profit in **87%** · median end ₹1.18 L · fell below half 6% |

> **Read these numbers honestly.** Index smart money has only **11** unseen trades: its PF could be much lower in
> reality. One in four 12-month runs of the ₹20k account still lost money. Paper first.

---

## 8. What did not work (tested and rejected)

| Idea | Result |
|---|---|
| Intraday option **buying** (opening-range breakout, Supertrend, pullback) on any index | Lost in both periods on all 4 indices (unseen PF 0.5–0.8) |
| Plain stock option buying on price signals | Lost in ~99.6% of settings |
| A fixed **30:70 OI ratio** | Almost never happens on indices; the relative (60-day) version is only shown |
| **FII futures long %** alone | PF 0.70 |
| **News** as a filter | Made results worse (PF 1.44 → 1.13); news tone is shown as context only |
| RSI, MACD, Supertrend, Bollinger squeeze as buying signals | Failed on unseen data |
| **VIX** filter on index buys | No setting improved both periods |
| Day filters for selling (ADX, RSI, smart money, squeeze, VIX) | None improved both periods without cutting total profit |
| Full-lot buying with ₹20k | Only 47% of 12-month runs profitable → debit spreads instead |

---

## 9. Data sources (all free)

| Data | Source | Used for |
|---|---|---|
| Index candles (5-minute, since Jan 2022) | Upstox public historical API | Selling simulator, charts |
| Live quotes, option chain + greeks | Upstox **Analytics token** (read-only, 1 year, free) | Dashboard ticks, live marks |
| Daily option prices + open interest | NSE F&O bhavcopy | Buying research and the evening job |
| FII / DII / Pro / Client positions | NSE participant-wise OI | Smart-money score |
| FII / DII cash buying | NSE (latest day) | Big money panel |
| India VIX | Upstox | Stock-buy filter, option model |
| Headlines | RSS feeds of 7 business outlets | News panel |
| News tone | GDELT | Context only |
| Live business TV | YouTube (CNBC-TV18, ET Now, NDTV Profit, Zee Business, CNBC Awaaz) | News & TV tab |

**How to get the Upstox Analytics token:** log in at the Upstox developer portal → *Apps* → **Analytics token** →
generate (valid one year) → paste it as `UPSTOX_ANALYTICS_TOKEN=` in `.env` on the server and restart. It is free and
read-only.

---

## 10. The dashboard

Tabs: **Overview · Buying · Selling · Big money · News & TV.**

- **Price tiles** (Nifty, Bank Nifty, Sensex, Midcap, VIX) update every 2 seconds and flash on each change. The pill
  shows *Live · Upstox* (real quotes) or *Live · ~1 min* (no token). From 15:15 to 15:30 it shows the closing-auction
  pause.
- **Chart** (5m / 15m / 1h / 1D): the last candle moves with the live price. The Buying tab shows buy arrows from the
  top 3 buying strategies (Nifty, daily); the Selling tab shows the straddle trades of the top selling books.
- **Signal cards**: every buy and sell signal with contract, stop, target, θ, Δ and the strategy's success rate, best
  first.
- **Buying tab**: the ₹20k / ₹50k accounts, tonight's signals, ranked strategies, all trades (paper and backtest), OI
  scanner.
- **Big money**: FII / DII / Pro / Client positions and their 60-day ranks; FII / DII cash.
- **News & TV**: headlines per channel and live TV inside the page.

---

## 11. Telegram alerts

The same Telegram bot as delta_bot. Messages: bot started, selling entries / stops / exits, the evening buying plan
(contract, stop, target), buy fills and exits.

---

## 12. Security

| Rule | How |
|---|---|
| No trading possible | There is no order code; the token is read-only |
| Secrets stay private | `.env` is never committed (`.gitignore`), `chmod 600` on the server, the token is only sent to `api.upstox.com` |
| Page cannot be attacked from outside | Bound to `127.0.0.1`; reachable only through your private Tailscale network |
| Page hardening | Content-Security-Policy, `X-Frame-Options: DENY`, `nosniff`, only GET allowed, parameter allowlist, http(s)-only links |

---

## 13. Deployment: Oracle Cloud + Tailscale

The bot runs next to delta_bot on the same **Oracle Cloud Always Free** VM (Mumbai, 1 GB RAM + 2 GB swap).

| | delta_bot | upstox_bot |
|---|---|---|
| Folder | `~/delta_bot` | `~/upstox_bot` |
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

**Memory.** The two Upstox containers use ≈ 230 MB. The evening job briefly needs more and runs ~10–15 minutes on the
small server (it uses swap). Never start a second evening job by hand while the bot's own one is running.

**Where to check it is running:** the Oracle console only shows the VM (CPU / memory graphs). The bots themselves:
the dashboard link, Telegram "bot started" messages, or on the server `docker compose ps` and
`docker compose logs --tail 30`.

---

## 14. Daily use: commands

| Command | What it does |
|---|---|
| `python bot.py` | Run the bot (loop) |
| `python bot.py --status` | Paper results so far |
| `python bot.py --evening` | Run the buying evening job now (only if the bot is not already running it) |
| `python dashboard.py` | Dashboard on http://localhost:8002 |
| `python -m pytest -q` | Run the 28 tests |
| `python research/buy_final.py` | Rebuild the buying history shown on the dashboard |

---

## 15. Configuration reference (`.env`)

| Key | Default | Meaning |
|---|---|---|
| `INDICES` | `NIFTY,BANKNIFTY,SENSEX,MIDCPNIFTY` | Indices shown and traded by the selling books |
| `PAPER_CAPITAL` | `200000` | Paper capital per selling book (₹) |
| `LOTS` | `1` | Lots per selling trade |
| `POLL_SECONDS` | `20` | Check interval in market hours |
| `TELEGRAM_TOKEN`, `TELEGRAM_CHAT_ID` | empty | Telegram alerts (optional) |
| `UPSTOX_ANALYTICS_TOKEN` | empty | Live quotes + option chain (optional, read-only) |

Put comments on **their own line** in `.env`, never after an empty value (`KEY=   # note`): Docker would read the
comment as the value.

---

## 16. The plan: from paper to real money

1. **Paper for 1–2 months** (now). Watch the ₹20k account and the selling books on the dashboard.
2. **Compare with the research.** Go further only if paper results look roughly like chapter 7: at least ~15 buy
   trades, profit factor above ~1.3, no rule surprises.
3. **Start small.** Real buying with ₹20k using only the signals that held up on paper; selling only with enough margin.
4. **Real orders are not built.** Placing orders would need Upstox's order API with a separate trading token, plus the
   same safety layer as delta_bot (daily loss limit, kill switch). That is a deliberate later step.

---

## 17. Troubleshooting and FAQ

| Question | Answer |
|---|---|
| Index price stops moving at 15:15 | Normal since Aug 2026: closing auction. VIX keeps moving. |
| No buy signals tonight | Normal: the signals fire a few times a month. |
| Why no buy signals on Sensex / Midcap? | Buying research used NSE data (Nifty, Bank Nifty); Sensex is on BSE. |
| Dashboard says "Live · ~1 min" | The Upstox token is missing or expired (one year); generate a new one. |
| The evening job did not run | NSE publishes late some days; the bot retries every 15 minutes until 23:45. |
| Server CPU near 100% in the evening | The evening job; it finishes in ~15 minutes. |

---

## 18. Glossary

| Term | Meaning |
|---|---|
| CE / PE | Call option (profits when the index rises) / put option (profits when it falls) |
| ATM / ITM / OTM | Strike at / inside / outside the current price |
| Straddle | Selling (or buying) a CE and a PE at the same strike |
| Wings | Far-away options bought as insurance on a short straddle |
| Debit spread | Buy one option, sell a cheaper one further out: lower cost and decay, capped profit |
| Expiry | Last day of an option contract |
| OI | Open interest: contracts still open |
| FII / DII / Pro / Client | Foreign funds / Indian funds / brokers' own trading / retail |
| PF (profit factor) | Total won ÷ total lost. Above 1 = profitable |
| Walk-forward | Choose settings on old data, judge on newer unseen data |
| VIX | India's volatility index: high = options expensive |
| θ theta / Δ delta | Daily time decay / move per 1 point of the underlying |
| Bhavcopy | NSE's daily file of every contract's prices and open interest |
