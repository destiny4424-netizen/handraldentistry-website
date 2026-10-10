"""Order block, liquidity and strength analysis on a list of OHLCV candles.

A candle is a dict: {"t": epoch seconds, "o", "h", "l", "c", "v"}.
"""


def atr(candles, n=14):
    out, prev_close, avg = [], None, None
    for i, k in enumerate(candles):
        tr = k["h"] - k["l"] if prev_close is None else max(
            k["h"] - k["l"], abs(k["h"] - prev_close), abs(k["l"] - prev_close))
        avg = tr if avg is None else (avg * (n - 1) + tr) / n
        out.append(avg)
        prev_close = k["c"]
    return out


def swing_points(candles, k):
    """Swing highs/lows with k bars on each side. Returns lists of (index, price)."""
    highs, lows = [], []
    for i in range(k, len(candles) - k):
        h, l = candles[i]["h"], candles[i]["l"]
        window = candles[i - k:i + k + 1]
        if h == max(c["h"] for c in window) and all(c["h"] < h for c in candles[i - k:i]):
            highs.append((i, h))
        if l == min(c["l"] for c in window) and all(c["l"] > l for c in candles[i - k:i]):
            lows.append((i, l))
    return highs, lows


def find_order_blocks(candles, k=3):
    """Walk the candles, create an order block on every break of structure and
    retire it once price closes through it. Returns the blocks still active."""
    if len(candles) < 2 * k + 20:
        return [], []
    a = atr(candles)
    highs, lows = swing_points(candles, k)
    # A swing at index i only becomes known k bars later.
    high_at = {i + k: (i, p) for i, p in highs}
    low_at = {i + k: (i, p) for i, p in lows}
    last_high = last_low = None
    high_broken = low_broken = True
    bulls, bears = [], []

    for j, c in enumerate(candles):
        if j in high_at:
            last_high, high_broken = high_at[j], False
        if j in low_at:
            last_low, low_broken = low_at[j], False

        # Mitigation and touches of existing zones.
        for ob in bulls:
            if ob["active"] and j > ob["created"]:
                if c["c"] < ob["bottom"]:
                    ob["active"] = False
                elif c["l"] <= ob["top"]:
                    ob["tests"] += 1
        for ob in bears:
            if ob["active"] and j > ob["created"]:
                if c["c"] > ob["top"]:
                    ob["active"] = False
                elif c["h"] >= ob["bottom"]:
                    ob["tests"] += 1

        if last_high and not high_broken and c["c"] > last_high[1]:
            high_broken = True
            seg = range(last_high[0], j)
            idx = min(seg, key=lambda i: candles[i]["l"])
            bulls.append(_block(candles, a, idx, j, "bull"))
        if last_low and not low_broken and c["c"] < last_low[1]:
            low_broken = True
            seg = range(last_low[0], j)
            idx = max(seg, key=lambda i: candles[i]["h"])
            bears.append(_block(candles, a, idx, j, "bear"))

    return [b for b in bulls if b["active"]], [b for b in bears if b["active"]]


def _block(candles, a, idx, broke_at, side):
    ob = candles[idx]
    brk = candles[broke_at]
    vols = [c["v"] for c in candles[max(0, broke_at - 20):broke_at]] or [1]
    avg_vol = sum(vols) / len(vols) or 1
    leg = range(idx + 1, broke_at + 1)
    peak_vol = max((candles[i]["v"] for i in leg), default=brk["v"])
    if side == "bull":
        move = brk["c"] - ob["l"]
    else:
        move = ob["h"] - brk["c"]
    return {
        "side": side, "index": idx, "created": broke_at, "active": True, "tests": 0,
        "top": ob["h"], "bottom": ob["l"],
        "impulse": move / (a[broke_at] or 1),          # size of the move in ATRs
        "vol_ratio": peak_vol / avg_vol,                # volume during the move
    }


def _clamp(x, lo=0.0, hi=1.0):
    return max(lo, min(hi, x))


def analyse(candles, k=3):
    """Score one stock. Returns a dict with its best bullish and bearish setups."""
    if len(candles) < 40:
        return None
    a = atr(candles)[-1] or 1
    price = candles[-1]["c"]
    prev_close = _previous_close(candles)
    pct = (price - prev_close) / prev_close * 100 if prev_close else 0.0
    highs, lows = swing_points(candles, k)
    bulls, bears = find_order_blocks(candles, k)

    best = {}
    for ob in bulls:
        if ob["top"] > price:          # price must be above a bullish zone
            continue
        dist = (price - ob["top"]) / a
        score = (30 * _clamp(ob["impulse"] / 4) + 25 * (1 - _clamp(dist / 4))
                 + 15 * (1 if ob["tests"] == 0 else 0.4) + 15 * _clamp((ob["vol_ratio"] - 1) / 2)
                 + 15 * _clamp(pct / 3))
        if "bull" not in best or score > best["bull"]["score"]:
            best["bull"] = dict(ob, score=round(score, 1))
    for ob in bears:
        if ob["bottom"] < price:       # price must be below a bearish zone
            continue
        dist = (ob["bottom"] - price) / a
        score = (30 * _clamp(ob["impulse"] / 4) + 25 * (1 - _clamp(dist / 4))
                 + 15 * (1 if ob["tests"] == 0 else 0.4) + 15 * _clamp((ob["vol_ratio"] - 1) / 2)
                 + 15 * _clamp(-pct / 3))
        if "bear" not in best or score > best["bear"]["score"]:
            best["bear"] = dict(ob, score=round(score, 1))

    for side, ob in best.items():
        ob.update(_levels(candles, side, ob, price, highs, lows))
    return {"price": price, "prev_close": prev_close, "pct": round(pct, 2),
            "atr": a, "bull": best.get("bull"), "bear": best.get("bear")}


def _levels(candles, side, ob, price, highs, lows):
    """Support, resistance and the liquidity pool (resting stops) for a setup.
    A level is None when there is nothing on the chart to put it at."""
    pdh, pdl = _previous_day_range(candles)
    if side == "bull":
        below = sorted((p for _, p in lows if p < ob["bottom"]), reverse=True)
        support = below[0] if below else None
        floor = support if support is not None else ob["bottom"]
        under = [p for p in below if p < floor]
        liquidity = under[0] if under else (pdl if pdl is not None and pdl < floor else None)
        above = sorted(p for _, p in highs if p > price)
        resistance = above[0] if above else (pdh if pdh is not None and pdh > price else None)
    else:
        above = sorted(p for _, p in highs if p > ob["top"])
        resistance = above[0] if above else None
        ceiling = resistance if resistance is not None else ob["top"]
        over = [p for p in above if p > ceiling]
        liquidity = over[0] if over else (pdh if pdh is not None and pdh > ceiling else None)
        below = sorted((p for _, p in lows if p < price), reverse=True)
        support = below[0] if below else (pdl if pdl is not None and pdl < price else None)
    return {"support": support, "resistance": resistance, "liquidity": liquidity}


def _day(t):
    return (t + 19800) // 86400        # IST calendar day


def _previous_day_range(candles):
    today = _day(candles[-1]["t"])
    prev = [c for c in candles if _day(c["t"]) < today]
    if not prev:
        return None, None
    last = _day(prev[-1]["t"])
    day = [c for c in prev if _day(c["t"]) == last]
    return max(c["h"] for c in day), min(c["l"] for c in day)


def _previous_close(candles):
    """Close of the last candle of the previous trading day (IST)."""
    today = _day(candles[-1]["t"])
    for c in reversed(candles):
        if _day(c["t"]) < today:
            return c["c"]
    return candles[0]["o"]
