"""HTF key level breakouts: a 15-minute candle closing through a higher-timeframe
level (previous day / previous week high or low, daily swing high or low)."""
from datetime import date, timedelta

from analysis import _clamp, _day, atr

SESSION_START = 9 * 3600 + 15 * 60           # 09:15 IST, seconds after midnight
LEVEL_WEIGHT = {"PWH": 5, "PWL": 5, "PDH": 4, "PDL": 4, "Swing H": 3, "Swing L": 3}


def resample(candles, minutes):
    """Group intraday candles into `minutes` buckets counted from 09:15 IST.
    Each output candle gets "end": the epoch second its bucket closes."""
    out, key = [], None
    for c in candles:
        local = c["t"] + 19800
        idx = (local % 86400 - SESSION_START) // (minutes * 60)
        if idx < 0:                              # pre-open prints
            continue
        k = (local // 86400, idx)
        if k != key:
            start = (local // 86400) * 86400 - 19800 + SESSION_START + idx * minutes * 60
            out.append({"t": start, "o": c["o"], "h": c["h"], "l": c["l"], "c": c["c"], "v": c["v"],
                        "end": start + minutes * 60})
            key = k
        else:
            b = out[-1]
            b["h"], b["l"], b["c"], b["v"] = max(b["h"], c["h"]), min(b["l"], c["l"]), c["c"], b["v"] + c["v"]
    return out


def htf_levels(daily, session_day):
    """Key levels from daily candles before `session_day` (IST day number)."""
    days = [d for d in daily if _day(d["t"]) < session_day]
    if not days:
        return []
    levels = [("PDH", days[-1]["h"]), ("PDL", days[-1]["l"])]

    week = lambda d: (date(1970, 1, 1) + timedelta(days=_day(d["t"]))).isocalendar()[:2]
    this_week = (date(1970, 1, 1) + timedelta(days=session_day)).isocalendar()[:2]
    earlier = [d for d in days if week(d) < this_week]
    if earlier:
        last_week = [d for d in earlier if week(d) == week(earlier[-1])]
        levels += [("PWH", max(d["h"] for d in last_week)), ("PWL", min(d["l"] for d in last_week))]

    recent, k = days[-60:], 2
    for i in range(k, len(recent) - k):
        win = recent[i - k:i + k + 1]
        if recent[i]["h"] == max(d["h"] for d in win):
            levels.append(("Swing H", recent[i]["h"]))
        if recent[i]["l"] == min(d["l"] for d in win):
            levels.append(("Swing L", recent[i]["l"]))

    # Merge levels closer than 0.2%: keep the most important name, count the confluence.
    merged = []
    for name, price in sorted(levels, key=lambda l: l[1]):
        w = LEVEL_WEIGHT[name]
        if merged and abs(price - merged[-1]["price"]) / merged[-1]["price"] < 0.002:
            m = merged[-1]
            m["hits"] += 1
            if w > m["weight"]:
                m.update(name=name, price=price, weight=w)
        else:
            merged.append({"name": name, "price": price, "weight": w, "hits": 1})
    for m in merged:
        m["weight"] += min(2, m["hits"] - 1)          # confluence makes a level stronger
    return merged


def detect(candles15, daily, now_ts, price=None):
    """Best breakout of an HTF level in the latest session, or None.
    Only closed 15-minute candles count, and a breakout that later closed back
    through its level is treated as failed."""
    closed = [c for c in candles15 if c["end"] <= now_ts]
    if len(closed) < 10:
        return None, []
    session_day = _day(closed[-1]["t"])
    levels = htf_levels(daily, session_day)
    if not levels:
        return None, []
    a = atr(closed)
    price = price if price is not None else closed[-1]["c"]
    best = None
    for i in range(1, len(closed)):
        c, p = closed[i], closed[i - 1]
        if _day(c["t"]) != session_day:
            continue
        prior = closed[max(0, i - 20):i]
        avg_vol = sum(x["v"] for x in prior) / len(prior) or 1
        rng = (c["h"] - c["l"]) or 1e-9
        for lv in levels:
            L = lv["price"]
            if p["c"] <= L < c["c"]:
                side, strength = "bull", (c["c"] - c["l"]) / rng
            elif p["c"] >= L > c["c"]:
                side, strength = "bear", (c["h"] - c["c"]) / rng
            else:
                continue
            vol_ratio = c["v"] / avg_vol
            if strength < 0.6 or vol_ratio < 1.2:      # weak close or no volume: skip
                continue
            after = closed[i + 1:]
            if side == "bull":
                if any(x["c"] < L for x in after):
                    continue
                retest = any(x["l"] <= L * 1.0015 for x in after)
                stop = c["l"]
                above = [l["price"] for l in levels if l["price"] > max(c["c"], L) * 1.002]
                target = min(above) if above else None
            else:
                if any(x["c"] > L for x in after):
                    continue
                retest = any(x["h"] >= L * 0.9985 for x in after)
                stop = c["h"]
                below = [l["price"] for l in levels if l["price"] < min(c["c"], L) * 0.998]
                target = max(below) if below else None
            age = len(after)
            body = abs(c["c"] - c["o"]) / (a[i] or 1)
            extension = abs(price - L) / (a[-1] or 1)
            score = (30 * _clamp((vol_ratio - 1) / 2) + 20 * _clamp(body / 1.5)
                     + 20 * _clamp(lv["weight"] / 7) + 15 * (1 - _clamp(age / 12))
                     + 15 * (1 - _clamp(extension / 3)) + (5 if retest else 0))
            setup = {
                "side": side, "level": lv["name"], "level_price": L, "confluence": lv["hits"],
                "index": i, "time": c["t"], "candles_since": age,
                "status": "New" if age == 0 else "Retest" if retest else "Holding",
                "vol_ratio": round(vol_ratio, 1), "stop": stop, "target": target,
                "score": round(min(100, score), 1),
            }
            if best is None or (setup["score"], setup["time"]) > (best["score"], best["time"]):
                best = setup
    return best, levels
