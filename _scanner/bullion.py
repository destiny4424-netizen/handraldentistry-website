"""MCX gold and silver screener for positional longs held 3-4 months (futures).

Works on daily candles of the near-month futures contract. For each metal it checks the
long-term trend (50/200-day averages, 20-week EMA), momentum (3-month return, weekly RSI)
and how stretched price is, and gives a verdict with a buy zone, stop-loss and targets.

A candle is a dict: {"t": epoch seconds, "o", "h", "l", "c", "v"}.
"""
from datetime import date, timedelta

from analysis import atr
from market import ema

# ₹ gained or lost per lot for a ₹1 move in the GOLD (per 10 g) or SILVER (per kg) price.
# GOLDGUINEA is quoted per 8 g and GOLDPETAL per 1 g, so their own prices are lower.
CONTRACTS = {
    "GOLD": [("GOLD", "1 kg", 100), ("GOLDM", "100 g", 10), ("GOLDGUINEA", "8 g", 0.8), ("GOLDPETAL", "1 g", 0.1)],
    "SILVER": [("SILVER", "30 kg", 30), ("SILVERM", "5 kg", 5), ("SILVERMIC", "1 kg", 1)],
}
ROLL_DAYS = 7                     # roll before MCX bullion's delivery (tender) period starts


def sma(values, n):
    return sum(values[-n:]) / n if len(values) >= n else None


def rsi(closes, n=14):
    if len(closes) <= n:
        return None
    gain = loss = 0.0
    for a, b in zip(closes[:n], closes[1:n + 1]):
        gain += max(0, b - a)
        loss += max(0, a - b)
    gain, loss = gain / n, loss / n
    for a, b in zip(closes[n:], closes[n + 1:]):
        gain = (gain * (n - 1) + max(0, b - a)) / n
        loss = (loss * (n - 1) + max(0, a - b)) / n
    return 100.0 if loss == 0 else 100 - 100 / (1 + gain / loss)


def weekly(daily):
    """Daily candles grouped into weeks (Monday to Sunday, IST)."""
    out = []
    for k in daily:
        wk = (k["t"] + 19800 + 3 * 86400) // (7 * 86400)    # 1970-01-01 was a Thursday
        if out and out[-1]["wk"] == wk:
            w = out[-1]
            w["h"], w["l"], w["c"] = max(w["h"], k["h"]), min(w["l"], k["l"]), k["c"]
            w["v"] += k.get("v") or 0
        else:
            out.append({"wk": wk, "t": k["t"], "o": k["o"], "h": k["h"], "l": k["l"], "c": k["c"],
                        "v": k.get("v") or 0})
    return out


def _r(x, nd=0):
    return None if x is None else round(x, nd)


def screen(metal, daily):
    """Verdict, checks and a trade plan for one metal from its daily futures candles."""
    if len(daily) < 60:
        return None
    closes = [k["c"] for k in daily]
    price = closes[-1]
    a = atr(daily, 14)[-1]
    long_n = 200 if len(closes) >= 200 else max(100, len(closes) // 10 * 10 - 20)
    long_n = min(long_n, len(closes))
    sma50, sma_long, sma20 = sma(closes, 50), sma(closes, long_n), sma(closes, 20)
    sma50_prev = sum(closes[-70:-20]) / 50 if len(closes) >= 70 else None
    long_prev = sum(closes[-long_n - 20:-20]) / long_n if len(closes) >= long_n + 20 else None
    wk = weekly(daily)
    wc = [w["c"] for w in wk]
    e10, e20 = ema(wc, 10)[-1], ema(wc, 20)[-1]
    w_rsi, d_rsi = rsi(wc), rsi(closes)
    ret3m = (price / closes[-64] - 1) * 100 if len(closes) > 64 else None
    ret6m = (price / closes[-127] - 1) * 100 if len(closes) > 127 else None
    year = daily[-250:]
    hi52, lo52 = max(k["h"] for k in year), min(k["l"] for k in year)
    off_high = (price / hi52 - 1) * 100
    stretch = (price - sma50) / a if a else 0                 # ATRs above the 50-day average
    hi60 = max(k["h"] for k in daily[-61:-1])
    base = daily[-41:-1]
    base_range = (max(k["h"] for k in base) - min(k["l"] for k in base)) / a if a else 99
    breakout = price > hi60 and base_range < 12 and (daily[-1]["v"] or 0) >= sum(k.get("v") or 0 for k in daily[-21:-1]) / 20

    long_name = f"{long_n}-day"
    checks = [
        (15, price > sma_long, f"Price {'above' if price > sma_long else 'below'} the {long_name} average"),
        (15, sma50 > sma_long, f"50-day average {'above' if sma50 > sma_long else 'below'} the {long_name}"),
        (10, long_prev is not None and sma_long > long_prev,
         f"{long_name} average {'rising' if long_prev is not None and sma_long > long_prev else 'falling or flat'}"),
        (10, sma50_prev is not None and sma50 > sma50_prev,
         f"50-day average {'rising' if sma50_prev is not None and sma50 > sma50_prev else 'falling'}"),
        (15, wc[-1] > e20 and e10 > e20, "Weekly close above the 20-week EMA, 10-week above 20-week"
         if wc[-1] > e20 and e10 > e20 else "Weekly trend not up (20-week EMA)"),
        (10, ret3m is not None and ret3m > 0, f"3-month return {ret3m:+.1f}%" if ret3m is not None else "3-month return n/a"),
        (10, w_rsi is not None and 50 <= w_rsi <= 72,
         f"Weekly RSI {w_rsi:.0f}" + ("" if w_rsi is None or 50 <= w_rsi <= 72 else
                                     " (overheated)" if w_rsi > 72 else " (weak)") if w_rsi is not None else "Weekly RSI n/a"),
        (15, -1 <= stretch <= 2.5, f"{abs(stretch):.1f} ATR {'above' if stretch >= 0 else 'below'} the 50-day average"
         + (" (stretched)" if stretch > 2.5 else "")),
    ]
    score = sum(pts for pts, ok, _ in checks if ok)
    uptrend = price > sma_long and sma50 > sma_long
    turning = price > sma_long and not uptrend

    # Buy zone: between the 50-day average and the 20-day average (or 50-day + 1 ATR).
    zone_top = max(sma50 + a, min(sma20, sma50 + 2 * a))
    zone = [sma50, zone_top]
    if uptrend and breakout:
        verdict, action = "BUY", "Fresh breakout to a 60-day high from a tight base"
        entry = price
    elif uptrend and stretch <= 1.5 and price >= sma50 - 0.5 * a:
        verdict, action = "BUY", "Uptrend and price is in the buy zone near the 50-day average"
        entry = price
    elif uptrend:
        verdict = "BUY ON DIP"
        action = (f"Uptrend, but price is {stretch:.1f} ATR above the 50-day average. "
                  f"Wait for a dip into {zone[0]:,.0f}–{zone[1]:,.0f}" if stretch > 1.5 else
                  "Uptrend, but price slipped under the 50-day average. Buy only when it closes back above it")
        entry = zone[1] if stretch > 1.5 else sma50 + 0.25 * a
    elif turning:
        verdict, action = "WAIT", f"Above the {long_name} average, but the 50-day is not above it yet. Trend not confirmed"
        entry = None
    else:
        verdict, action = "AVOID", f"Downtrend: price and the 50-day average are below the {long_name}. No long futures"
        entry = None

    plan = None
    if entry:
        recent_low = min(k["l"] for k in daily[-20:])
        sl = max(entry - 4 * a, min(sma50 - a, recent_low - 0.25 * a))
        sl = min(sl, entry - 1.5 * a)
        risk = entry - sl
        t1, t2 = entry + 2 * risk, entry + 3 * risk
        plan = {"entry": _r(entry), "sl": _r(sl), "t1": _r(t1), "t2": _r(t2), "risk": _r(risk),
                "risk_pct": _r(risk / entry * 100, 1), "t1_pct": _r((t1 / entry - 1) * 100, 1),
                "t2_pct": _r((t2 / entry - 1) * 100, 1),
                "trail": "After T1, book half and trail the stop under the 20-week EMA "
                         f"(now {e20:,.0f}) on a weekly closing basis",
                "lots": [{"symbol": s, "size": size, "per_point": pp, "risk_lot": _r(risk * pp),
                          "value": _r(entry * pp)} for s, size, pp in CONTRACTS[metal]]}

    prev = closes[-2] if len(closes) > 1 else price
    return {"symbol": metal, "price": _r(price), "pct": _r((price / prev - 1) * 100, 2),
            "as_of": daily[-1]["t"], "verdict": verdict, "action": action, "score": score,
            "uptrend": uptrend, "checks": [{"ok": ok, "text": t} for _, ok, t in checks],
            "atr": _r(a), "atr_pct": _r(a / price * 100, 2), "sma20": _r(sma20), "sma50": _r(sma50),
            "sma_long": _r(sma_long), "long_n": long_n, "ema20w": _r(e20), "rsi_w": _r(w_rsi), "rsi_d": _r(d_rsi),
            "ret3m": _r(ret3m, 1), "ret6m": _r(ret6m, 1), "hi52": _r(hi52), "lo52": _r(lo52),
            "off_high": _r(off_high, 1), "stretch": _r(stretch, 1), "zone": [_r(zone[0]), _r(zone[1])],
            "breakout": breakout, "plan": plan, "bars": len(daily)}


def ratio_view(gold, silver):
    """Gold/silver ratio (grams of silver per gram of gold) and which metal it favours."""
    by_t = {k["t"] // 86400: k["c"] for k in silver}
    series = [g["c"] * 100 / by_t[g["t"] // 86400] for g in gold if by_t.get(g["t"] // 86400)]
    if len(series) < 20:
        return None
    now = series[-1]
    year = series[-250:]
    lo, hi = min(year), max(year)
    pos = (now - lo) / (hi - lo) * 100 if hi > lo else 50
    if now >= 85 or pos >= 75:
        favour, why = "SILVER", "the ratio is high: silver is cheap against gold and tends to catch up in bull runs"
    elif now <= 65 or pos <= 25:
        favour, why = "GOLD", "the ratio is low: silver has already run ahead of gold"
    else:
        favour, why = None, "the ratio is mid-range: neither metal is clearly cheap"
    return {"ratio": round(now, 1), "lo": round(lo, 1), "hi": round(hi, 1), "pos": round(pos),
            "favour": favour, "why": why, "chg_3m": round(now - series[-64], 1) if len(series) > 64 else None}


def usdinr_view(daily):
    """Rupee trend: a weaker rupee lifts MCX gold and silver prices."""
    if len(daily) < 50:
        return None
    closes = [k["c"] for k in daily]
    s50 = sma(closes, 50)
    up = closes[-1] > s50
    return {"price": round(closes[-1], 2), "sma50": round(s50, 2), "weak_rupee": up,
            "text": f"USD/INR {closes[-1]:.2f} is {'above' if up else 'below'} its 50-day average "
                    f"({'rupee weakening: a tailwind' if up else 'rupee firm: a mild headwind'} for MCX bullion)"}


def contract_plan(contracts, today):
    """Which futures contract to hold and when to roll it, from [(symbol, expiry, security id)]."""
    out = []
    for sym, rows in contracts.items():
        live = sorted(r for r in rows if r[0] > today)
        if not live:
            continue
        pick = next((r for r in live if _days(today, r[0]) > ROLL_DAYS + 14), live[0])
        nxt = next((r for r in live if r[0] > pick[0]), None)
        out.append({"symbol": sym, "expiry": pick[0], "days_left": _days(today, pick[0]),
                    "roll_by": _shift(pick[0], -ROLL_DAYS), "next": nxt[0] if nxt else None,
                    "all": [r[0] for r in live[:4]]})
    return out


def _days(a, b):
    return (date.fromisoformat(b) - date.fromisoformat(a)).days


def _shift(d, n):
    return (date.fromisoformat(d) + timedelta(days=n)).isoformat()
