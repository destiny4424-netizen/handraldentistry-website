"""Bank Nifty option buying: a swing setup on the daily chart, traded with the monthly option.

Bank Nifty has one expiry a month, so an option bought 3+ weeks before expiry can be held for days
without the fast decay of the last week. Option buyers lose to two things: time decay and paying too
much for volatility. They win on quick, strong moves. So the setup only buys when a move is likely
to come soon and options are not expensive:

  1. Direction (daily chart)  the trend: higher highs/lows or a fresh BOS/CHoCH, the 20-day EMA on the
                              right side of the 50-day.
  2. One of two triggers      a) Pullback: price came back into a Fair Value Gap (FVG, inverse FVG, BPR or
                                 gap) with the trend, and printed a price-action candle there (hammer,
                                 engulfing, strong close).
                              b) Squeeze: an inside day or the narrowest day of the last 7 (NR7) while the
                                 Bollinger bands are at their tightest in months. A move out of a squeeze
                                 tends to be fast, which is what an option buyer needs.
  3. Volatility               India VIX at 22 or lower (cheap enough to buy), and better when VIX is low for
                              its own last 4 months and the option's IV is not far above Bank Nifty's own
                              20-day movement (HV).
  4. Option chain             put/call OI ratio (PCR), today's change in OI (who is writing), and the call
                              wall (the strike with the most call OI, where call writers defend) must leave
                              room up to the target (put wall for puts).
  5. Futures OI (5 sessions)  long build-up / short covering for calls; short build-up rules calls out.
  6. The big banks            HDFC Bank and ICICI Bank (counted 3×), SBI, Kotak and Axis: most of them on the
                              same side of their 20-day EMA.

The option: the monthly expiry with more than 15 days left (otherwise next month's), at the money or one
strike in the money (delta near 0.6). Entry when Bank Nifty breaks the trigger day's high (low for puts)
within 2 sessions, not before the 9:30 candle closes (the first 15 minutes have the widest option spreads);
if Bank Nifty opens more than half the stop distance past the entry, the trade is skipped (the reward-to-risk
is gone); stop, T1 and T2 are Bank Nifty levels; half is booked at T1 and the stop moves to cost;
anything still open after 6 sessions is closed, because time decay grows. If the stop would cost more than
40% of the premium, the option is too far from a sensible stop: no trade.
"""
import math

import options
import swing
from analysis import _clamp, _day, atr

RULES = {"setups": "both", "sides": "both", "vix_max": 22.0, "score_min": 55}
TRIGGER_DAYS, HOLD_DAYS = 2, 6
MIN_DAYS_LEFT = 15                                     # roll to next month's expiry inside this
STEP = 100                                             # Bank Nifty strike step
MAX_PREMIUM_LOSS = 0.40
HEAVY = {"HDFCBANK": 3, "ICICIBANK": 3, "SBIN": 1, "KOTAKBANK": 1, "AXISBANK": 1}
VARIANTS = [("Current rules", {}), ("Pullbacks only", {"setups": "pullback"}), ("Squeezes only", {"setups": "squeeze"}),
            ("Calls only", {"sides": "bull"}), ("VIX 16 or lower", {"vix_max": 16.0}), ("Score 65+", {"score_min": 65})]


def rules_of(r):
    return dict(RULES, **(r or {}))


def hv(d, n=20):
    """Bank Nifty's own movement: annualised % stdev of the last n daily returns."""
    c = [x["c"] for x in d[-(n + 1):]]
    if len(c) < n + 1:
        return None
    r = [math.log(b / a) for a, b in zip(c, c[1:])]
    m = sum(r) / len(r)
    return round(math.sqrt(sum((x - m) ** 2 for x in r) / (len(r) - 1)) * math.sqrt(252) * 100, 1)


def bandwidths(d, n=20):
    out = []
    for i in range(n - 1, len(d)):
        w = [x["c"] for x in d[i - n + 1:i + 1]]
        m = sum(w) / n
        sd = math.sqrt(sum((x - m) ** 2 for x in w) / n)
        out.append(4 * sd / m)
    return out


def vix_on(vix_d, day):
    """VIX close on (or just before) an IST day number, and its percentile over the 80 sessions before."""
    if not vix_d:
        return None, None
    upto = [x for x in vix_d if _day(x["t"]) <= day]
    if not upto:
        return None, None
    v = upto[-1]["c"]
    win = [x["c"] for x in upto[-81:-1]]
    pctl = round(100 * sum(1 for x in win if x < v) / len(win)) if len(win) >= 20 else None
    return round(v, 2), pctl


def _targets(st, zs, side, entry, risk):
    sign = 1 if side == "bull" else -1
    hi, lo = st["hi"], st["lo"]
    ahead = sorted({round(p, 2) for _, p in (hi if side == "bull" else lo)[-6:] if (p - entry) * sign >= 1.5 * risk} |
                   {(o["bottom"] if side == "bull" else o["top"]) for o in zs if o["side"] != side
                    and ((o["bottom"] if side == "bull" else o["top"]) - entry) * sign >= 1.5 * risk},
                   key=lambda v: (v - entry) * sign)
    t1 = ahead[0] if ahead and (ahead[0] - entry) * sign <= 3 * risk else entry + sign * 2 * risk
    t2 = next((v for v in ahead if (v - t1) * sign > 0.5 * risk and (v - entry) * sign <= 5 * risk), entry + sign * 3 * risk)
    if (t2 - t1) * sign <= 0:
        t2 = t1 + sign * risk
    return t1, t2


def candidates(d, vix_d=None, rules=None):
    """Today's Bank Nifty swing setups from the daily chart alone (d = daily candles, today last).
    Each: side, setup, signal, plan, base score parts. Used live and in the backtest."""
    rules = rules_of(rules)
    if len(d) < 70:
        return []
    a = atr(d)[-1] or d[-1]["c"] * 0.012
    today, prev = d[-1], d[-2]
    price = today["c"]
    closes = [c["c"] for c in d]
    e20, e50 = swing.ema(closes, 20), swing.ema(closes, 50)
    st = swing.structure(d)
    zs = swing.zones(d[:-1], a)
    vix, vix_pct = vix_on(vix_d, _day(today["t"]))
    if vix is not None and vix > rules["vix_max"]:
        return []
    bw = bandwidths(d)
    bw_pct = round(100 * sum(1 for x in bw[-121:-1] if x < bw[-1]) / len(bw[-121:-1])) if len(bw) > 40 else 50
    inside = today["h"] <= prev["h"] and today["l"] >= prev["l"]
    rng = today["h"] - today["l"]
    nr7 = rng <= min(c["h"] - c["l"] for c in d[-7:])
    hl = swing.pivots(d)
    swing_hi = max((p for _, p in hl[0][-3:]), default=max(c["h"] for c in d[-40:]))
    swing_lo = min((p for _, p in hl[1][-3:]), default=min(c["l"] for c in d[-40:]))
    mid = (swing_hi + swing_lo) / 2
    out = []
    for side in ("bull", "bear"):
        if rules["sides"] not in ("both", side):
            continue
        sign = 1 if side == "bull" else -1
        ev = st["event"] or ""
        fresh_event = ev.endswith("up" if side == "bull" else "down")
        trend_ok = st["trend"] == ("up" if side == "bull" else "down") or fresh_event
        ema_ok = (e20[-1] - e50[-1]) * sign > 0 and (price - e50[-1]) * sign > 0
        if not trend_ok or not ema_ok:
            continue
        trend_pts = (8 if st["trend"] == ("up" if side == "bull" else "down") else 3) + (5 if fresh_event else 0) \
            + 4 + (3 if (e20[-1] - e20[-6]) * sign > 0 else 0)                          # /20
        vol_pts = (0 if vix is None else 8 if vix <= 14 else 6 if vix <= 18 else 3) \
            + (4 if vix_pct is not None and vix_pct <= 50 else 0)                        # /12 (+3 from IV vs HV live)
        found = []
        # a) Pullback into a Fair Value Gap zone with a price-action candle.
        sig = swing.candle_signal(today, prev, side)
        if rules["setups"] in ("both", "pullback") and sig:
            for z in zs:
                if z["side"] != side:
                    continue
                tapped = (min(today["l"], prev["l"]) <= z["top"]) if side == "bull" else (max(today["h"], prev["h"]) >= z["bottom"])
                held = (price >= z["ce"] and today["l"] >= z["bottom"] - 0.25 * a) if side == "bull" else \
                       (price <= z["ce"] and today["h"] <= z["top"] + 0.25 * a)
                if not (tapped and held):
                    continue
                entry = today["h"] if side == "bull" else today["l"]
                ref = min(z["bottom"], today["l"]) if side == "bull" else max(z["top"], today["h"])
                sl = ref - sign * 0.2 * a
                discount = (z["ce"] <= mid) if side == "bull" else (z["ce"] >= mid)
                setup_pts = min(20, {"fresh": 10, "partial": 7, "inverted": 8}.get(z["state"], 7)
                                + (5 if z["type"] in ("BPR", "IFVG") else 3 if z["type"] == "Gap" else 0) + (5 if discount else 0))
                pa = {"Hammer / pin bar": 10, "Shooting star / pin bar": 10, "Bullish engulfing": 9, "Bearish engulfing": 9}.get(sig, 7)
                ztxt = f'{"Bullish" if side == "bull" else "Bearish"} {z["type"]} {z["bottom"]:.0f}–{z["top"]:.0f}'
                found.append({"setup": "pullback", "signal": sig, "zone": z, "zone_text": ztxt, "discount": discount,
                              "entry": entry, "sl": sl, "setup_pts": setup_pts, "pa": pa})
        # b) Squeeze: inside day / NR7 with the bands at their tightest.
        if rules["setups"] in ("both", "squeeze") and (inside or nr7) and bw_pct <= 25:
            pos = (price - today["l"]) / (rng or 1e-9)
            if (pos if side == "bull" else 1 - pos) >= 0.5:
                entry = today["h"] if side == "bull" else today["l"]
                sl = (min(today["l"], entry - 0.7 * a) if side == "bull" else max(today["h"], entry + 0.7 * a)) - sign * 0.1 * a
                setup_pts = (8 if inside else 0) + (6 if nr7 else 0) + (6 if bw_pct <= 10 else 3)
                pa = 10 if (pos if side == "bull" else 1 - pos) >= 0.7 else 6
                name = " + ".join(x for x, y in (("Inside day", inside), ("NR7", nr7)) if y)
                found.append({"setup": "squeeze", "signal": f"{name} · bands at {bw_pct}th percentile", "zone": None,
                              "zone_text": f"Squeeze range {today['l']:.0f}–{today['h']:.0f}", "discount": None,
                              "entry": entry, "sl": sl, "setup_pts": setup_pts, "pa": pa})
        for f in found:
            risk = abs(f["entry"] - f["sl"])
            if risk <= 0 or risk > 1.6 * a:
                continue                               # too wide a stop for an option buy
            t1, t2 = _targets(st, zs, side, f["entry"], risk)
            parts = {"trend": trend_pts, "setup": f["setup_pts"], "price_action": f["pa"], "volatility": vol_pts}
            word = "Buy CE above" if side == "bull" else "Buy PE below"
            out.append({"side": side, "setup": f["setup"], "signal": f["signal"], "zone": f["zone"],
                        "zone_text": f["zone_text"], "discount": f["discount"], "trend": st["trend"], "event": st["event"],
                        "parts": parts, "atr": round(a, 1), "ema20": round(e20[-1], 1), "ema50": round(e50[-1], 1),
                        "vix": vix, "vix_pct": vix_pct, "bw_pct": bw_pct,
                        "plan": {"basis": "Bank Nifty · " + ("FVG pullback" if f["setup"] == "pullback" else "Squeeze breakout"),
                                 "entry": round(f["entry"], 1), "sl": round(f["sl"], 1), "t1": round(t1, 1), "t2": round(t2, 1),
                                 "rr": round(abs(t1 - f["entry"]) / risk, 2), "rr2": round(abs(t2 - f["entry"]) / risk, 2),
                                 "risk_pts": round(risk, 1), "risk_pct": round(risk / f["entry"] * 100, 2),
                                 "action": f"{word} {f['entry']:.0f} (Bank Nifty) within {TRIGGER_DAYS} sessions, after the 9:30 "
                                           f"candle (skip if it opens {0.5 * risk:.0f}+ pts past the entry) · "
                                           f"stop {f['sl']:.0f} · T1 {t1:.0f} (book half, stop to cost) · T2 {t2:.0f} · "
                                           f"exit after {HOLD_DAYS} sessions"}})
    return out


# ── Live data: option chain, banks, futures OI ─────────────────────────────────────────────
def chain_view(chain, spot):
    """PCR, OI change, call/put walls, max pain and ATM IV from Dhan's option chain."""
    oc = (chain or {}).get("oc") or {}
    rows = []
    for k, row in oc.items():
        try:
            k = float(k)
        except ValueError:
            continue
        ce, pe = (row or {}).get("ce") or {}, (row or {}).get("pe") or {}
        rows.append({"k": k, "ce": ce.get("oi") or 0, "pe": pe.get("oi") or 0,
                     "ce0": ce.get("previous_oi"), "pe0": pe.get("previous_oi"),
                     "ce_iv": ce.get("implied_volatility") or 0, "pe_iv": pe.get("implied_volatility") or 0})
    if not rows or not spot:
        return None
    rows.sort(key=lambda r: r["k"])
    ce_t, pe_t = sum(r["ce"] for r in rows), sum(r["pe"] for r in rows)
    near = [r for r in rows if abs(r["k"] - spot) <= spot * 0.08]
    above = [r for r in near if r["k"] >= spot]
    below = [r for r in near if r["k"] <= spot]
    call_wall = max(above, key=lambda r: r["ce"])["k"] if above else None
    put_wall = max(below, key=lambda r: r["pe"])["k"] if below else None
    have0 = all(r["ce0"] is not None and r["pe0"] is not None for r in near) and near
    d_ce = sum(r["ce"] - r["ce0"] for r in near) if have0 else None
    d_pe = sum(r["pe"] - r["pe0"] for r in near) if have0 else None
    atm = min(rows, key=lambda r: abs(r["k"] - spot))
    ivs = [x for x in (atm["ce_iv"], atm["pe_iv"]) if x]
    pain = min(rows, key=lambda p: sum(r["ce"] * max(0, p["k"] - r["k"]) + r["pe"] * max(0, r["k"] - p["k"]) for r in rows))["k"]
    return {"pcr": round(pe_t / ce_t, 2) if ce_t else None, "call_wall": call_wall, "put_wall": put_wall,
            "d_ce": d_ce, "d_pe": d_pe, "max_pain": pain, "atm": atm["k"],
            "atm_iv": round(sum(ivs) / len(ivs), 1) if ivs else None}


def banks_view(heavy_daily):
    """Each big bank: above or below its 20-day EMA, and today's %."""
    out = []
    for s, w in HEAVY.items():
        d = heavy_daily.get(s)
        if not d or len(d) < 25:
            continue
        e = swing.ema([x["c"] for x in d], 20)[-1]
        out.append({"symbol": s, "weight": w, "above": d[-1]["c"] > e,
                    "pct": round((d[-1]["c"] - d[-2]["c"]) / d[-2]["c"] * 100, 2)})
    return out


def score_live(c, chain_v, fut_oi, banks, iv_hv):
    """Add the option chain, futures OI, the big banks and IV vs HV to a candidate. None if one rules it out."""
    side, p = c["side"], c["plan"]
    bull = side == "bull"
    checks, parts = [], dict(c["parts"])
    # Futures OI over 5 sessions.
    want, ok2 = ("LONG BUILD-UP", "SHORT COVERING") if bull else ("SHORT BUILD-UP", "LONG UNWINDING")
    if fut_oi and fut_oi["kind"] == ("SHORT BUILD-UP" if bull else "LONG BUILD-UP"):
        return None, f"Futures show {fut_oi['kind'].lower()} over 5 sessions, against a {'call' if bull else 'put'}"
    parts["futures_oi"] = 10 if fut_oi and fut_oi["kind"] == want else 6 if fut_oi and fut_oi["kind"] == ok2 else 4
    # The big banks.
    if banks:
        tot = sum(b["weight"] for b in banks)
        share = sum(b["weight"] for b in banks if b["above"] == bull) / tot
        if share < 0.4:
            return None, f"Most of the big banks are {'below' if bull else 'above'} their 20-day EMA"
        parts["banks"] = round(10 * share, 1)
    else:
        parts["banks"] = 5
    # Option chain: PCR, who is writing, and room to the wall.
    oc_pts = 5
    if chain_v:
        pcr = chain_v["pcr"] or 1
        oc_pts = (5 if (pcr >= 1.0 if bull else pcr <= 0.8) else 2 if 0.8 < pcr < 1.0 else 0)
        if chain_v["d_ce"] is not None:
            writers = (chain_v["d_pe"] > chain_v["d_ce"]) if bull else (chain_v["d_ce"] > chain_v["d_pe"])
            oc_pts += 5 if writers else 0
        else:
            oc_pts += 2
        wall = chain_v["call_wall"] if bull else chain_v["put_wall"]
        risk = p["risk_pts"]
        if wall is not None and (wall - p["entry"]) * (1 if bull else -1) < risk:
            return None, f"{'Call' if bull else 'Put'} writers' wall at {wall:.0f}, right next to the entry"
        if wall is not None and (wall - p["t1"]) * (1 if bull else -1) < 0:
            if abs(wall - p["entry"]) >= 1.5 * risk:
                p["t1"] = round(wall - (1 if bull else -1) * 0.1 * risk, 1)   # T1 just before the wall
                p["rr"] = round(abs(p["t1"] - p["entry"]) / risk, 2)
                checks.append({"ok": None, "text": f"T1 moved to just before the {'call' if bull else 'put'} wall at {wall:.0f}"})
            else:
                oc_pts = max(0, oc_pts - 4)
        else:
            oc_pts += 5
    parts["option_chain"] = min(15, oc_pts)
    parts["volatility"] = parts["volatility"] + (3 if iv_hv is not None and iv_hv <= 1.1 else 0)
    c = dict(c, parts={k: round(v, 1) for k, v in parts.items()}, plan=p)
    c["score"] = round(sum(c["parts"].values()), 1)
    c["checks_extra"] = checks
    return c, None


# ── The option ────────────────────────────────────────────────────────────────────────────
def option_for(chain, kind, plan, spot, lot, expiry, rolled, strikes):
    """The option to buy (delta near 0.6), its levels at Bank Nifty's levels, decay a day, and a spread."""
    near = options.strikes_near(strikes, plan["entry"], kind, n=2)
    got = [(k, from_chain(chain, kind, k)) for k in near]
    got = [(k, o) for k, o in got if o and options.usable(o)] or [(k, o) for k, o in got if o]
    if not got:
        return None
    k, o = min(got, key=lambda x: abs(abs(x[1]["delta"]) - 0.6))
    out = options.plan(kind, o, spot, plan, lot, expiry, rolled, k)
    if not out:
        return None
    out["theta"] = o.get("theta")
    out["loss_pct"] = round((out["entry"] - out["sl"]) / out["entry"] * 100)
    if o.get("theta") is not None and lot:
        out["decay_lot"] = round(abs(o["theta"]) * lot)
    # Lower-risk version: a debit spread, selling the strike at T2.
    sell_k = min(strikes, key=lambda s: abs(s - plan["t2"]))
    so = from_chain(chain, kind, sell_k)
    if so and (sell_k - k) * (1 if kind == "CE" else -1) > 0:
        pa = lambda opt, at: options.premium_at(opt, spot, at)
        e, sl = pa(o, plan["entry"]) - pa(so, plan["entry"]), pa(o, plan["sl"]) - pa(so, plan["sl"])
        t1 = pa(o, plan["t1"]) - pa(so, plan["t1"])
        if e > 0 and sl < e < t1:
            out["spread"] = {"buy": k, "sell": sell_k, "label": f"{options.fmt_strike(k)}/{options.fmt_strike(sell_k)} {kind}",
                             "debit": round(e, 2), "sl": round(max(sl, 0.05), 2), "t1": round(t1, 2),
                             "max": round(abs(sell_k - k), 2),
                             "risk_lot": round((e - max(sl, 0)) * lot) if lot else None,
                             "cost_lot": round(e * lot) if lot else None}
    return out


def from_chain(chain, kind, strike):
    o = options.from_chain(chain, kind, strike)
    if o:
        for kk, row in ((chain or {}).get("oc") or {}).items():
            try:
                if abs(float(kk) - strike) < 1e-6:
                    o["theta"] = ((row.get(kind.lower()) or {}).get("greeks") or {}).get("theta")
            except ValueError:
                pass
    return o


def demo_chain(spot, days, iv=14.0, seed=7):
    """Made-up Bank Nifty option chain (Black-Scholes greeks, bell-shaped OI) for demo mode."""
    import random
    rng = random.Random(seed)
    oc, atm = {}, round(spot / STEP) * STEP
    for i in range(-30, 31):
        k = atm + STEP * i
        ce_ltp, ce_d, g = options.bs(spot, k, days, iv, "CE")
        pe_ltp, pe_d, _ = options.bs(spot, k, days, iv, "PE")
        base = 2e5 * math.exp(-((k - atm) / 1500) ** 2) * (2.2 if i % 10 == 0 else 1)
        th_c, th_p = -ce_ltp / max(days, 1) / 2.2, -pe_ltp / max(days, 1) / 2.2
        ce_oi, pe_oi = int(base * rng.uniform(0.6, 1.6) * (1.5 if i > 0 else 1)), int(base * rng.uniform(0.6, 1.6) * (1.5 if i < 0 else 1))
        oc[f"{k:.6f}"] = {
            "ce": {"oi": ce_oi, "previous_oi": int(ce_oi * rng.uniform(0.9, 1.05)), "implied_volatility": iv, "last_price": ce_ltp,
                   "volume": 50000, "top_bid_price": ce_ltp * 0.998, "top_ask_price": ce_ltp * 1.002,
                   "greeks": {"gamma": g, "delta": ce_d, "theta": th_c}},
            "pe": {"oi": pe_oi, "previous_oi": int(pe_oi * rng.uniform(0.9, 1.05)), "implied_volatility": iv, "last_price": pe_ltp,
                   "volume": 50000, "top_bid_price": pe_ltp * 0.998, "top_ask_price": pe_ltp * 1.002,
                   "greeks": {"gamma": g, "delta": pe_d, "theta": th_p}}}
    return {"oc": oc, "last_price": spot}


def analyse(d, vix_d, heavy_daily, fut_oi, chain, expiry, rolled, strikes, lot, rules=None, days_left=None):
    """Today's Bank Nifty view: the trade (if any), and every check, for the Swing tab."""
    rules = rules_of(rules)
    if len(d) < 70:
        return {"verdict": "NO DATA", "reason": "Not enough Bank Nifty daily history yet."}
    today, prev = d[-1], d[-2]
    spot = float((chain or {}).get("last_price") or today["c"])
    cv = chain_view(chain, spot) if chain else None
    h = hv(d)
    iv = cv["atm_iv"] if cv else None
    iv_hv = round(iv / h, 2) if iv and h else None
    banks = banks_view(heavy_daily)
    st = swing.structure(d)
    closes = [c["c"] for c in d]
    e20, e50 = swing.ema(closes, 20)[-1], swing.ema(closes, 50)[-1]
    vix, vix_pct = vix_on(vix_d, _day(today["t"]))
    view = {"price": round(today["c"], 1), "pct": round((today["c"] - prev["c"]) / prev["c"] * 100, 2),
            "trend": st["trend"], "event": st["event"], "ema20": round(e20, 1), "ema50": round(e50, 1),
            "vix": vix, "vix_pct": vix_pct, "hv": h, "iv": iv, "iv_hv": iv_hv, "chain": cv, "fut_oi": fut_oi,
            "banks": banks, "expiry": expiry, "rolled": rolled, "days_left": days_left, "lot": lot,
            "bias": "bull" if today["c"] > e50 and e20 > e50 else "bear" if today["c"] < e50 and e20 < e50 else "neutral"}
    if vix is not None and vix > rules["vix_max"]:
        return dict(view, verdict="NO TRADE", reason=f"India VIX {vix} is above {rules['vix_max']:g}: options are expensive to buy.")
    cands = candidates(d, vix_d, rules)
    if not cands:
        why = ("No pullback into a Fair Value Gap with a price-action candle, and no squeeze, in the direction of the trend."
               if view["bias"] != "neutral" else "Bank Nifty has no clear trend (20 and 50-day EMAs disagree): option buyers wait.")
        return dict(view, verdict="NO TRADE", reason=why)
    best, reasons = None, []
    for c in cands:
        c2, why = score_live(c, cv, fut_oi, banks, iv_hv)
        if not c2:
            reasons.append(why)
            continue
        if c2["score"] < rules["score_min"]:
            reasons.append(f"{c2['plan']['basis']} scores {c2['score']:.0f}, below {rules['score_min']}")
            continue
        if best is None or c2["score"] > best["score"]:
            best = c2
    if not best:
        return dict(view, verdict="NO TRADE", reason="; ".join(reasons) or "No setup passed the checks.", watch=cands[0]["plan"])
    kind = "CE" if best["side"] == "bull" else "PE"
    opt = option_for(chain, kind, best["plan"], spot, lot, expiry, rolled, strikes) if chain and strikes else None
    pick = dict(best, symbol="BANKNIFTY", price=round(today["c"], 1), pct=view["pct"], option=opt, sector="Bank Nifty")
    if opt and opt["loss_pct"] > MAX_PREMIUM_LOSS * 100:
        return dict(view, verdict="NO TRADE", watch=best["plan"],
                    reason=f"The stop would cost {opt['loss_pct']}% of the option's premium (more than {MAX_PREMIUM_LOSS:.0%}): "
                           "too far for an option buy. Wait for a tighter setup.")
    return dict(view, verdict=f"BUY {kind}", reason="", pick=pick)


# ── Backtest (Bank Nifty daily + India VIX daily; no option chain / OI history) ────────────
def theta_r(spot, vix, days_left, risk_pts, delta=0.6):
    """Rough time decay a session, in R, of an ATM-ish monthly option."""
    sig = (vix or 15) / 100
    prem = 0.4 * spot * sig * math.sqrt(max(days_left, 1) / 365)
    return (prem / (2 * max(days_left, 1))) * 7 / 5 / (delta * risk_pts)


def backtest(d, vix_d, rules=None, book_cls=None, start=70):
    """Replay the setup day by day: one Bank Nifty trade at a time. Results in Bank Nifty R and an
    estimate of the option's R (after time decay and ~0.03R costs)."""
    book = book_cls()
    for i in range(start, len(d) - 1):
        if any(r.get("exit_at") is None for r in book.rows):
            book.update({"BANKNIFTY": d[:i + 1]}, d[i]["t"] + 86400)
            if any(r.get("exit_at") is None for r in book.rows):
                continue
        cs = candidates(d[:i + 1], vix_d, rules)
        r = rules_of(rules)
        cs = [c for c in cs if sum(c["parts"].values()) + 19 >= r["score_min"]]   # +19: average of the live-only parts
        if not cs:
            continue
        c = max(cs, key=lambda c: sum(c["parts"].values()))
        pick = dict(c, symbol="BANKNIFTY", price=d[i]["c"], score=round(sum(c["parts"].values()) + 19, 1))
        book.record(pick, d[i]["t"] + 15 * 3600)
        book.rows[-1]["vix"] = c["vix"]
    book.update({"BANKNIFTY": d}, d[-1]["t"] + 86400)
    rows = book.rows
    for r in rows:
        if r["result_r"] is None or not r.get("triggered_at"):
            r["opt_r"] = None
            continue
        risk = abs(r["entry"] - r["sl"])
        r["opt_r"] = round(r["result_r"] - theta_r(r["entry"], r.get("vix"), 22, risk) * max(1, r.get("held") or 1) - 0.03, 2)
    done = [r for r in rows if r.get("opt_r") is not None]
    half = len(done) // 2

    def st(xs, key):
        return {"n": len(xs), "win_rate": round(100 * sum(1 for x in xs if x[key] > 0) / len(xs)) if xs else None,
                "avg_r": round(sum(x[key] for x in xs) / len(xs), 2) if xs else None,
                "total_r": round(sum(x[key] for x in xs), 2)}
    return {"index": st(done, "result_r"), "option": st(done, "opt_r"), "early": st(done[:half], "opt_r"),
            "late": st(done[half:], "opt_r"), "sessions": len(d) - start,
            "trades": [{k: r.get(k) for k in ("day", "side", "basis", "entry", "sl", "t1", "t2", "status", "result_r", "opt_r")}
                       for r in done[-12:]]}


def compare(d, vix_d, book_cls):
    """Every variant; the ⭐ one has the best option R in total with both halves positive (10+ trades)."""
    out = []
    for name, rules in VARIANTS:
        b = backtest(d, vix_d, rules, book_cls)
        out.append({"name": name, "rules": rules, **{k: b[k] for k in ("index", "option", "early", "late")}})
    ok = [v for v in out if v["option"]["n"] >= 10 and (v["early"]["avg_r"] or 0) > 0 and (v["late"]["avg_r"] or 0) > 0]
    if ok:
        max(ok, key=lambda v: v["option"]["total_r"])["pick"] = True
    return out
