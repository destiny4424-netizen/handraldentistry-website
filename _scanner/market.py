"""Market context for intraday trading: index direction (NIFTY, BANK NIFTY), India VIX,
breadth, top gainers/losers, and per-stock VWAP and relative volume."""
from analysis import _day

OPENING_RANGE = 3                                      # 5-minute candles = the first 15 minutes


def ema(values, n):
    out, k, e = [], 2 / (n + 1), None
    for v in values:
        e = v if e is None else e + k * (v - e)
        out.append(e)
    return out


def session(candles):
    """(today's candles, all earlier candles) by IST day of the last candle."""
    if not candles:
        return [], []
    d = _day(candles[-1]["t"])
    return [c for c in candles if _day(c["t"]) == d], [c for c in candles if _day(c["t"]) < d]


def index_view(name, candles):
    """Direction of an index today, with the reasons, from its 5-minute candles."""
    today, before = session(candles)
    if not today:
        return None
    price, opn = today[-1]["c"], today[0]["o"]
    prev_day = [c for c in before if before and _day(c["t"]) == _day(before[-1]["t"])]
    prev_close = before[-1]["c"] if before else opn
    pdh = max(c["h"] for c in prev_day) if prev_day else None
    pdl = min(c["l"] for c in prev_day) if prev_day else None
    closes = [c["c"] for c in candles]
    e = ema(closes, 20)
    slope = e[-1] - e[-4] if len(e) > 4 else 0
    orh = max(c["h"] for c in today[:OPENING_RANGE])
    orl = min(c["l"] for c in today[:OPENING_RANGE])
    score, why = 0, []
    if price > opn:
        score += 1; why.append("above today's open")
    elif price < opn:
        score -= 1; why.append("below today's open")
    if price > prev_close:
        score += 1; why.append("above yesterday's close")
    elif price < prev_close:
        score -= 1; why.append("below yesterday's close")
    if price > e[-1] and slope > 0:
        score += 1; why.append("above a rising 20-EMA")
    elif price < e[-1] and slope < 0:
        score -= 1; why.append("below a falling 20-EMA")
    if len(today) > OPENING_RANGE:
        if price > orh:
            score += 1; why.append("broke the first-15-min high")
        elif price < orl:
            score -= 1; why.append("broke the first-15-min low")
        else:
            why.append("inside the first-15-min range")
    if pdh and price > pdh:
        why.append("above yesterday's high")
    elif pdl and price < pdl:
        why.append("below yesterday's low")
    trend = "Bullish" if score >= 2 else "Bearish" if score <= -2 else "Sideways"
    return {"name": name, "price": round(price, 2), "pct": round((price - prev_close) / prev_close * 100, 2),
            "trend": trend, "score": score, "why": why, "open": opn, "prev_close": prev_close,
            "or_high": orh, "or_low": orl, "pdh": pdh, "pdl": pdl,
            "spark": [round(c["c"], 2) for c in today]}


def vix_view(candles, daily):
    """India VIX now, and whether it is high or low against its own 20-day average."""
    today, before = session(candles)
    if not today:
        return None
    price = today[-1]["c"]
    prev = before[-1]["c"] if before else today[0]["o"]
    closes = [d["c"] for d in (daily or [])][-20:]
    avg = sum(closes) / len(closes) if closes else None
    level = "Normal"
    if avg:
        level = "High" if price > avg * 1.15 else "Low" if price < avg * 0.85 else "Normal"
    return {"name": "INDIA VIX", "price": round(price, 2), "pct": round((price - prev) / prev * 100, 2),
            "level": level, "avg20": round(avg, 2) if avg else None,
            "note": {"High": "Fear is high: wider swings, use smaller size and wider stops.",
                     "Low": "Calm market: moves may be smaller; take profits sooner.",
                     "Normal": "Volatility is normal."}[level]}


def vwap(candles):
    today, _ = session(candles)
    v = sum(c["v"] for c in today)
    if not v:
        return None
    return sum((c["h"] + c["l"] + c["c"]) / 3 * c["v"] for c in today) / v


def rvol(candles, daily):
    """Relative volume: today's volume so far against a normal day's volume by this time."""
    today, _ = session(candles)
    days = [d["v"] for d in (daily or []) if _day(d["t"]) < _day(candles[-1]["t"])][-10:] if candles else []
    if not today or not days:
        return None
    avg = sum(days) / len(days)
    done = min(1.0, max(len(today), 1) * 5 / 375)      # share of the 375-minute session gone
    return round(sum(c["v"] for c in today) / (avg * done), 2) if avg else None


def breadth(pcts):
    up = sum(1 for p in pcts if p > 0)
    down = sum(1 for p in pcts if p < 0)
    return {"advances": up, "declines": down, "unchanged": len(pcts) - up - down,
            "ratio": round(up / down, 2) if down else None}


def movers(rows, n=5):
    """rows: [{symbol, price, pct, rvol}] -> top gainers and losers."""
    s = sorted(rows, key=lambda r: r["pct"])
    return {"gainers": list(reversed(s[-n:])), "losers": s[:n]}


def verdict(nifty, bank, vix, br):
    """One line telling the trader which side the market favours right now."""
    if not nifty:
        return ""
    t = nifty["trend"]
    agree = bank and bank["trend"] == t
    tilt = ""
    if br and br["advances"] + br["declines"]:
        share = br["advances"] / (br["advances"] + br["declines"])
        tilt = (" Breadth is strong." if share > .65 else " Breadth is weak." if share < .35 else
                " Breadth is mixed.")
    if t == "Bullish":
        msg = "NIFTY is bullish: favour BUY picks" + (" (BANK NIFTY agrees)." if agree else ", but BANK NIFTY does not agree.")
    elif t == "Bearish":
        msg = "NIFTY is bearish: favour SELL picks" + (" (BANK NIFTY agrees)." if agree else ", but BANK NIFTY does not agree.")
    else:
        msg = "NIFTY is sideways: be selective, take only the strongest picks and book profits sooner."
    if vix and vix["level"] == "High":
        msg += " VIX is high, so reduce position size."
    return msg + tilt


def supertrend(candles, period=10, mult=3.0):
    """Supertrend on the given candles: list of (line value, direction), +1 up / -1 down."""
    out, prev_up, prev_dn, d, a = [], None, None, 1, None
    prev_close = None
    for c in candles:
        tr = c["h"] - c["l"] if prev_close is None else max(c["h"] - c["l"], abs(c["h"] - prev_close), abs(c["l"] - prev_close))
        a = tr if a is None else (a * (period - 1) + tr) / period
        mid = (c["h"] + c["l"]) / 2
        up, dn = mid - mult * a, mid + mult * a
        if prev_up is not None and prev_close > prev_up:
            up = max(up, prev_up)
        if prev_dn is not None and prev_close < prev_dn:
            dn = min(dn, prev_dn)
        if prev_dn is not None and c["c"] > prev_dn:
            d = 1
        elif prev_up is not None and c["c"] < prev_up:
            d = -1
        out.append((up if d == 1 else dn, d))
        prev_up, prev_dn, prev_close = up, dn, c["c"]
    return out
