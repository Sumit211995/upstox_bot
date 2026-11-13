# Per-share big money (NSE delivery volume): research result

Data: NSE sec_bhavdata_full, 1 Oct 2019 → 12 Nov 2026, 1,766 days, all 50 universe symbols (TMPV ← TATAMOTORS,
ETERNAL ← ZOMATO). Bulk / block deals: NSE archives keep only TODAY's file (no free history) → cannot be tested;
`bigmoney_stocks.collect_deals()` can build a history going forward (not scheduled).
Choose on 2020–23, judge on 2024–Nov 2026. ₹20k account, delivery charges, 0.1% slippage, next-open fills.

**Verdict: nothing adopted.** Delivery data is shown for information only.

| Idea | 2020–23: n · win · PF · CAGR · DD | 2024–26: n · win · PF · CAGR · DD | Verdict |
|---|---|---|---|
| A · momentum baseline (FINAL) | 52 · 50% · 1.93 · 24.4% · −28% | 26 · 50% · 4.55 · 23.0% · −18% | — |
| A · keep delivery-% rank ≥ 50% | 64 · 56% · 1.80 · 18.9% · −20% | 35 · 63% · 2.20 · 14.8% · −19% | reject |
| A · keep delivery-% rank ≥ 30% | 66 · 52% · 1.37 · 13.2% · −26% | 30 · 47% · 3.31 · 19.1% · −10% | reject |
| A · keep delivery qty trend ≥ 1 | 87 · 39% · 0.79 · −4.8% · −52% | 50 · 46% · 1.81 · 9.9% · −16% | reject |
| A · keep accumulation days ≥ 40% | 81 · 48% · 1.14 · 4.6% · −39% | 47 · 47% · 1.32 · 5.2% · −17% | reject |
| A · top-6 re-ranked by delivery | 72 · 58% · 1.48 · 14.0% · −27% | 33 · 55% · 2.17 · 17.7% · −14% | reject |
| A · momentum × (0.5 + delivery rank) | 57 · 54% · 1.66 · 20.5% · −31% | 30 · 50% · 3.07 · 18.4% · −12% | reject |
| B · unusual delivery (≥2–3×, +2%, > EMA50), 6 exit settings | 125–230 trades · PF 0.59–0.95 · all lose | 73–136 trades · PF 0.42–0.73 · all lose | reject |
| C · stock-option smart buys, baseline | 128 · 57% · 3.06 · net ₹4.88 L | 181 · 46% · 1.75 · net ₹3.53 L | — |
| C · skip if delivery trend disagrees | 50 · 50% · 2.37 · ₹1.52 L | 111 · 43% · 1.65 · ₹1.99 L | reject |
| C · skip if accumulation disagrees | 82 · 42% · 1.43 · ₹0.89 L | 126 · 47% · 1.80 · ₹2.50 L | reject |
| C · CE only: skip delivery rank < 30% | 119 · 55% · 2.45 · ₹3.35 L | 166 · 48% · 1.94 · ₹3.78 L | reject (better 2024–26, worse 2020–23) |

Some filters lower the drawdown (e.g. rank ≥ 30%: −18% → −10% in 2024–26) but every one cuts PF and CAGR in at least
one period. Rule: keep only if PF AND CAGR / net improve in both periods with ≥ 60% of trades.
