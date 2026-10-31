"""Learning journal: what worked and what failed, from the finished trades.

Every finished trade carries what it looked like when it was given (breakout volume, time, side, NIFTY,
retest, sector, score...). The journal groups the trades by each of these and compares win rate and
average R, then turns clear differences into plain suggestions. Suggestions are only advice: rule changes
are made after you approve them.
"""
MIN_N = 8                                              # fewer trades than this in a group: not enough to judge


def _bucket_vol(f):
    v = f.get("vol_x")
    return None if v is None else "1.5–2×" if v < 2 else "2–3×" if v < 3 else "3× and more"


def _bucket_time(f):
    t = f.get("broke_at")
    if not t:
        return None
    return "9:30–10:00" if t <= "10:00" else "10:00–11:00" if t <= "11:00" else "11:00–12:30" if t <= "12:30" else "after 12:30"


def _bucket_score(f):
    s = f.get("score")
    return None if s is None else "score 70+" if s >= 70 else "score 55–70" if s >= 55 else "score under 55"


INTRADAY_GROUPS = [
    ("Breakout volume", _bucket_vol),
    ("Time of breakout", _bucket_time),
    ("Side", lambda f: {"bull": "Buys (PDH)", "bear": "Sells (PDL)"}.get(f.get("side"))),
    ("NIFTY", lambda f: {"with": "With NIFTY", "against": "Against NIFTY", "sideways": "NIFTY sideways"}.get(f.get("nifty"))),
    ("Retest", lambda f: "After a retest" if f.get("retest") else "Straight breakout"),
    ("Gap open", lambda f: "Gap beyond the level" if f.get("gap") else "No gap"),
    ("Score", _bucket_score),
    ("Sector", lambda f: f.get("sector")),
    ("NIFTY gamma (GEX)", lambda f: {"pos": "Positive gamma (range day)", "neg": "Negative gamma (trend day)"}.get(f.get("gex"))),
]
BTST_GROUPS = [
    ("Side", lambda f: {"bull": "BTST (buy)", "bear": "STBT (sell)"}.get(f.get("side"))),
    ("20-day breakout", lambda f: "At a 20-day high/low" if f.get("at20") else "Not at 20-day extreme"),
    ("Day's move", lambda f: None if f.get("pct") is None else "1.5–3%" if abs(f["pct"]) < 3 else "3–5%" if abs(f["pct"]) < 5 else "5% and more"),
    ("Volume", lambda f: None if f.get("rvol") is None else "1.5–2.5×" if f["rvol"] < 2.5 else "2.5× and more"),
    ("Score", _bucket_score),
    ("Sector", lambda f: f.get("sector")),
    ("NIFTY gamma (GEX)", lambda f: {"pos": "Positive gamma (range day)", "neg": "Negative gamma (trend day)"}.get(f.get("gex"))),
]


def _stats(rows):
    done = [r["result_r"] for r in rows]
    wins = [x for x in done if x > 0]
    return {"n": len(done), "win_rate": round(100 * len(wins) / len(done)) if done else None,
            "avg_r": round(sum(done) / len(done), 2) if done else None, "total_r": round(sum(done), 2)}


def analyse(rows, kind="intraday"):
    rows = [r for r in rows if r.get("result_r") is not None and r.get("f")]
    overall = _stats(rows)
    groups, insights = [], []
    for name, fn in (INTRADAY_GROUPS if kind == "intraday" else BTST_GROUPS):
        by = {}
        for r in rows:
            k = fn(r["f"])
            if k:
                by.setdefault(k, []).append(r)
        if len(by) < 2:
            continue
        buckets = sorted(({"label": k, **_stats(v)} for k, v in by.items()), key=lambda b: -(b["avg_r"] or 0))
        groups.append({"name": name, "buckets": buckets})
        judged = [b for b in buckets if b["n"] >= MIN_N]
        if len(judged) < 2 or overall["avg_r"] is None:
            continue
        best, worst = judged[0], judged[-1]
        if best["avg_r"] - worst["avg_r"] >= 0.3:
            insights.append({"group": name, "good": best["label"], "bad": worst["label"],
                             "text": f"{name}: {best['label']} made {best['avg_r']:+.2f}R a trade ({best['win_rate']}% won, "
                                     f"{best['n']} trades); {worst['label']} made {worst['avg_r']:+.2f}R ({worst['win_rate']}% won, {worst['n']} trades)."})
            if worst["avg_r"] < 0:
                insights[-1]["suggest"] = f"Consider skipping “{worst['label']}” ({name.lower()})."
    insights.sort(key=lambda i: 0 if i.get("suggest") else 1)
    return {"overall": overall, "groups": groups, "insights": insights[:8], "min_n": MIN_N}
