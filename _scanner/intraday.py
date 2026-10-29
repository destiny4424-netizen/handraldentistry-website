"""Intraday picks: 15-minute breakouts of yesterday's high (buy) or low (sell), confirmed by volume.

This is the classic day-trader setup, kept deliberately simple (order blocks have their own tab):

    1. Broke the level   a 15-minute candle CLOSES above yesterday's high (PDH) / below its low (PDL)
    2. Volume            that candle traded at least 1.5x the usual volume for that time of day
    3. Strong candle     it closed near its high (low for a sell) with a real body, not a wick
    4. Holding           no 15-minute close back through the level since, and price is not more than
                         1R past the entry (not extended - otherwise wait for a retest)
    5. Trend agrees      price on the right side of VWAP and the 15-minute Supertrend

Entry is the break of the breakout candle (or of the retest candle if price came back to the level and
held). The stop goes just beyond the broken level, which should now act as support (resistance).
Targets are the next higher-timeframe levels, or 1.5R and 3R.
"""
from datetime import datetime, timedelta, timezone

from analysis import _clamp, _day, atr
from market import supertrend
from picks import sector_of, sector_table

IST = timezone(timedelta(hours=5, minutes=30))
STAGES = ["Scanned", "Broke PDH/PDL", "Volume 1.5×+", "Strong candle", "Holding", "Trend agrees", "Intraday picks"]
VOL_X = 1.5                                            # breakout candle volume vs usual for that time
LAST_ENTRY = "14:00"                                   # no fresh intraday breakouts after this (IST)


def _hm(ts):
    return datetime.fromtimestamp(ts, IST).strftime("%H:%M")


def _slot(c):
    """Index of a 15-minute candle within its session (0 = 9:15-9:30)."""
    return ((c["t"] + 19800) % 86400 - (9 * 3600 + 15 * 60)) // 900


def usual_volume(c15, day):
    """Average volume per time-of-day slot over the previous sessions (falls back to the overall average)."""
    prev = [c for c in c15 if _day(c["t"]) < day]
    by = {}
    for c in prev:
        by.setdefault(_slot(c), []).append(c["v"])
    overall = sum(c["v"] for c in prev) / len(prev) if prev else None
    return {k: sum(v) / len(v) for k, v in by.items()}, overall


def analyse(c15, pdh, pdl, price, vwap, now_ts, levels):
    """Best PDH/PDL breakout today for one stock, or (None, stage reached, side)."""
    closed = [c for c in c15 if c["end"] <= now_ts]
    if len(closed) < 2 or not pdh or not pdl:
        return None, 0, None
    day = _day(closed[-1]["t"])
    today = [c for c in closed if _day(c["t"]) == day]
    if not today:
        return None, 0, None
    if not any(c["c"] > pdh or c["c"] < pdl for c in today):
        return None, 0, None                           # nothing broke yet: skip the heavier work
    usual, overall = usual_volume(closed, day)
    a15 = atr(closed)[-1] or price * 0.004
    best, best_stage, best_side = None, 0, None
    for side, L in (("bull", pdh), ("bear", pdl)):
        sign = 1 if side == "bull" else -1
        brk_i = next((i for i, c in enumerate(today) if (c["c"] - L) * sign > 0), None)
        if brk_i is None:
            continue
        stage = 1
        brk = today[brk_i]
        after = today[brk_i + 1:]
        if any((c["c"] - L) * sign <= 0 for c in after):
            # Closed back through the level: failed. A fresh break after that counts again.
            back = max(i for i, c in enumerate(today) if (c["c"] - L) * sign <= 0)
            nxt = next((i for i in range(back + 1, len(today)) if (today[i]["c"] - L) * sign > 0), None)
            if nxt is None:
                if stage > best_stage:
                    best_stage, best_side = stage, side
                continue
            brk_i, brk = nxt, today[nxt]
            after = today[nxt + 1:]
        if _hm(brk["end"]) > LAST_ENTRY:
            continue
        base = usual.get(_slot(brk)) or overall or brk["v"]
        vol_x = brk["v"] / base if base else 1
        rng = (brk["h"] - brk["l"]) or 1e-9
        close_pos = (brk["c"] - brk["l"]) / rng if side == "bull" else (brk["h"] - brk["c"]) / rng
        body = abs(brk["c"] - brk["o"]) / rng
        gap = (brk_i == 0 and (brk["o"] - L) * sign > 0)
        if vol_x >= VOL_X:
            stage = 2
            if close_pos >= 0.6 and (body >= 0.4 or gap):
                stage = 3
        # Retest: price came back to the level and held -> enter on the retest candle instead.
        if side == "bull":
            retest = next((c for c in after if c["l"] <= L * 1.002), None)
        else:
            retest = next((c for c in after if c["h"] >= L * 0.998), None)
        signal = retest or brk
        entry = signal["h"] if side == "bull" else signal["l"]
        risk = min(max(abs(entry - L) + 0.25 * a15, 0.6 * a15), 2.0 * a15)
        sl = entry - sign * risk
        hit = next((c for c in today if c["t"] > signal["t"] and (c["h"] >= entry if side == "bull" else c["l"] <= entry)), None)
        extended = (price - entry) * sign > risk
        holding = (price - L) * sign > 0 and not extended
        if stage >= 3 and holding:
            stage = 4
        st = supertrend(closed) if len(closed) > 12 else []
        st_ok = bool(st) and (st[-1][1] == 1) == (side == "bull")
        vwap_ok = vwap is None or (price - vwap) * sign >= 0
        if stage >= 4 and vwap_ok and st_ok:
            stage = 5
        ahead = sorted({round(l["price"], 2) for l in levels if (l["price"] - entry) * sign > 1.0 * risk},
                       key=lambda v: (v - entry) * sign)
        t1 = ahead[0] if ahead and (ahead[0] - entry) * sign <= 3 * risk else entry + sign * 1.5 * risk
        t2 = next((v for v in ahead if (v - t1) * sign > 0.5 * risk), entry + sign * 3 * risk)
        if (t2 - t1) * sign <= 0:
            t2 = t1 + sign * 1.5 * risk
        also = [l["name"] for l in levels if l["name"] in (("PWH",) if side == "bull" else ("PWL",))
                and (brk["c"] - l["price"]) * sign > 0 and (l["price"] - L) * sign >= 0]
        word = "Buy above" if side == "bull" else "Sell below"
        name = "PDH" if side == "bull" else "PDL"
        age = len([c for c in today if c["t"] > brk["t"]])
        # Score out of 100, in five parts (shown on each pick):
        parts = {"volume": 25 * _clamp((vol_x - 1) / 2),                                   # /25
                 "candle": 12 * _clamp((close_pos - 0.5) / 0.5) + 8 * (1 if gap else _clamp(body)),   # /20
                 "structure": min(15, 8 * (1 - _clamp(age / 8)) + (5 if retest else 0) + (2 if also else 0)),  # /15
                 "trend": (5 if st_ok else 0) + (5 if vwap_ok and vwap else 0),             # /25 with sector + NIFTY
                 "participation": 0.0}                                                      # /15: RVOL + OI
        score = sum(parts.values())
        res = {"side": side, "level": round(L, 2), "level_name": name, "vol_x": round(vol_x, 1),
               "close_pos": round(close_pos, 2), "gap": gap, "retest": bool(retest), "extended": extended,
               "supertrend": {"ok": st_ok, "value": round(st[-1][0], 2)} if st else None, "vwap_ok": vwap_ok,
               "broke_at": _hm(brk["end"]), "also": also, "score": round(score, 1), "parts": parts, "age": age,
               "plan": {"basis": f"{name} breakout · 15m" + (" (gap)" if gap else "") + (" · retest" if retest else ""),
                        "entry": round(entry, 2), "sl": round(sl, 2), "t1": round(t1, 2), "t2": round(t2, 2),
                        "zone": (round(min(L, brk["c"]), 2), round(max(L, brk["c"]), 2)),
                        "rr": round((t1 - entry) * sign / risk, 2), "rr2": round((t2 - entry) * sign / risk, 2),
                        "risk_pct": round(risk / entry * 100, 2), "confirmed": True,
                        "held_at": signal["t"], "triggered_at": hit["t"] if hit else None,
                        "status": "Triggered" if hit else f"{word} {entry:.2f}",
                        "action": f"{word} {entry:.2f}" + (" (retest of " + name + ")" if retest else ""),
                        "pd_rule": True}}
        if stage > best_stage or (stage == best_stage and best and res["score"] > best["score"]):
            best, best_stage, best_side = res, stage, side
    return best, best_stage, best_side


def build(stocks, n_picks=6, picks_open=True, nifty_trend=None):
    """stocks: {symbol: {"res", "levels", "pdh", "pdl", "c15", "vwap", "now"}} -> (candidates, finish)."""
    pcts = {s: d["res"]["pct"] for s, d in stocks.items()}
    sectors = sector_table(pcts)
    sec = {r["name"]: r for r in sectors}
    flow, cands, watch = {}, [], []
    for sym, d in stocks.items():
        res = d["res"]
        price = res["price"]
        b, stage, side = analyse(d["c15"], d.get("pdh"), d.get("pdl"), price, res.get("vwap"), d["now"], d["levels"])
        flow[sym] = {"symbol": sym, "pct": res["pct"], "side": side or ("bull" if res["pct"] >= 0 else "bear"), "stage": stage}
        if b and stage >= 5:
            s = sec[sector_of(sym)]
            sector_ok = (s["pct"] > 0) if side == "bull" else (s["pct"] < 0)
            with_n = nifty_trend == ("Bullish" if side == "bull" else "Bearish")
            nifty_pts = 10 if with_n else 5 if nifty_trend in (None, "Sideways") else 0
            rv = res.get("rvol") or 1
            parts = dict(b["parts"])
            parts["trend"] += (5 if sector_ok else 0) + nifty_pts
            parts["participation"] = 7 * _clamp((rv - 1) / 2)
            b["parts"] = parts
            b["nifty"] = "with" if with_n else "sideways" if nifty_trend in (None, "Sideways") else "against"
            b["base"] = round(sum(parts.values()), 1)
            cands.append(dict(b, symbol=sym, price=price, pct=res["pct"], sector=sector_of(sym),
                              sector_pct=s["pct"], sector_rank=s["rank"],
                              sources=[b["plan"]["basis"]]))
        elif not b or stage < 1:
            # Not broken yet: on the watchlist when price is within 0.5% of the level on good volume.
            pdh, pdl = d.get("pdh"), d.get("pdl")
            rv = res.get("rvol") or 0
            for sd, L in (("bull", pdh), ("bear", pdl)):
                if L and rv >= 1.2 and 0 < (L - price if sd == "bull" else price - L) <= L * 0.005:
                    watch.append({"symbol": sym, "side": sd, "price": price, "basis": f"{'PDH' if sd == 'bull' else 'PDL'} {L:.2f}",
                                  "status": "Near the level", "dist": abs(L - price) / L,
                                  "action": f"{'Buy' if sd == 'bull' else 'Sell'} only after a 15m close {'above' if sd == 'bull' else 'below'} {L:.2f} on 1.5× volume"})
        elif b and stage == 4:
            watch.append({"symbol": sym, "side": side, "price": price, "basis": b["plan"]["basis"],
                          "status": "Trend not with it", "dist": 1,
                          "action": "Broke out on volume, but VWAP/Supertrend disagree: wait"})
        elif b and b.get("extended"):
            watch.append({"symbol": sym, "side": side, "price": price, "basis": b["plan"]["basis"],
                          "status": "Extended", "dist": 2,
                          "action": f"Ran too far from {b['level_name']} {b['level']}: wait for a retest"})
    cands.sort(key=lambda c: -c["base"])
    watch.sort(key=lambda w: w["dist"])

    def finish(oi_by_symbol):
        for c in cands:
            oi = oi_by_symbol.get(c["symbol"])
            c["oi"] = oi
            want = "LONG BUILD-UP" if c["side"] == "bull" else "SHORT BUILD-UP"
            ok2 = "SHORT COVERING" if c["side"] == "bull" else "LONG UNWINDING"
            c["parts"] = dict(c["parts"], participation=c["parts"]["participation"] +
                              (8 if oi and oi["kind"] == want else 4 if oi is None or oi["kind"] == ok2 else 0))
            c["parts"] = {k: round(v, 1) for k, v in c["parts"].items()}
            c["score"] = round(sum(c["parts"].values()), 1)
        ranked = sorted(cands, key=lambda c: -c["score"])
        top = ranked[:n_picks] if picks_open else []
        for c in top:
            flow[c["symbol"]]["stage"] = 6
        counts = [sum(1 for f in flow.values() if f["stage"] >= i) for i in range(len(STAGES))]
        return {"funnel": [{"name": n, "count": counts[i]} for i, n in enumerate(STAGES)],
                "flow": sorted(flow.values(), key=lambda f: -f["pct"]), "sectors": sectors,
                "picks": top, "watch": watch[:8], "picks_open": picks_open, "kind": "breakout"}

    return cands, finish
