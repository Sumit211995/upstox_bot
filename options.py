"""Option maths: Black-Scholes prices, implied volatility, strike selection and Indian F&O charges.

Without a broker login there is no free live/historical intraday option price, so premiums are ESTIMATED from the
index price, time to expiry and India VIX (scaled per index; calibrated on real NSE closing prices, see research).
"""
import math

from datetime import datetime, timedelta, timezone

YEAR = 365 * 86400
RATE = 0.065                    # risk-free rate (~ Indian T-bill)
IST = timezone(timedelta(hours=5, minutes=30))
SESSION_MIN = 375               # 09:15 → 15:30
TRADING_DAYS = 252


def trading_years(now_ts, expiry_ts):
    """Time to expiry on a TRADING clock: option value decays during market hours, not overnight/weekends.
    Remaining part of today's session + one full session per weekday until (and including) expiry day."""
    if expiry_ts <= now_ts:
        return 0.0
    now = datetime.fromtimestamp(now_ts, IST)
    exp = datetime.fromtimestamp(expiry_ts, IST)
    mins = now.hour * 60 + now.minute
    if now.weekday() < 5:
        today = min(max(15 * 60 + 30 - max(mins, 9 * 60 + 15), 0), SESSION_MIN) / SESSION_MIN
    else:
        today = 0.0
    days, d = 0, now.date() + timedelta(days=1)
    while d <= exp.date():
        days += d.weekday() < 5
        d += timedelta(days=1)
    if now.date() == exp.date():
        today = min(today, max(exp.hour * 60 + exp.minute - max(mins, 9 * 60 + 15), 0) / SESSION_MIN)
        days = 0
    return (today + days) / TRADING_DAYS

# implied vol of a near-month ATM option ≈ VIX × factor (calibrated on NSE bhavcopy, research/calibrate.py)
IV_FACTOR = {"NIFTY": 0.90, "SENSEX": 0.92, "BANKNIFTY": 1.06, "MIDCPNIFTY": 1.35}
EXPIRY_WEEK_BOOST = 1.0           # not needed on the trading-time clock (IV÷VIX is flat across days to expiry)
# ON expiry day the real ATM premium at 09:15 is lower than VIX implies (research/validate_expiry.py, real NSE
# open prices on 513 expiry days): scale the vol down so the model does not overstate a seller's profit.
EXPIRY_DAY = {"NIFTY": 0.895, "SENSEX": 0.895, "BANKNIFTY": 0.935, "MIDCPNIFTY": 0.82}
SKEW = 0.10                     # extra IV per 10% OTM put moneyness (puts dearer below spot); kept small and simple


def _ncdf(x):
    return 0.5 * (1 + math.erf(x / math.sqrt(2)))


def bs_price(spot, strike, t_years, vol, kind, r=RATE):
    """European option price. kind = 'CE' or 'PE'. Uses intrinsic value at/after expiry."""
    if t_years <= 0 or vol <= 0:
        return max(0.0, spot - strike) if kind == "CE" else max(0.0, strike - spot)
    sq = vol * math.sqrt(t_years)
    d1 = (math.log(spot / strike) + (r + vol * vol / 2) * t_years) / sq
    d2 = d1 - sq
    if kind == "CE":
        return spot * _ncdf(d1) - strike * math.exp(-r * t_years) * _ncdf(d2)
    return strike * math.exp(-r * t_years) * _ncdf(-d2) - spot * _ncdf(-d1)


def bs_delta(spot, strike, t_years, vol, kind, r=RATE):
    if t_years <= 0 or vol <= 0:
        itm = spot > strike if kind == "CE" else spot < strike
        return (1.0 if itm else 0.0) * (1 if kind == "CE" else -1)
    d1 = (math.log(spot / strike) + (r + vol * vol / 2) * t_years) / (vol * math.sqrt(t_years))
    return _ncdf(d1) if kind == "CE" else _ncdf(d1) - 1


def implied_vol(price, spot, strike, t_years, kind, r=RATE):
    """Volatility that reproduces `price` (bisection). None if the price is below intrinsic or not solvable."""
    if t_years <= 0 or price <= 0:
        return None
    lo, hi = 0.01, 3.0
    if bs_price(spot, strike, t_years, hi, kind, r) < price or bs_price(spot, strike, t_years, lo, kind, r) > price:
        return None
    for _ in range(60):
        mid = (lo + hi) / 2
        if bs_price(spot, strike, t_years, mid, kind, r) > price:
            hi = mid
        else:
            lo = mid
    return (lo + hi) / 2


def model_vol(index, vix, spot, strike, t_years=1.0):
    """Estimated implied volatility for one option: VIX scaled per index, expiry-week boost, simple put-side skew."""
    v = vix / 100 * IV_FACTOR.get(index, 1.0)
    if t_years <= 1 / TRADING_DAYS:
        v *= EXPIRY_DAY.get(index, 1.0)
    elif t_years < 3.5 / TRADING_DAYS:
        v *= EXPIRY_WEEK_BOOST
    m = (strike - spot) / spot                     # moneyness: negative = below spot
    if m < 0:
        v *= 1 + SKEW * min(-m / 0.10, 2)
    return max(v, 0.05)


def premium(index, spot, strike, kind, expiry_ts, now_ts, vix):
    """Model premium (₹ per unit) for an index option at `now_ts`."""
    t = trading_years(now_ts, expiry_ts)
    return bs_price(spot, strike, t, model_vol(index, vix, spot, strike, t), kind)


def atm_strike(spot, step):
    return round(spot / step) * step


def pick_strike(spot, step, kind, offset_steps=0):
    """offset_steps > 0 = in the money by that many strikes, < 0 = out of the money."""
    atm = atm_strike(spot, step)
    return atm - offset_steps * step if kind == "CE" else atm + offset_steps * step


# ------------------------------------------------------------------ Indian F&O charges (options), per order leg
BROKERAGE = 20.0                # ₹ flat per executed order (Upstox F&O)
STT_SELL = 0.001                # 0.1% of premium on the SELL side (Oct 2024 →)
TXN_NSE = 0.0003503             # exchange transaction charge on premium (NSE)
TXN_BSE = 0.000325
SEBI = 10 / 1e7                 # ₹10 per crore
STAMP_BUY = 0.00003             # 0.003% on the BUY side
GST = 0.18                      # on brokerage + exchange + SEBI charges


def charges(premium_value, side, exchange="NSE"):
    """Total charges (₹) for one option order. premium_value = premium × quantity; side = 'buy' or 'sell'."""
    txn = premium_value * (TXN_BSE if exchange == "BSE" else TXN_NSE)
    sebi = premium_value * SEBI
    stt = premium_value * STT_SELL if side == "sell" else 0.0
    stamp = premium_value * STAMP_BUY if side == "buy" else 0.0
    gst = GST * (BROKERAGE + txn + sebi)
    return BROKERAGE + txn + sebi + stt + stamp + gst


def greeks(spot, strike, t_years, vol, kind, r=RATE):
    """Delta, gamma (per 1 point), theta (₹ per unit per TRADING day, negative = value lost to time) and vega
    (₹ per unit per 1 volatility point) for one option. Numerical, from bs_price, so it matches our pricing."""
    p = bs_price(spot, strike, t_years, vol, kind, r)
    h = max(spot * 0.001, 0.5)
    up, dn = bs_price(spot + h, strike, t_years, vol, kind, r), bs_price(spot - h, strike, t_years, vol, kind, r)
    day = 1 / TRADING_DAYS
    theta = (bs_price(spot, strike, max(t_years - day, 0.0), vol, kind, r) - p) if t_years > 0 else 0.0
    vega = bs_price(spot, strike, t_years, vol + 0.01, kind, r) - p
    return dict(delta=(up - dn) / (2 * h), gamma=(up - 2 * p + dn) / (h * h), theta=theta, vega=vega)
