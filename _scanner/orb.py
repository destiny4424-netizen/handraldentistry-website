"""Opening Range Breakout on "stocks in play" (Zarattini, Barbon & Aziz, "A Profitable Day Trading
Strategy For The U.S. Equity Market", 2023-24), adapted to NSE F&O stocks.

    1. Opening range: the first 5 minutes (9:15-9:20), or the first 15 (9:15-9:30).
    2. Stocks in play: the opening range traded at least as much as usual for that time
       (relative volume >= 1 against the last 14 sessions); the most in play are taken first.
    3. Direction: the opening range candle's colour. Green -> buy above its high; red -> sell below
       its low; no real body (doji) -> no trade.
    4. Stop: 10% of the 14-day ATR from the entry (the paper's rule), or the other side of the range.
    5. Exit: the stop, or the close (3:15 PM). No targets: the winners are allowed to run all day.
"""
from analysis import _clamp, _day

ORB_MINUTES = {"orb5": 1, "orb15": 3}                 # 5-minute candles in the opening range


def daily_atr(daily, n=14):
    """Average true range of the last n daily candles."""
    d = daily[-(n + 1):]
    if len(d) < 2:
        return None
    trs = [max(c["h"] - c["l"], abs(c["h"] - p["c"]), abs(c["l"] - p["c"])) for p, c in zip(d, d[1:])]
    return sum(trs) / len(trs)


def opening_volumes(c5, k):
    """{IST day: volume of that day's first k 5-minute candles}."""
    out, count = {}, {}
    for c in c5:
        dn = _day(c["t"])
        if count.get(dn, 0) < k:
            out[dn] = out.get(dn, 0) + c["v"]
            count[dn] = count.get(dn, 0) + 1
    return {dn: v for dn, v in out.items() if count[dn] == k}


def usual_opening(vols_by_day, day, n=14):
    prev = [v for dn, v in sorted(vols_by_day.items()) if dn < day][-n:]
    return sum(prev) / len(prev) if len(prev) >= 5 else None


def candidate(sym, today5, usual_vol, atr, setup="orb5", stop="atr10", sector=None):
    """The ORB trade for one stock today, or None."""
    k = ORB_MINUTES[setup]
    if len(today5) < k or not usual_vol or not atr:
        return None
    rng = today5[:k]
    o, c = rng[0]["o"], rng[-1]["c"]
    h, l = max(x["h"] for x in rng), min(x["l"] for x in rng)
    vol = sum(x["v"] for x in rng)
    rvol = vol / usual_vol
    if rvol < 1.0 or h <= l or abs(c - o) < 0.1 * (h - l):
        return None                                    # not in play, or no direction (doji)
    side = "bull" if c > o else "bear"
    sign = 1 if side == "bull" else -1
    entry = h if side == "bull" else l
    risk = 0.1 * atr if stop == "atr10" else (h - l)
    risk = max(risk, entry * 0.001)                    # never tighter than 0.1% (ticks and spread)
    sl = entry - sign * risk
    start = rng[-1]["t"] + 300
    body = abs(c - o) / (h - l)
    parts = {"volume": 25 * _clamp((rvol - 1) / 4), "candle": 20 * _clamp(body),
             "structure": 15 * _clamp((h - l) / atr / 0.5), "trend": 0.0, "participation": 0.0}
    mins = 5 * k
    return {"symbol": sym, "side": side, "price": today5[-1]["c"], "rvol_or": round(rvol, 2), "vol_x": round(rvol, 1),
            "range": (round(l, 2), round(h, 2)), "atr": round(atr, 2), "sector": sector, "parts": parts,
            "broke_at": None, "setup": setup,
            "plan": {"basis": f"ORB {mins}-min · stocks in play", "entry": round(entry, 2), "sl": round(sl, 2),
                     "t1": round(entry + sign * 2 * risk, 2), "t2": round(entry + sign * 4 * risk, 2),
                     "zone": (round(l, 2), round(h, 2)), "rr": 2.0, "rr2": 4.0,
                     "risk_pct": round(risk / entry * 100, 2), "confirmed": True, "held_at": rng[-1]["t"],
                     "start_at": start, "exit_rule": "eod", "triggered_at": None,
                     "status": f"{'Buy above' if side == 'bull' else 'Sell below'} {entry:.2f}",
                     "action": f"{'Buy above' if side == 'bull' else 'Sell below'} {entry:.2f} · stop {sl:.2f} · "
                               f"hold to 3:15 PM (no target)"}}


def rank(cands, n):
    """The most in play first."""
    for c in cands:
        c["score"] = round(sum(c["parts"].values()), 1)
    return sorted(cands, key=lambda c: -c["rvol_or"])[:n]
