# Stock swing trading (shares, delivery): research summary

Universe: 50 current Nifty 50 shares (incl. the 20 F&O stocks the option bot follows). Daily Upstox candles (already
split/bonus adjusted; TMPV back-adjusted for the Oct-2025 Tata Motors demerger). Signal at the close → buy at the next
open, 0.1% slippage each side, Upstox delivery charges (₹20 brokerage per order, STT 0.1% both sides, stamp, exchange,
SEBI, GST, ₹20 DP per sale). Fresh ₹20,000 account per period, max 3 positions, whole shares.
Choose on 2019–2023 (train), judge on 2024 → Nov 2026 (test). Benchmark: buy-and-hold Nifty 50.

| Family (best setting on train) | Train: trades · win · PF · CAGR · max DD | Test: trades · win · PF · CAGR · max DD |
|---|---|---|
| Trend pullback (RSI2 < 5, exit RSI2 > 70) | 295 · 26% · 0.26 · −50% · −97% | 206 · 24% · 0.14 · −44% · −81% |
| Breakout (20-day high, vol > 1.5×, 2 ATR stop, 1:2) | 191 · 44% · 1.06 · +3% · −48% | 110 · 36% · 0.63 · −15% · −43% |
| **Momentum (12-month, monthly, top 3, Nifty > EMA200)** | **63 · 49% · 1.70 · +17.5% · −30%** | **26 · 50% · 4.55 · +23.0% · −18%** |
| Nifty 50 buy & hold | — · +14.8% · −38% | — · +5.4% · −16% |

Why pullback fails: before costs it wins 64% of the time, but the average gain is only 0–0.2% per trade; costs are
~1.3% per round trip on a ₹6k position (fixed ₹20 fees) and ~0.25% even with unlimited capital.

Momentum filters (on the chosen setting): Nifty > EMA200 cuts drawdown (test −35% → −18%) → kept. VIX filter hurt
(test CAGR 23% → 1.6%). Smart-money filter changed nothing. Top 5 instead of top 3: lower return, lower drawdown.

| Account | Test CAGR · max DD |
|---|---|
| ₹20k, top 3 (paper) | +23.0% · −18% |
| ₹1L, top 3 | +26.2% · −17% |
| ₹1L, top 5 | +18.3% · −15% |

Year by year (₹20k, top 3) vs Nifty: 2019 −4.5 vs +12.0 · 2020 +4.1 vs +14.9 · 2021 +73.5 vs +24.1 · 2022 +19.9 vs +4.3 ·
2023 +7.9 vs +20.0 · 2024 +96.4 vs +8.8 · 2025 −2.9 vs +10.5 · 2026 (to Nov) +2.3 vs −3.2.

**Honest caveats.** (1) Lumpy: most of the gain came from 2021 and 2024; it trailed Nifty in 4 of 8 years.
(2) Survivorship bias: today's Nifty 50 list includes past winners (BEL, TRENT, …) that momentum picked — real
results with the historical list would be lower. (3) Only 26 test trades. Paper first.
