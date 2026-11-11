"""Swing trades on the daily chart: Fair Value Gaps + price action + futures OI.

A swing trade is held for days (up to 10 sessions), not overnight only. The setup, for a buy
(a sell is the mirror image, taken through futures or a put):

  1. Trend (price action)   daily structure is up: higher highs and higher lows, or a fresh bullish
                            change of character (CHoCH) / break of structure (BOS); close above the 50-day EMA.
  2. A zone to buy from     price has come back into a bullish "imbalance" zone and held it. All the
                            common Fair Value Gap types count:
                              - FVG: candle 1's high below candle 3's low (fresh = untouched, or partly filled)
                              - Inverse FVG (IFVG): a bearish FVG that price closed through; it flips to support
                              - BPR (balanced price range): where a bullish and a bearish FVG overlap
                              - Gap: today's low above yesterday's high (a true price gap), until filled
                            The middle of a zone (its "consequent encroachment", CE) has to hold on a close.
                            Better in the "discount" half of the latest swing range (premium for sells).
  3. Price action trigger   the day that tapped the zone closes strong: a hammer / pin bar, a bullish
                            engulfing, or a strong close in the top part of its range, back above the CE.
  4. Futures OI             over the last 5 sessions: long build-up (price up, OI up) is best, short
                            covering is fine, short build-up against the trade rules it out.
  5. Participation          the trigger day's volume vs its 20-day average, and strength vs NIFTY.

Plan: buy above the trigger day's high (valid for 3 sessions), stop below the zone and the trigger low,
T1 = the nearest swing high / opposing zone (at least 1.5R), T2 = the next one (or 3R); after T1 half is
booked and the stop moves to cost; anything still open is closed after 10 sessions.
"""
from analysis import _clamp, _day, atr

RULES = {"sides": "both", "zones": "all", "need_bos": False, "vol_min": 0.0, "discount_only": False,
         "t1_r": 0, "score_min": 0, "skip_sectors": []}
LOOKBACK = 60                                          # sessions of zones to consider
HOLD_DAYS = 10                                         # time stop
TRIGGER_DAYS = 3                                       # the entry must trigger within this many sessions


def rules_of(r):
    return dict(RULES, **(r or {}))


def ema(vals, n):
    out, k, e = [], 2 / (n + 1), None
    for v in vals:
        e = v if e is None else e + k * (v - e)
        out.append(e)
    return out


def pivots(d, k=2):
    """Swing highs and lows (k candles each side): [(index, price)]."""
    hi, lo = [], []
    for i in range(k, len(d) - k):
        win = d[i - k:i + k + 1]
        if d[i]["h"] == max(c["h"] for c in win):
            hi.append((i, d[i]["h"]))
        if d[i]["l"] == min(c["l"] for c in win):
            lo.append((i, d[i]["l"]))
    return hi, lo


def structure(d):
    """Daily market structure: trend, last BOS / CHoCH, swing range."""
    hi, lo = pivots(d)
    out = {"trend": "range", "event": None, "event_at": None, "hi": hi, "lo": lo}
    if len(hi) >= 2 and len(lo) >= 2:
        hh, hl = hi[-1][1] > hi[-2][1], lo[-1][1] > lo[-2][1]
        lh, ll = hi[-1][1] < hi[-2][1], lo[-1][1] < lo[-2][1]
        out["trend"] = "up" if hh and hl else "down" if lh and ll else "range"
    # The latest close through the last swing high / low: BOS with the trend, CHoCH against it.
    for i in range(len(d) - 1, max(0, len(d) - 11), -1):
        ph = [p for j, p in hi if j < i - 1]
        pl = [p for j, p in lo if j < i - 1]
        if ph and d[i]["c"] > ph[-1] >= d[i - 1]["c"]:
            out["event"] = "BOS up" if out["trend"] != "down" else "CHoCH up"
            out["event_at"] = i
            break
        if pl and d[i]["c"] < pl[-1] <= d[i - 1]["c"]:
            out["event"] = "BOS down" if out["trend"] != "up" else "CHoCH down"
            out["event_at"] = i
            break
    return out


def zones(d, a):
    """Every imbalance zone of the last LOOKBACK sessions, with its type, side and state.
    Each zone: {"type", "side" (bull = support / bear = resistance), "bottom", "top", "ce", "at", "state"}."""
    n = len(d)
    start = max(2, n - LOOKBACK)
    raw = []
    for i in range(start, n):
        c1, c3 = d[i - 2], d[i]
        if c3["l"] > c1["h"] and c3["l"] - c1["h"] >= 0.1 * a:
            raw.append({"type": "FVG", "side": "bull", "bottom": c1["h"], "top": c3["l"], "at": i})
        if c3["h"] < c1["l"] and c1["l"] - c3["h"] >= 0.1 * a:
            raw.append({"type": "FVG", "side": "bear", "bottom": c3["h"], "top": c1["l"], "at": i})
        p = d[i - 1]
        if d[i]["l"] > p["h"] and d[i]["l"] - p["h"] >= 0.1 * a:      # true gap between two days
            raw.append({"type": "Gap", "side": "bull", "bottom": p["h"], "top": d[i]["l"], "at": i})
        if d[i]["h"] < p["l"] and p["l"] - d[i]["h"] >= 0.1 * a:
            raw.append({"type": "Gap", "side": "bear", "bottom": d[i]["h"], "top": p["l"], "at": i})
    out = []
    for z in raw:
        after = d[z["at"] + 1:]
        lo, hi = z["bottom"], z["top"]
        if z["side"] == "bull":
            broken = next((k for k, c in enumerate(after) if c["c"] < lo), None)
            touched = any(c["l"] <= hi for c in after)
            depth = max([(hi - c["l"]) / (hi - lo) for c in after if c["l"] <= hi] or [0])
        else:
            broken = next((k for k, c in enumerate(after) if c["c"] > hi), None)
            touched = any(c["h"] >= lo for c in after)
            depth = max([(c["h"] - lo) / (hi - lo) for c in after if c["h"] >= lo] or [0])
        if broken is not None:
            if z["type"] != "FVG":
                continue                               # a filled gap is gone
            # Closed through: an inverse FVG on the other side, from the day it broke.
            inv = dict(z, type="IFVG", side="bear" if z["side"] == "bull" else "bull", at=z["at"] + 1 + broken)
            again = d[inv["at"] + 1:]
            if inv["side"] == "bull" and any(c["c"] < lo for c in again):
                continue
            if inv["side"] == "bear" and any(c["c"] > hi for c in again):
                continue
            inv["state"] = "inverted"
            out.append(inv)
            continue
        z["state"] = "fresh" if not touched else "partial"
        z["fill"] = round(_clamp(depth), 2)
        out.append(z)
    # Balanced price range: a bullish and a bearish FVG that overlap; it acts with the newer one.
    fv = [z for z in out if z["type"] == "FVG"]
    for i, a1 in enumerate(fv):
        for b1 in fv[i + 1:]:
            if a1["side"] == b1["side"]:
                continue
            lo, hi = max(a1["bottom"], b1["bottom"]), min(a1["top"], b1["top"])
            if hi > lo:
                newer = a1 if a1["at"] > b1["at"] else b1
                out.append({"type": "BPR", "side": newer["side"], "bottom": lo, "top": hi, "at": newer["at"],
                            "state": "fresh"})
    for z in out:
        z["ce"] = round((z["bottom"] + z["top"]) / 2, 2)
        z["bottom"], z["top"] = round(z["bottom"], 2), round(z["top"], 2)
    return out


def candle_signal(c, prev, side):
    """Price action of the trigger day: hammer/pin, engulfing or strong close (None if weak)."""
    rng = (c["h"] - c["l"]) or 1e-9
    body = abs(c["c"] - c["o"])
    if side == "bull":
        lower = min(c["o"], c["c"]) - c["l"]
        if lower >= 0.5 * rng and (c["c"] - c["l"]) / rng >= 0.6:
            return "Hammer / pin bar"
        if c["c"] > c["o"] and prev["c"] < prev["o"] and c["c"] >= prev["o"] and c["o"] <= prev["c"]:
            return "Bullish engulfing"
        if c["c"] > c["o"] and (c["c"] - c["l"]) / rng >= 0.7 and body >= 0.4 * rng:
            return "Strong close"
    else:
        upper = c["h"] - max(c["o"], c["c"])
        if upper >= 0.5 * rng and (c["h"] - c["c"]) / rng >= 0.6:
            return "Shooting star / pin bar"
        if c["c"] < c["o"] and prev["c"] > prev["o"] and c["c"] <= prev["o"] and c["o"] >= prev["c"]:
            return "Bearish engulfing"
        if c["c"] < c["o"] and (c["h"] - c["c"]) / rng >= 0.7 and body >= 0.4 * rng:
            return "Weak close"
    return None


def futures_oi(fut_daily, n=5):
    """Build-up over the last n sessions from daily futures candles with OI."""
    f = [c for c in fut_daily or [] if c.get("oi")]
    if len(f) < n + 1:
        return None
    p0, p1, o0, o1 = f[-n - 1]["c"], f[-1]["c"], f[-n - 1]["oi"], f[-1]["oi"]
    dp, doi = (p1 - p0) / p0 * 100, (o1 - o0) / o0 * 100
    kind = ("LONG BUILD-UP" if doi > 0 else "SHORT COVERING") if dp > 0 else ("SHORT BUILD-UP" if doi > 0 else "LONG UNWINDING")
    return {"kind": kind, "oi_pct": round(doi, 1), "price_pct": round(dp, 1), "days": n}


def analyse(sym, d, nifty_d=None, oi=None, rules=None, sector=None):
    """The best swing setup today for one stock (d = daily candles, today last), or None."""
    rules = rules_of(rules)
    if len(d) < 60:
        return None
    a = atr(d)[-1] or d[-1]["c"] * 0.02
    today, prev = d[-1], d[-2]
    price = today["c"]
    closes = [c["c"] for c in d]
    e50, e20 = ema(closes, 50), ema(closes, 20)
    st = structure(d)
    zs = zones(d[:-1], a)                              # zones formed before today
    hi, lo = st["hi"], st["lo"]
    swing_hi = max((p for _, p in hi[-3:]), default=max(c["h"] for c in d[-40:]))
    swing_lo = min((p for _, p in lo[-3:]), default=min(c["l"] for c in d[-40:]))
    mid = (swing_hi + swing_lo) / 2
    vol20 = sum(c["v"] for c in d[-21:-1]) / 20 or 1
    vol_x = today["v"] / vol20
    rs = None
    if nifty_d and len(nifty_d) > 21:
        rs = round(((price / d[-21]["c"]) - (nifty_d[-1]["c"] / nifty_d[-21]["c"])) * 100, 2)
    best = None
    for side in ("bull", "bear"):
        if rules["sides"] not in ("both", side):
            continue
        sign = 1 if side == "bull" else -1
        trend_ok = (st["trend"] == "up" or (st["event"] or "").endswith("up")) if side == "bull" else \
                   (st["trend"] == "down" or (st["event"] or "").endswith("down"))
        ema_ok = (price > e50[-1]) if side == "bull" else (price < e50[-1])
        if not trend_ok or not ema_ok:
            continue
        if rules["need_bos"] and not (st["event"] or "").endswith("up" if side == "bull" else "down"):
            continue
        sig = candle_signal(today, prev, side)
        if not sig:
            continue
        for z in zs:
            if z["side"] != side:
                continue
            if rules["zones"] == "fresh" and z["state"] != "fresh":
                continue
            # Tapped the zone today (or yesterday) and closed back beyond its CE.
            tapped = (min(today["l"], prev["l"]) <= z["top"]) if side == "bull" else (max(today["h"], prev["h"]) >= z["bottom"])
            held = (price >= z["ce"] and today["l"] >= z["bottom"] - 0.25 * a) if side == "bull" else \
                   (price <= z["ce"] and today["h"] <= z["top"] + 0.25 * a)
            if not (tapped and held):
                continue
            discount = (z["ce"] <= mid) if side == "bull" else (z["ce"] >= mid)
            if rules["discount_only"] and not discount:
                continue
            if vol_x < rules["vol_min"]:
                continue
            entry = today["h"] if side == "bull" else today["l"]
            stop_ref = min(z["bottom"], today["l"]) if side == "bull" else max(z["top"], today["h"])
            sl = stop_ref - sign * 0.2 * a
            risk = abs(entry - sl)
            if risk <= 0 or risk > 2.5 * a or risk > entry * 0.08:
                continue
            # Targets: swing highs (lows) and opposing zones ahead of the entry.
            ahead = sorted({round(p, 2) for _, p in (hi if side == "bull" else lo)[-6:] if (p - entry) * sign >= 1.5 * risk} |
                           {(o["bottom"] if side == "bull" else o["top"]) for o in zs if o["side"] != side
                            and ((o["bottom"] if side == "bull" else o["top"]) - entry) * sign >= 1.5 * risk},
                           key=lambda v: (v - entry) * sign)
            t1 = ahead[0] if ahead and (ahead[0] - entry) * sign <= 4 * risk else entry + sign * 2 * risk
            t2 = next((v for v in ahead if (v - t1) * sign > 0.5 * risk), entry + sign * 3.5 * risk)
            if rules["t1_r"]:
                t1, t2 = entry + sign * rules["t1_r"] * risk, entry + sign * 2 * rules["t1_r"] * risk
            if (t2 - t1) * sign <= 0:
                t2 = t1 + sign * 1.5 * risk
            # OI over 5 sessions.
            want, ok2 = ("LONG BUILD-UP", "SHORT COVERING") if side == "bull" else ("SHORT BUILD-UP", "LONG UNWINDING")
            against = ("SHORT BUILD-UP",) if side == "bull" else ("LONG BUILD-UP",)
            if oi and oi["kind"] in against:
                continue
            ev = st["event"] or ""
            parts = {
                "structure": (10 if st["trend"] == ("up" if side == "bull" else "down") else 4)
                             + (8 if ev.endswith("up" if side == "bull" else "down") else 0)
                             + (7 if (e20[-1] - e20[-6]) * sign > 0 else 0),                         # /25
                "zone": {"fresh": 10, "partial": 7, "inverted": 8}.get(z["state"], 7)
                        + (5 if z["type"] in ("BPR", "IFVG") else 3 if z["type"] == "Gap" else 0)
                        + (5 if discount else 0) + 5 * _clamp((z["top"] - z["bottom"]) / a),        # /25
                "price_action": {"Hammer / pin bar": 20, "Shooting star / pin bar": 20, "Bullish engulfing": 18,
                                 "Bearish engulfing": 18}.get(sig, 13),                              # /20
                "oi": 15 if oi and oi["kind"] == want else 8 if oi and oi["kind"] == ok2 else 6 if not oi else 0,   # /15
                "participation": 8 * _clamp((vol_x - 0.8) / 1.2) + (7 if rs is not None and rs * sign > 0 else 0),  # /15
            }
            parts = {k: round(v, 1) for k, v in parts.items()}
            score = round(sum(parts.values()), 1)
            if score < rules["score_min"]:
                continue
            word = "Buy above" if side == "bull" else "Sell below"
            ztxt = f'{"Bullish" if side == "bull" else "Bearish"} {z["type"]} {z["bottom"]:.2f}–{z["top"]:.2f}'
            res = {"symbol": sym, "side": side, "price": round(price, 2), "score": score, "parts": parts,
                   "zone": z, "zone_text": ztxt, "signal": sig, "trend": st["trend"], "event": st["event"],
                   "discount": discount, "vol_x": round(vol_x, 2), "rs": rs, "oi": oi, "sector": sector,
                   "ema50": round(e50[-1], 2), "atr": round(a, 2),
                   "why": [f'{"Uptrend (HH/HL)" if st["trend"] == "up" else "Downtrend (LH/LL)" if st["trend"] == "down" else "Range"}'
                           + (f' · {st["event"]}' if st["event"] else ""),
                           f'{ztxt} · {z["state"]}' + (" · CE held" if True else ""),
                           f"{sig} at the zone", ("Discount zone" if side == "bull" else "Premium zone") if discount else
                           ("Premium half (less ideal)" if side == "bull" else "Discount half (less ideal)"),
                           f"Volume {vol_x:.1f}× 20-day"] + ([f'{oi["kind"].lower()} (OI {oi["oi_pct"]:+.1f}% in {oi["days"]}d)'] if oi else []),
                   "plan": {"basis": f"Swing · {z['type']}" + (" (inverse)" if z["type"] == "IFVG" else ""),
                            "entry": round(entry, 2), "sl": round(sl, 2), "t1": round(t1, 2), "t2": round(t2, 2),
                            "zone": (z["bottom"], z["top"]), "rr": round((t1 - entry) * sign / risk, 2),
                            "rr2": round((t2 - entry) * sign / risk, 2), "risk_pct": round(risk / entry * 100, 2),
                            "action": f"{word} {entry:.2f} within {TRIGGER_DAYS} sessions · stop {sl:.2f} · hold up to {HOLD_DAYS} sessions"}}
            if best is None or res["score"] > best["score"]:
                best = res
    return best


def rank(cands, n=5):
    return sorted([c for c in cands if c], key=lambda c: -c["score"])[:n]
