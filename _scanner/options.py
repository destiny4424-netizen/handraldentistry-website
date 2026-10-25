"""Stock option plans: which call (for a buy) or put (for a sell) to trade, and the option's
entry, stop-loss and targets worked out from the stock's own levels.

The strike is at the money, or one strike in the money when the at-the-money strike trades thin.
The expiry is the current month's, unless it is less than NEAR_DAYS away: then next month's,
because premiums melt fastest in the last week. Option prices at the stock's levels come from the
option's delta and gamma (from Dhan's option chain), so they move the way the option really moves.
"""
import math
from datetime import date, datetime

NEAR_DAYS = 7                                          # roll to next month inside the last week


def choose_expiry(expiries, today, near_days=NEAR_DAYS):
    """Nearest expiry with more than near_days to go (expiries: 'YYYY-MM-DD' strings)."""
    ahead = sorted(e for e in expiries if e >= today.isoformat())
    for e in ahead:
        if (date.fromisoformat(e) - today).days > near_days:
            return e, e != ahead[0]
    return (ahead[-1], False) if ahead else (None, False)


def strikes_near(strikes, price, kind, n=2):
    """At-the-money strike first, then up to n in-the-money strikes (calls below, puts above)."""
    if not strikes:
        return []
    s = sorted(strikes)
    atm = min(s, key=lambda k: abs(k - price))
    i = s.index(atm)
    itm = s[max(0, i - n):i][::-1] if kind == "CE" else s[i + 1:i + 1 + n]
    return [atm] + itm


def tick(x):
    return max(0.05, round(round(x / 0.05) * 0.05, 2))


def premium_at(opt, spot, at):
    """Option price when the stock is at `at`, from its price now and its delta/gamma."""
    d = at - spot
    return tick(opt["ltp"] + opt["delta"] * d + 0.5 * opt.get("gamma", 0) * d * d)


def plan(kind, opt, spot, stock_plan, lot, expiry, rolled, strike):
    """Option entry/SL/T1/T2 at the stock's entry/SL/T1/T2."""
    e = premium_at(opt, spot, stock_plan["entry"])
    sl = premium_at(opt, spot, stock_plan["sl"])
    t1 = premium_at(opt, spot, stock_plan["t1"])
    t2 = premium_at(opt, spot, stock_plan["t2"])
    if not (sl < e < t1 <= t2):                       # delta too small to be useful
        return None
    out = {"kind": kind, "strike": strike, "expiry": expiry, "rolled": rolled, "lot": lot,
           "ltp": tick(opt["ltp"]), "delta": round(opt["delta"], 2), "iv": opt.get("iv"),
           "oi": opt.get("oi"), "volume": opt.get("volume"),
           "entry": e, "sl": sl, "t1": t1, "t2": t2,
           "risk_lot": round((e - sl) * lot) if lot else None,
           "reward_lot": round((t1 - e) * lot) if lot else None,
           "spread_pct": opt.get("spread_pct")}
    out["label"] = f"{label_expiry(expiry)} {fmt_strike(strike)} {kind}"
    return out


def fmt_strike(k):
    return str(int(k)) if float(k).is_integer() else f"{k:g}"


def label_expiry(e):
    try:
        return datetime.strptime(e, "%Y-%m-%d").strftime("%d %b").upper()
    except (TypeError, ValueError):
        return e or ""


def from_chain(chain, kind, strike):
    """Pick one strike out of Dhan's option chain response (data.oc)."""
    oc = (chain or {}).get("oc") or {}
    for k, row in oc.items():
        try:
            if abs(float(k) - strike) > 1e-6:
                continue
        except ValueError:
            continue
        o = (row or {}).get(kind.lower()) or {}
        g = o.get("greeks") or {}
        ltp = o.get("last_price") or 0
        if not ltp or g.get("delta") is None:
            return None
        bid, ask = o.get("top_bid_price") or 0, o.get("top_ask_price") or 0
        return {"ltp": float(ltp), "delta": float(g["delta"]), "gamma": float(g.get("gamma") or 0),
                "iv": round(float(o.get("implied_volatility") or 0), 1), "oi": o.get("oi"),
                "volume": o.get("volume"),
                "spread_pct": round((ask - bid) / ltp * 100, 1) if bid and ask and ltp else None}
    return None


def usable(o):
    """Liquid enough to trade: has volume and a tight-ish bid/ask."""
    return o and (o.get("volume") or 0) > 0 and (o.get("spread_pct") is None or o["spread_pct"] <= 5)


# ── Black-Scholes, for demo data (no option chain without Dhan keys) ───────────────────────
def _ncdf(x):
    return 0.5 * (1 + math.erf(x / math.sqrt(2)))


def bs(spot, strike, days, iv, kind, r=0.065):
    t = max(days, 0.5) / 365
    v = iv / 100
    d1 = (math.log(spot / strike) + (r + v * v / 2) * t) / (v * math.sqrt(t))
    d2 = d1 - v * math.sqrt(t)
    pdf = math.exp(-d1 * d1 / 2) / math.sqrt(2 * math.pi)
    gamma = pdf / (spot * v * math.sqrt(t))
    if kind == "CE":
        return spot * _ncdf(d1) - strike * math.exp(-r * t) * _ncdf(d2), _ncdf(d1), gamma
    return strike * math.exp(-r * t) * _ncdf(-d2) - spot * _ncdf(-d1), _ncdf(d1) - 1, gamma


def demo_strike_step(price):
    for top, step in ((250, 2.5), (500, 5), (1000, 10), (2500, 20), (5000, 50), (10000, 100)):
        if price < top:
            return step
    return 250
