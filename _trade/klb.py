"""HTF Key Level Breakout — the owner's Pine Script indicator, server copy (the chart runs static/htfklb.js; the two
are kept identical by tests/test_klb.py, which runs both on the same candles and compares every trade).

compute(bars, tf_min, opts) -> trades, the open one, stats, and `crosses`: every candle that closed beyond the green
(HTF high) or red (HTF low) line it opened inside, with whether the script took it and, if not, why.
bars: closed candles [{"t" (epoch s), "o", "h", "l", "c"}], oldest first.
"""
import math

DEFAULTS = {"htf": "auto", "useLong": True, "useShort": True,
            "bufATR": 0.1, "dispMult": 0.8, "useRetest": False, "retestBars": 10,
            "stopATR": 1.5, "minStopATR": 1.0, "tgtATRMult": 1.0, "rrMin": 7.0, "rrMax": 9.0, "trailR": 0.0,
            "maxHold": 60, "cooldown": 3, "feePct": 0.0472}
AUTO_HTF = {1: "15", 5: "60", 15: "240", 60: "D", 1440: "W"}
SECONDS = {"15": 900, "60": 3600, "240": 14400, "D": 86400, "W": 604800}
SESSION = 9 * 3600 + 15 * 60
NUMS = ("bufATR", "dispMult", "retestBars", "stopATR", "minStopATR", "tgtATRMult", "rrMin", "rrMax", "trailR",
        "maxHold", "cooldown", "feePct")


def clean_opts(o):
    """Settings from the app: known keys only, numbers as numbers."""
    out = {}
    for k, v in (o or {}).items():
        if k in ("useLong", "useShort", "useRetest"):
            out[k] = bool(v)
        elif k == "htf" and str(v) in ("auto", "15", "60", "240", "D", "W"):
            out[k] = str(v)
        elif k in NUMS:
            try:
                f = float(v)
            except (TypeError, ValueError):
                continue
            if math.isfinite(f) and f >= 0:
                out[k] = f
    return out


def htf_for(tf_min, htf):
    return AUTO_HTF.get(tf_min, "D") if not htf or htf == "auto" else str(htf)


def bucket(t, htf):
    ist = t + 19800
    day = ist // 86400
    if htf == "W":
        return day - ((day + 3) % 7)
    if htf == "D":
        return day
    return day * 1000 + (ist % 86400 - SESSION) // (int(htf) * 60)


def rma(vals, n):
    out, s, prev = [None] * len(vals), 0.0, None
    for i, v in enumerate(vals):
        if prev is None:
            s += v
            if i == n - 1:
                prev = s / n
            else:
                continue
        else:
            prev = (v + (n - 1) * prev) / n
        out[i] = prev
    return out


def atr(bars, n=14):
    tr = [b["h"] - b["l"] if i == 0 else max(b["h"] - b["l"], abs(b["h"] - bars[i - 1]["c"]), abs(b["l"] - bars[i - 1]["c"]))
          for i, b in enumerate(bars)]
    return rma(tr, n)


def compute(bars, tf_min, opts=None):
    o = dict(DEFAULTS, **(opts or {}))
    htf = htf_for(tf_min, o["htf"])
    htf_ok = SECONDS.get(htf, 0) > tf_min * 60
    H, h_idx, last = [], [], None
    for b in bars:
        k = bucket(b["t"], htf)
        if k != last:
            H.append({"t": b["t"], "o": b["o"], "h": b["h"], "l": b["l"], "c": b["c"]})
            last = k
        else:
            x = H[-1]
            x["h"], x["l"], x["c"] = max(x["h"], b["h"]), min(x["l"], b["l"]), b["c"]
        h_idx.append(len(H) - 1)
    h_atr, A = atr(H), atr(bars)
    hhs, hls, has = [], [], []
    for i in range(len(bars)):
        p = h_idx[i] - 1
        hhs.append(H[p]["h"] if p >= 0 else None)
        hls.append(H[p]["l"] if p >= 0 else None)
        has.append(h_atr[p] if p >= 0 else None)

    last_high = last_low = None
    long_done = short_done = False
    t_dir, entry_p, cur_stop, tgt_p, risk_p, peak_p, entry_bar, cooldown_to = 0, None, None, None, None, None, None, 0
    cur, pend_dir, pend_bar = None, 0, None
    trades, crosses = [], []
    st = {"wins": 0, "losses": 0, "sumR": 0.0, "sumFeeR": 0.0, "sumRR": 0.0, "skipped": 0, "warmup": 0, "htfBars": len(H)}

    for i, b in enumerate(bars):
        a, hh, hl, ha = A[i], hhs[i], hls[i], has[i]
        if hh != last_high:
            long_done, last_high = False, hh
        if hl != last_low:
            short_done, last_low = False, hl
        in_trade = t_dir != 0
        fired = False
        decisive = o["dispMult"] == 0 or (a is not None and abs(b["c"] - b["o"]) > a * o["dispMult"])
        buf = (a or 0) * o["bufATR"]

        # every close beyond a line it opened inside (what the owner watches for), before the script's filters
        for side, lvl in ((1, hh), (-1, hl)):
            if lvl is not None and ((side == 1 and b["c"] > lvl >= b["o"]) or (side == -1 and b["c"] < lvl <= b["o"])):
                crosses.append({"i": i, "t": b["t"], "dir": side, "level": lvl, "close": b["c"], "why": None})

        hit_dir = 0
        if htf_ok and not in_trade and i >= cooldown_to and decisive and a is not None:
            if o["useLong"] and hh is not None and not long_done and b["c"] > hh + buf and b["o"] <= hh + buf:
                hit_dir = 1
            if o["useShort"] and hl is not None and not short_done and hit_dir == 0 and b["c"] < hl - buf and b["o"] >= hl - buf:
                hit_dir = -1
        if hit_dir != 0 and o["useRetest"] and not in_trade:
            pend_dir, pend_bar, hit_dir = hit_dir, i, 0
        if o["useRetest"] and pend_dir != 0 and not in_trade:
            lvl = hh if pend_dir == 1 else hl
            back = b["l"] <= lvl if pend_dir == 1 else b["h"] >= lvl
            if back:
                hit_dir, pend_dir = pend_dir, 0
            elif i - pend_bar > o["retestBars"]:
                pend_dir = 0

        why = None
        if hit_dir != 0 and not in_trade:
            p_entry = b["c"]
            p_stop = p_entry - hit_dir * a * o["stopATR"]
            p_risk = abs(p_entry - p_stop)
            p_tgt = p_entry + hit_dir * ((ha or 0) * o["tgtATRMult"])
            p_rr = abs(p_tgt - p_entry) / p_risk if p_risk > 0 else 0.0
            p_fee_r = (2 * o["feePct"] / 100 * p_entry) / p_risk if p_risk > 0 else 999.0
            too_tight = o["minStopATR"] > 0 and a > 0 and p_risk < a * o["minStopATR"]
            out_of_band = p_rr < o["rrMin"] or (o["rrMax"] > 0 and p_rr > o["rrMax"])
            if ha is None:
                st["warmup"] += 1
                why = "not enough higher-timeframe candles yet"
            elif p_risk <= 0 or too_tight or out_of_band:
                st["skipped"] += 1
                why = (f"stop tighter than {o['minStopATR']:g} × ATR" if too_tight else
                       f"R:R 1:{p_rr:.1f} outside {o['rrMin']:g}–{o['rrMax']:g}" if o["rrMax"] > 0 else f"R:R 1:{p_rr:.1f} under {o['rrMin']:g}")
            else:
                t_dir, entry_p, cur_stop, tgt_p, risk_p, peak_p, entry_bar = hit_dir, p_entry, p_stop, p_tgt, p_risk, p_entry, i
                st["sumRR"] += p_rr
                fired = True
                if hit_dir == 1:
                    long_done = True
                else:
                    short_done = True
                cur = {"dir": hit_dir, "i": i, "t": b["t"], "entry": p_entry, "stop0": p_stop, "stop": p_stop, "tgt": p_tgt,
                       "risk": p_risk, "rr": p_rr, "feeR": p_fee_r, "level": hh if hit_dir == 1 else hl}
                trades.append(cur)
        for c in crosses:
            if c["i"] == i and c["why"] is None:
                c["why"] = ("signal" if fired and c["dir"] == t_dir else why if hit_dir == c["dir"] and why else
                            "already in a trade" if in_trade else "cooling down after the last trade" if i < cooldown_to else
                            "this level already fired" if (long_done if c["dir"] == 1 else short_done) and not fired else
                            f"body under {o['dispMult']:g} × ATR" if not decisive else
                            f"not past the {o['bufATR']:g} × ATR buffer" if hit_dir == 0 else
                            "waiting for a retest" if o["useRetest"] else "not taken")

        if in_trade and not fired:
            peak_p = max(peak_p, b["h"]) if t_dir == 1 else min(peak_p, b["l"])
            achieved = ((peak_p - entry_p) if t_dir == 1 else (entry_p - peak_p)) / risk_p if risk_p > 0 else 0.0
            if o["trailR"] > 0 and achieved >= o["trailR"]:
                want = peak_p - t_dir * o["trailR"] * risk_p
                if (want > cur_stop) if t_dir == 1 else (want < cur_stop):
                    cur_stop = want
            cur["stop"] = cur_stop
            hit_stop = b["l"] <= cur_stop if t_dir == 1 else b["h"] >= cur_stop
            hit_tgt = b["h"] >= tgt_p if t_dir == 1 else b["l"] <= tgt_p
            stale = i - entry_bar > o["maxHold"]
            if hit_stop or hit_tgt or stale:
                exit_p = cur_stop if hit_stop else tgt_p if hit_tgt else b["c"]
                raw_r = ((exit_p - entry_p) if t_dir == 1 else (entry_p - exit_p)) / risk_p if risk_p > 0 else 0.0
                fee_r = (2 * o["feePct"] / 100 * entry_p) / risk_p if risk_p > 0 else 0.0
                r = raw_r - fee_r
                st["sumR"] += r
                st["sumFeeR"] += fee_r
                st["wins" if r > 0 else "losses"] += 1
                cur.update(exitI=i, exitT=b["t"], exitP=exit_p, r=r,
                           status=("TRAILED" if raw_r > 0 else "STOPPED") if hit_stop else "TARGET" if hit_tgt else "TIMED OUT")
                t_dir, cooldown_to = 0, i + int(o["cooldown"])
                entry_p = cur_stop = tgt_p = risk_p = None
                cur = None
    return {"htf": htf, "htfOK": htf_ok, "trades": trades, "open": cur, "stats": st, "crosses": crosses, "opts": o}
