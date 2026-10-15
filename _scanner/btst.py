"""BTST / STBT: stocks to buy (or, in F&O, sell) near today's close and exit tomorrow.

A good BTST closes strong: up on the day, near the day's high, above VWAP, on more
volume than usual, ideally above yesterday's high, in a strong sector, with long
build-up in futures and NIFTY not falling. STBT is the mirror image."""
from analysis import _clamp, _day, atr
from breakout import resample
from market import session

MAX_RISK = 3.0                                         # % of price; wider stops are skipped
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
        prev = [c for c in before if before and _day(c["t"]) == _day(before[-1]["t"])]
        pdh = max(c["h"] for c in prev) if prev else None
        pdl = min(c["l"] for c in prev) if prev else None
        sec = sector_pct.get(sym, 0)
        if pct >= 1.0 and pos >= .75 and vwap and price > vwap and rvol >= 1.2 and nifty_trend != "Bearish":
            side = "bull"
        elif pct <= -1.0 and pos <= .25 and vwap and price < vwap and rvol >= 1.2 and nifty_trend != "Bullish":
            side = "bear"
        else:
            continue
        sign = 1 if side == "bull" else -1
        hourly = [c for c in resample(today, 60) if c["end"] <= now_ts]
        if len(hourly) < 2:
            continue
        # Stop beyond the last two closed hourly candles (or VWAP if that is closer to price).
        if side == "bull":
            sl = min(c["l"] for c in hourly[-2:])
            sl = max(sl, vwap * 0.998) if vwap < price else sl
        else:
            sl = max(c["h"] for c in hourly[-2:])
            sl = min(sl, vwap * 1.002) if vwap > price else sl
        a = atr(c5)[-1] if len(c5) > 15 else price * .005
        risk = (price - sl) * sign
        if risk < price * MIN_RISK / 100:
            sl, risk = price - sign * max(price * MIN_RISK / 100, 1.5 * a), max(price * MIN_RISK / 100, 1.5 * a)
        if risk > price * MAX_RISK / 100:
            continue
        lv = [l["price"] for l in levels_by.get(sym) or []] + [v for v in (pdh, pdl, hi, lo) if v]
        ahead = sorted({round(v, 2) for v in lv if (v - price) * sign >= 1.2 * risk}, key=lambda v: (v - price) * sign)
        t1 = ahead[0] if ahead else price + sign * 1.5 * risk
        t2 = ahead[1] if len(ahead) > 1 else price + sign * 2.5 * risk
        if (t2 - t1) * sign <= 0:
            t2 = t1 + sign * risk
        broke = (pdh and price > pdh) if side == "bull" else (pdl and price < pdl)
        sector_ok = sec > 0 if side == "bull" else sec < 0
        why = [f"{pct:+.2f}% today", f"closing near the day's {'high' if side == 'bull' else 'low'}",
               f"{'above' if side == 'bull' else 'below'} VWAP", f"{rvol}× usual volume"]
        if broke:
            why.append(f"{'above yesterday’s high' if side == 'bull' else 'below yesterday’s low'}")
        if sector_ok:
            why.append("sector agrees")
        strength = pos if side == "bull" else 1 - pos
        base = (25 * _clamp(abs(pct) / 4) + 20 * _clamp((strength - .75) / .25) + 20 * _clamp((rvol - 1) / 2)
                + 10 * (1 if broke else 0) + 10 * (1 if sector_ok else 0))
        out.append({"symbol": sym, "side": side, "price": price, "pct": pct, "rvol": rvol,
                    "vwap": round(vwap, 2), "base": round(base, 1), "why": why,
                    "plan": {"entry": price, "sl": sl, "t1": t1, "t2": t2, "zone": (price, price),
                             "basis": "BTST · buy near close" if side == "bull" else "STBT · sell near close",
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
