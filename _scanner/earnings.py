"""Results breakouts: mid-cap stocks that broke their 52-week high after their quarterly results.

Two of the best-documented effects in markets point the same way here:
  - post-earnings drift: after a strong result, prices keep moving in the direction of the surprise for
    weeks, because the market takes time to price it in (Bernard & Thomas, 1989);
  - the 52-week-high effect: stocks at a new 52-week high tend to keep outperforming, because traders
    anchor on the old high and under-react to the news that pushed it through (George & Hwang, 2004).
Mid caps show both more strongly than large caps (fewer analysts follow them).

The setup (long only, delivery; held for days to a few weeks):
  1. Results declared in the last 15 sessions (NSE's corporate calendar). If NSE can't be reached, a
     big-volume day (2.5x the 50-day average, a 3%+ move) stands in for it, and the pick says so.
  2. A close above the previous 52-week high on or after the results day, within the last 10 sessions.
  3. Still holding: the close is above the old 52-week high and within 8% of the high since the breakout.
  4. Trend: above the 50-day EMA, the 50-day EMA rising; stronger than NIFTY over 3 months scores more.
  5. Not stretched: more than 3 ATR above the 20-day EMA goes on the watchlist (wait for a pause).

Plan: buy above the highest high of the last 3 sessions (valid 5 sessions). Stop below the lowest low of
the last 5 sessions (risk at most 8%, otherwise it waits on the watchlist). T1 = 2R (book half,
stop to cost), T2 = 4R; anything still open after 25 sessions is closed.
"""
from analysis import _clamp, _day, atr

LOOKBACK_RESULTS = 15                                  # results within this many sessions
BREAKOUT_WINDOW = 10                                   # a 52-week-high close within this many sessions
TRIGGER_DAYS, HOLD_DAYS = 5, 25
MAX_RISK = 0.08


def ema(vals, n):
    out, k, e = [], 2 / (n + 1), None
    for v in vals:
        e = v if e is None else e + k * (v - e)
        out.append(e)
    return out


def result_index(d, result_day):
    """Index of the first session on or after the results day (a result after hours moves the next day)."""
    if not result_day:
        return None
    for i, c in enumerate(d):
        if _day(c["t"]) >= result_day:
            return i
    return None


def proxy_result_index(d, n=LOOKBACK_RESULTS):
    """No results date: the latest big-volume, big-move day in the window (very likely the results)."""
    for i in range(len(d) - 1, max(51, len(d) - n) - 1, -1):
        avg = sum(c["v"] for c in d[i - 50:i]) / 50 or 1
        move = abs(d[i]["c"] - d[i - 1]["c"]) / d[i - 1]["c"]
        if d[i]["v"] >= 2.5 * avg and move >= 0.03:
            return i
    return None


def analyse(sym, d, result_day=None, nifty_d=None, name=None):
    """The results-breakout setup for one stock today (d = daily candles, today last), or None.
    result_day: IST day number of the results (None = unknown: a big-volume day stands in).
    Returns {"symbol", "status": "BUY" | "WATCH", ...}."""
    if len(d) < 200:
        return None
    n = len(d)
    proxy = result_day is None
    r = proxy_result_index(d) if proxy else result_index(d, result_day)
    if r is None or r < n - LOOKBACK_RESULTS or r < 51:
        return None
    # The first close above the previous 52-week high, on or after the results day, in the last 10 sessions.
    b, old_high = None, None
    for i in range(max(r, n - BREAKOUT_WINDOW, 1), n):
        prev = max(c["h"] for c in d[max(0, i - 250):i])
        if d[i]["c"] > prev:
            b, old_high = i, prev
            break
    if b is None:
        return None
    today = d[-1]
    price = today["c"]
    hi_since = max(c["h"] for c in d[b:])
    if price < old_high * 0.99 or price < hi_since * 0.92:
        return None                                    # the breakout failed or gave too much back
    closes = [c["c"] for c in d]
    e20, e50 = ema(closes, 20), ema(closes, 50)
    if price < e50[-1] or e50[-1] <= e50[-6]:
        return None
    a = atr(d)[-1] or price * 0.03
    vol50 = sum(c["v"] for c in d[r - 50:r]) / 50 or 1
    vol_x = max(c["v"] for c in d[r:b + 1]) / vol50
    react = (d[r]["c"] - d[r - 1]["c"]) / d[r - 1]["c"] * 100
    gap = (d[r]["o"] - d[r - 1]["c"]) / d[r - 1]["c"] * 100
    stretch = (price - e20[-1]) / a
    rs = None
    if nifty_d and len(nifty_d) > 64:
        rs = round(((price / d[-64]["c"]) - (nifty_d[-1]["c"] / nifty_d[-64]["c"])) * 100, 1)
    entry = max(c["h"] for c in d[-3:])
    low5 = min(c["l"] for c in d[-5:])
    sl = low5 - 0.25 * a                               # under the last 5 sessions' base
    risk = entry - sl
    status = "BUY"
    why_watch = None
    if stretch > 3:
        status, why_watch = "WATCH", f"{stretch:.1f} ATR above the 20-day EMA: wait for a pause or a pullback"
    elif risk > entry * MAX_RISK:
        status, why_watch = "WATCH", f"the stop is {risk / entry * 100:.1f}% away (more than {MAX_RISK:.0%}): wait for a tighter base"
    age = n - 1 - b
    parts = {
        "breakout": round(20 * (1 - _clamp(age / BREAKOUT_WINDOW)) + (0 if price >= hi_since * 0.97 else -5), 1),     # /20
        "results": round(12 * _clamp(react / 8) + 8 * _clamp(gap / 5), 1) - (5 if proxy else 0),                       # /20
        "volume": round(20 * _clamp((vol_x - 1) / 3), 1),                                                              # /20
        "trend": round((8 if e20[-1] > e50[-1] else 0) + (4 if closes[-1] > e20[-1] else 0)
                       + (8 * _clamp(rs / 20) if rs is not None else 4), 1),                                           # /20
        "tightness": round(20 * (1 - _clamp((stretch - 0.5) / 2.5)) * (1 - _clamp((risk / entry - 0.03) / 0.05)), 1),  # /20
    }
    parts = {k: max(0.0, v) for k, v in parts.items()}
    score = round(sum(parts.values()), 1)
    t1, t2 = entry + 2 * risk, entry + 4 * risk
    day_txt, bo_txt = _txt(d[r]["t"]), _txt(d[b]["t"])
    return {"symbol": sym, "name": name, "side": "bull", "status": status, "why_watch": why_watch,
            "price": round(price, 2), "pct": round((price - d[-2]["c"]) / d[-2]["c"] * 100, 2),
            "score": score, "parts": parts, "result_day": day_txt, "result_proxy": proxy,
            "react_pct": round(react, 1), "gap_pct": round(gap, 1), "vol_x": round(vol_x, 1),
            "breakout_day": bo_txt, "breakout_age": age, "level": round(old_high, 2), "level_name": "Old 52W high",
            "high_since": round(hi_since, 2), "stretch": round(stretch, 1), "rs": rs,
            "ema20": round(e20[-1], 2), "ema50": round(e50[-1], 2), "atr": round(a, 2),
            "why": [f"Results {day_txt}" + (" (big-volume day; results date not confirmed)" if proxy else "")
                    + f": {react:+.1f}% on the day" + (f", gapped {gap:+.1f}%" if abs(gap) >= 1 else ""),
                    f"Closed above the 52-week high {old_high:.2f} on {bo_txt} ({age} sessions ago)",
                    f"Volume {vol_x:.1f}x the 50-day average", f"{stretch:.1f} ATR above the 20-day EMA"]
                   + ([f"{rs:+.1f}% vs NIFTY over 3 months"] if rs is not None else []),
            "plan": {"basis": "Results breakout · 52W high", "entry": round(entry, 2), "sl": round(sl, 2),
                     "t1": round(t1, 2), "t2": round(t2, 2), "rr": 2.0, "rr2": 4.0,
                     "zone": (round(old_high, 2), round(hi_since, 2)),
                     "risk_pct": round(risk / entry * 100, 2),
                     "action": f"Buy above {entry:.2f} within {TRIGGER_DAYS} sessions · stop {sl:.2f} · "
                               f"T1 {t1:.2f} (book half, stop to cost) · T2 {t2:.2f} · out after {HOLD_DAYS} sessions"}}


def _txt(t):
    from datetime import datetime, timedelta, timezone
    return datetime.fromtimestamp(t, timezone(timedelta(hours=5, minutes=30))).strftime("%d %b")


def rank(cands):
    buys = sorted([c for c in cands if c and c["status"] == "BUY"], key=lambda c: -c["score"])
    watch = sorted([c for c in cands if c and c["status"] == "WATCH"], key=lambda c: -c["score"])
    return buys, watch


def parse_index_csv(text):
    """Symbols from an NSE / Nifty Indices constituents CSV (Company Name, Industry, Symbol, Series, ISIN Code)."""
    import csv
    import io
    rows = list(csv.DictReader(io.StringIO(text)))
    out = {}
    for r in rows:
        r = {(k or "").strip().lower(): (v or "").strip() for k, v in r.items()}
        if r.get("symbol"):
            out[r["symbol"]] = r.get("industry") or ""
    return out
