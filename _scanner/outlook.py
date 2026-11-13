"""Outlook: how the broad market (NIFTY) is likely to behave tomorrow and next week.

Nobody can know this. What can be measured is how NIFTY behaved after days (and weeks) that looked
like today: the "analogue" method. For the last close, the model finds the most similar past days in
NIFTY's own history - by today's move (in ATRs), where it closed in its range, how wide the range was,
the last 5 days' move, the trend (above/below the 20 and 50-day averages) and the streak of up/down
days - and counts what happened next:

  - direction: up / down / flat (a move of less than 0.3%)
  - type: trend continuation (moves on in today's direction), reversal (turns against it), or sideways
  - opening: gap up / gap down (more than 0.25% from the close) / flat
  - the usual size of the move and of the day's range

The flows the scanner already reads then nudge the direction a little: the market mood score, FII
cash, FII index-futures positioning and VIX (each nudge is small and shown). Gaps depend mostly on
overnight global markets (US, Asia, GIFT Nifty), which the scanner can't read, so the gap odds are the
weakest part.

The same is done on weekly bars for next week, with the scheduled events of the week.

Honesty checks: the model is walk-forward tested on the last ~250 days (each forecast only uses data
before it), and every live forecast is logged and graded the next day, so the hit rate is always shown
next to what guessing would get.
"""
import math

FLAT_DAY, GAP = 0.30, 0.25                             # % thresholds
FLAT_WEEK = 0.75


def atr_list(d, n=14):
    out, a = [], None
    for i, c in enumerate(d):
        tr = c["h"] - c["l"] if i == 0 else max(c["h"] - c["l"], abs(c["h"] - d[i - 1]["c"]), abs(c["l"] - d[i - 1]["c"]))
        a = tr if a is None else a + (tr - a) / n
        out.append(a)
    return out


def sma_at(d, i, n):
    return sum(x["c"] for x in d[i - n + 1:i + 1]) / n if i >= n - 1 else None


def feats(d, i, atr):
    c, p = d[i], d[i - 1]
    rng = (c["h"] - c["l"]) or 1e-9
    a = atr[i - 1] or rng
    streak, j = 0, i
    sgn = 1 if c["c"] >= p["c"] else -1
    while j > 0 and (1 if d[j]["c"] >= d[j - 1]["c"] else -1) == sgn and streak < 6:
        streak += 1
        j -= 1
    s20, s50 = sma_at(d, i, 20), sma_at(d, i, 50)
    return {"ret_atr": (c["c"] - p["c"]) / a, "pos": (c["c"] - c["l"]) / rng, "rng": rng / a,
            "r5": (c["c"] - d[i - 5]["c"]) / (a * 2.2) if i >= 5 else 0,
            "t20": 1 if s20 and c["c"] > s20 else -1, "t50": 1 if s50 and c["c"] > s50 else -1,
            "streak": sgn * streak, "ret": (c["c"] / p["c"] - 1) * 100}


W = {"ret_atr": 1.0, "pos": 1.2, "rng": 0.6, "r5": 0.8, "t20": 0.5, "t50": 0.4, "streak": 0.15}


def dist(a, b):
    return math.sqrt(sum(w * (a[k] - b[k]) ** 2 for k, w in W.items()))


def outcomes(d, j, flat):
    """What happened after bar j: move %, gap %, range in ATRs."""
    c, n = d[j], d[j + 1]
    return {"move": (n["c"] / c["c"] - 1) * 100, "gap": (n["o"] / c["c"] - 1) * 100,
            "range": n["h"] - n["l"], "dir": 1 if n["c"] > c["c"] else -1}


def classify(today_ret, move, flat):
    if abs(move) < flat:
        return "Sideways"
    if abs(today_ret) < flat / 2:                      # a flat day: "continuation" = with the 5-day move (set by caller)
        return None
    return "Continuation" if (move > 0) == (today_ret > 0) else "Reversal"


def analogue(d, i, k=60, flat=FLAT_DAY, start=60):
    """Probabilities for bar i+1 from the k most similar bars before i (only data up to i is used)."""
    if i < start + k:
        return None
    atr = atr_list(d[:i + 1])
    f = feats(d, i, atr)
    cands = []
    for j in range(start, i - 1):                      # j+1 must also be known before i
        cands.append((dist(f, feats(d, j, atr)), j))
    cands.sort()
    near = [j for _, j in cands[:k]]
    up = dn = fl = cont = rev = side = gu = gd = gf = 0
    moves, ranges = [], []
    ref = f["ret"] if abs(f["ret"]) >= flat / 2 else (1 if f["r5"] > 0 else -1)
    for j in near:
        o = outcomes(d, j, flat)
        moves.append(abs(o["move"]))
        ranges.append(o["range"] / (atr[j] or 1))
        if abs(o["move"]) < flat:
            fl += 1
            side += 1
        elif o["move"] > 0:
            up += 1
        else:
            dn += 1
        if abs(o["move"]) >= flat:
            if (o["move"] > 0) == (ref > 0):
                cont += 1
            else:
                rev += 1
        if o["gap"] > GAP:
            gu += 1
        elif o["gap"] < -GAP:
            gd += 1
        else:
            gf += 1
    n = len(near)
    pr = lambda x: round(100 * (x + 1) / (n + 3))      # smoothed
    moves.sort()
    ranges.sort()
    return {"n": n, "up": pr(up), "down": pr(dn), "flat": pr(fl),
            "continuation": pr(cont), "reversal": pr(rev), "sideways": pr(side),
            "gap_up": pr(gu), "gap_down": pr(gd), "gap_flat": pr(gf),
            "move_med": round(moves[n // 2], 2), "range_atr": round(ranges[n // 2], 2),
            "ref_dir": "up" if ref > 0 else "down", "features": {k2: round(v, 2) for k2, v in f.items()}}


def _norm3(a, b, c):
    t = a + b + c
    return round(100 * a / t), round(100 * b / t), 100 - round(100 * a / t) - round(100 * b / t)


def nudge(p, mood=None, fii_today=None, fii_long_change=None, vix_change=None):
    """Small, shown adjustments of up/down odds from the flows (each at most a few points)."""
    notes, shift = [], 0.0
    if mood is not None:
        s = 6 * mood / 100
        shift += s
        notes.append(f"market mood {mood:+d}: {s:+.1f} pts")
    if fii_today is not None:
        s = 3 * math.tanh(fii_today / 3000)
        shift += s
        notes.append(f"FII cash {fii_today:+,.0f} cr: {s:+.1f} pts")
    if fii_long_change is not None:
        s = 3 * math.tanh(fii_long_change / 5)
        shift += s
        notes.append(f"FII futures long % {fii_long_change:+.1f}: {s:+.1f} pts")
    if vix_change is not None:
        s = -2 * math.tanh(vix_change / 6)
        shift += s
        notes.append(f"VIX {vix_change:+.1f}%: {s:+.1f} pts")
    up, dn = max(1.0, p["up"] + shift), max(1.0, p["down"] - shift)
    u, d_, f = _norm3(up, dn, p["flat"])
    q = dict(p, up=u, down=d_, flat=f)
    # continuation / reversal follow the direction shift relative to today's direction
    cs = shift if p["ref_dir"] == "up" else -shift
    c2, r2, s2 = _norm3(max(1.0, p["continuation"] + cs), max(1.0, p["reversal"] - cs), p["sideways"])
    q.update(continuation=c2, reversal=r2, sideways=s2, nudges=notes, shift=round(shift, 1))
    return q


def verdicts(p):
    """The most likely of each set, and how sure (the gap between the top two)."""
    def top(keys, names):
        vals = sorted(((p[k], names[k]) for k in keys), reverse=True)
        edge = vals[0][0] - vals[1][0]
        return {"pick": vals[0][1], "p": vals[0][0], "conf": "clear" if edge >= 15 else "slight" if edge >= 6 else "toss-up"}
    return {"direction": top(("up", "down", "flat"), {"up": "Up", "down": "Down", "flat": "Flat"}),
            "type": top(("continuation", "reversal", "sideways"), {"continuation": "Trend continuation", "reversal": "Reversal", "sideways": "Sideways"}),
            "open": top(("gap_up", "gap_down", "gap_flat"), {"gap_up": "Gap up", "gap_down": "Gap down", "gap_flat": "Flat open"})}


def walk_forward(d, last=250, k=60, flat=FLAT_DAY, step=1, first=160, start=60):
    """How often the model's top pick was right, forecasting each of the last `last` bars with only the
    data before it - against always guessing the most common answer."""
    hits = {"direction": 0, "type": 0, "open": 0}
    base = {"direction": {}, "type": {}, "open": {}}
    n = 0
    for i in range(max(len(d) - last - 1, first), len(d) - 1, step):
        p = analogue(d, i, k, flat, start)
        if not p:
            continue
        v = verdicts(p)
        a = actual(d, i, flat, p["ref_dir"])
        n += 1
        for key in hits:
            hits[key] += v[key]["pick"] == a[key]
            base[key][a[key]] = base[key].get(a[key], 0) + 1
    if not n:
        return None
    return {"n": n, **{key: {"hit": round(100 * hits[key] / n), "guess": round(100 * max(base[key].values()) / n),
                             "guess_pick": max(base[key], key=base[key].get)} for key in hits}}


def actual(d, i, flat, ref_dir):
    """What bar i+1 actually did, in the same words as the forecast."""
    c, nx = d[i], d[i + 1]
    move = (nx["c"] / c["c"] - 1) * 100
    gap = (nx["o"] / c["c"] - 1) * 100
    direction = "Flat" if abs(move) < flat else "Up" if move > 0 else "Down"
    typ = "Sideways" if abs(move) < flat else "Trend continuation" if (move > 0) == (ref_dir == "up") else "Reversal"
    op = "Gap up" if gap > GAP else "Gap down" if gap < -GAP else "Flat open"
    return {"direction": direction, "type": typ, "open": op, "move": round(move, 2), "gap": round(gap, 2)}


def weekly(d):
    """Daily bars -> weekly bars (Monday-Friday weeks)."""
    from datetime import datetime, timedelta, timezone
    ist = timezone(timedelta(hours=5, minutes=30))
    out, key = [], None
    for c in d:
        dt = datetime.fromtimestamp(c["t"], ist).date()
        k = dt.isocalendar()[:2]
        if k != key:
            out.append(dict(c, v=c.get("v", 0)))
            key = k
        else:
            w = out[-1]
            w.update(h=max(w["h"], c["h"]), l=min(w["l"], c["l"]), c=c["c"], v=w.get("v", 0) + c.get("v", 0))
            w["t_end"] = c["t"]
    return out


def levels(d, atr_mult=1.0):
    """Tomorrow's / next week's reference levels from the last bar."""
    a = atr_list(d)[-1]
    c = d[-1]
    return {"close": round(c["c"], 2), "high": round(c["h"], 2), "low": round(c["l"], 2),
            "up1": round(c["c"] + atr_mult * a * 0.5, 2), "dn1": round(c["c"] - atr_mult * a * 0.5, 2), "atr": round(a, 2)}


def summary(v, p, when, pts):
    """One plain sentence."""
    d, t, o = v["direction"], v["type"], v["open"]
    lean = {"clear": "leans", "slight": "slightly leans", "toss-up": "is close to a coin toss, slight edge"}[d["conf"]]
    return (f"{when} {lean} {d['pick'].lower()} ({d['p']}%); most likely {t['pick'].lower()} ({t['p']}%)"
            + (f", {o['pick'].lower()} ({o['p']}%)" if o else "") + f". Typical move ±{p['move_med']}% (about {pts:,.0f} pts).")
