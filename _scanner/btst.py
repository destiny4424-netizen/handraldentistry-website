"""BTST / STBT: stocks to buy (or, in F&O, sell) near today's close and exit tomorrow morning.

What a professional looks for in a BTST (STBT is the mirror image):
    - a real daily breakout: closing above yesterday's high, best of all at a 20-day high
    - a strong close: in the top 20% of the day's range, still near the high in the last hour
    - big participation: today's volume well above the 20-day average (projected for the full day)
    - trend on its side: above VWAP and above the 20-day average close
    - fresh money: long build-up in futures OI (unwinding = money leaving: skipped)
    - not exhausted: up 1.5-6% (bigger moves often give back the next morning)
    - market and sector not against it
Exit next morning at T1/T2, the stop, or by 11:00.
"""
from analysis import _clamp, _day, atr
from breakout import resample
from market import session

MAX_RISK = 2.5                                         # % of price; wider stops are skipped
MIN_RISK = 0.5


def candidates(results, candles_by, daily_by, levels_by, sector_pct, nifty_trend, now_ts):
    """Rough BTST/STBT shortlist (before OI), strongest first."""
    out = []
    for sym, r in results.items():
        c5 = candles_by.get(sym) or []
        today, before = session(c5)
        if len(today) < 24:                            # need at least two hours of today's session
            continue
        price, pct, vwap, rvol = r["price"], r["pct"], r.get("vwap"), r.get("rvol") or 0
        hi, lo = max(c["h"] for c in today), min(c["l"] for c in today)
        pos = (price - lo) / (hi - lo) if hi > lo else .5
        daily = [d for d in (daily_by.get(sym) or []) if _day(d["t"]) < _day(today[0]["t"])]
        prev = [c for c in before if before and _day(c["t"]) == _day(before[-1]["t"])]
        pdh = daily[-1]["h"] if daily else (max(c["h"] for c in prev) if prev else None)
        pdl = daily[-1]["l"] if daily else (min(c["l"] for c in prev) if prev else None)
        if not pdh or not pdl or not vwap:
            continue
        last20 = daily[-20:]
        hi20 = max(d["h"] for d in last20) if last20 else None
        lo20 = min(d["l"] for d in last20) if last20 else None
        sma20 = sum(d["c"] for d in last20) / len(last20) if last20 else None
        hour = today[-12:]                             # the last hour of 5-minute candles
        h_hi, h_lo = max(c["h"] for c in hour), min(c["l"] for c in hour)
        sec = sector_pct.get(sym, 0)
        bull = (1.5 <= pct <= 6 and pos >= 0.8 and price > vwap and price > pdh and rvol >= 1.5
                and nifty_trend != "Bearish" and price >= h_hi * 0.995)
        bear = (-6 <= pct <= -1.5 and pos <= 0.2 and price < vwap and price < pdl and rvol >= 1.5
                and nifty_trend != "Bullish" and price <= h_lo * 1.005)
        if not (bull or bear):
            continue
        side = "bull" if bull else "bear"
        sign = 1 if bull else -1
        hourly = [c for c in resample(today, 60) if c["end"] <= now_ts]
        if len(hourly) < 2:
            continue
        # Stop beyond the last hour's low (high), but never tighter than 0.5% or wider than 2.5%.
        sl = h_lo if bull else h_hi
        a = atr(c5)[-1] if len(c5) > 15 else price * .005
        risk = (price - sl) * sign
        if risk < max(price * MIN_RISK / 100, 1.5 * a):
            risk = max(price * MIN_RISK / 100, 1.5 * a)
            sl = price - sign * risk
        if risk > price * MAX_RISK / 100:
            continue
        lv = [l["price"] for l in levels_by.get(sym) or []] + [v for v in (hi20, lo20) if v]
        ahead = sorted({round(v, 2) for v in lv if (v - price) * sign >= 1.0 * risk}, key=lambda v: (v - price) * sign)
        t1 = ahead[0] if ahead and (ahead[0] - price) * sign <= 2.5 * risk else price + sign * 1.2 * risk
        t2 = next((v for v in ahead if (v - t1) * sign > 0.5 * risk), price + sign * 2.2 * risk)
        if (t2 - t1) * sign <= 0:
            t2 = t1 + sign * risk
        at20 = (hi20 and price > hi20) if bull else (lo20 and price < lo20)
        trend = (sma20 and price > sma20) if bull else (sma20 and price < sma20)
        sector_ok = sec > 0 if bull else sec < 0
        strength = pos if bull else 1 - pos
        why = [f"{pct:+.2f}% today", f"closing in the {'top' if bull else 'bottom'} {round((1 - strength) * 100)}% of the day's range",
               f"{'above' if bull else 'below'} yesterday's {'high' if bull else 'low'}", f"{rvol}× usual volume",
               f"{'above' if bull else 'below'} VWAP"]
        if at20:
            why.append(f"20-day {'high' if bull else 'low'} breakout")
        if trend:
            why.append(f"{'above' if bull else 'below'} its 20-day average")
        if sector_ok:
            why.append("sector agrees")
        base = (20 * _clamp((strength - 0.8) / 0.2) + 20 * _clamp((rvol - 1.5) / 2) + (15 if at20 else 0)
                + (10 if trend else 0) + (10 if sector_ok else 0) + 10 * _clamp((abs(pct) - 1.5) / 3)
                - (5 if abs(pct) > 5 else 0))
        out.append({"symbol": sym, "side": side, "price": price, "pct": pct, "rvol": rvol,
                    "vwap": round(vwap, 2), "base": round(base, 1), "why": why,
                    "plan": {"entry": price, "sl": round(sl, 2), "t1": round(t1, 2), "t2": round(t2, 2),
                             "zone": (price, price),
                             "basis": "BTST · buy near close" if bull else "STBT · sell near close",
                             "rr": round((t1 - price) * sign / risk, 2), "rr2": round((t2 - price) * sign / risk, 2),
                             "risk_pct": round(risk / price * 100, 2)},
                    "spark": [round(c["c"], 2) for c in today]})
    out.sort(key=lambda c: -c["base"])
    return out


def finish(cands, oi_by_symbol, n=5):
    """Add the OI build-up, drop OI that contradicts, keep the best n."""
    keep = []
    for c in cands:
        oi = oi_by_symbol.get(c["symbol"])
        want = "LONG BUILD-UP" if c["side"] == "bull" else "SHORT BUILD-UP"
        bad = "LONG UNWINDING" if c["side"] == "bull" else "SHORT COVERING"
        if oi and oi["kind"] == bad:
            continue                                   # money leaving the trade overnight
        c["oi"] = oi
        c["score"] = round(c["base"] + (15 if oi and oi["kind"] == want else 0), 1)
        if oi and oi["kind"] == want:
            c["why"].append(f'{oi["kind"].lower()} (OI {oi["oi_pct"]:+.1f}%)')
        keep.append(c)
    keep.sort(key=lambda c: -c["score"])
    return keep[:n]
