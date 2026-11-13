"""Market Pulse: the whole market's mood before taking a trade.

Pulled together from:
  - NSE "all indices" (one request): every NSE index today - large, mid and small caps, all sectors - with
    advances/declines, 52-week range, 30-day and 1-year change, P/E.
  - Dhan daily candles of the main indices: trend against the 20/50/200-day averages, 1-week and 1-month change.
  - NSE FII/DII cash (provisional, after the close): how much foreign and domestic institutions bought/sold.
  - NSE participant-wise open interest (after ~7 PM): how FIIs are positioned in index futures and options
    (long % of their index futures, net calls and puts), day by day.
  - NIFTY option chain (already fetched for GEX): put/call ratio, call and put walls, max pain.
  - India VIX, and the scanner's own breadth (F&O stocks, the mid caps above their 50-day average, new
    52-week highs and lows).

Each source is optional: if one can't be reached, its part is left out and the mood is worked out from
the rest (with fewer points in play).

The mood score runs from -100 (risk-off) to +100 (risk-on):
  trend 20 · breadth 20 · FII cash 15 · FII index futures 15 · options PCR 10 · VIX 10 · risk appetite 10.
"""
import csv
import io
import json
import math
import random
import time
import urllib.request
from datetime import date, datetime, timedelta

UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124 Safari/537.36"
_CACHE = {}

# The indices shown, with the names NSE and Dhan use for them (compared without spaces / punctuation).
BROAD = [("NIFTY 50", ["NIFTY50", "NIFTY"]), ("NIFTY NEXT 50", ["NIFTYNEXT50", "NIFTYJR", "NIFTYNXT50"]),
         ("NIFTY 500", ["NIFTY500"]), ("NIFTY MIDCAP 150", ["NIFTYMIDCAP150"]),
         ("NIFTY MIDCAP 100", ["NIFTYMIDCAP100", "NIFTYMIDCAP"]), ("NIFTY SMALLCAP 250", ["NIFTYSMALLCAP250"]),
         ("NIFTY SMALLCAP 100", ["NIFTYSMALLCAP100", "NIFTYSMLCAP100"]), ("NIFTY MICROCAP 250", ["NIFTYMICROCAP250"])]
SECTORS = [("NIFTY BANK", ["NIFTYBANK", "BANKNIFTY"]), ("NIFTY PVT BANK", ["NIFTYPVTBANK", "NIFTYPRIVATEBANK"]),
           ("NIFTY PSU BANK", ["NIFTYPSUBANK"]), ("NIFTY FIN SERVICE", ["NIFTYFINSERVICE", "FINNIFTY", "NIFTYFINANCIALSERVICES"]),
           ("NIFTY IT", ["NIFTYIT"]), ("NIFTY AUTO", ["NIFTYAUTO"]), ("NIFTY PHARMA", ["NIFTYPHARMA"]),
           ("NIFTY HEALTHCARE", ["NIFTYHEALTHCAREINDEX", "NIFTYHEALTHCARE"]), ("NIFTY FMCG", ["NIFTYFMCG"]),
           ("NIFTY METAL", ["NIFTYMETAL"]), ("NIFTY REALTY", ["NIFTYREALTY"]), ("NIFTY ENERGY", ["NIFTYENERGY"]),
           ("NIFTY OIL & GAS", ["NIFTYOILGAS", "NIFTYOILANDGAS"]), ("NIFTY MEDIA", ["NIFTYMEDIA"]),
           ("NIFTY INFRA", ["NIFTYINFRA", "NIFTYINFRASTRUCTURE"]), ("NIFTY CONSUMER DURABLES", ["NIFTYCONSUMERDURABLES", "NIFTYCONSRDURBL"]),
           ("NIFTY CPSE", ["NIFTYCPSE"]), ("NIFTY PSE", ["NIFTYPSE"]), ("NIFTY INDIA DEFENCE", ["NIFTYINDIADEFENCE"]),
           ("NIFTY CHEMICALS", ["NIFTYCHEMICALS"])]
ALL = BROAD + SECTORS


def norm(name):
    return "".join(ch for ch in str(name or "").upper() if ch.isalnum()).replace("AND", "")


ALIAS = {norm(a): name for name, aliases in ALL for a in aliases + [name]}


def short(name):
    if name in ("NIFTY 50", "NIFTY 500", "NIFTY NEXT 50"):
        return name.title()
    return name.replace("NIFTY ", "").title().replace("Fmcg", "FMCG").replace("Psu", "PSU").replace("It", "IT") \
        .replace("Pvt", "Pvt").replace("Cpse", "CPSE").replace("Pse", "PSE").replace("Fin Service", "Fin Services")


def _f(x):
    try:
        return float(str(x).replace(",", ""))
    except (TypeError, ValueError):
        return None


# ── NSE ────────────────────────────────────────────────────────────────────────────────────
def _nse():
    """An opener with NSE's cookies (it wants a browser-like first visit), reused for 10 minutes."""
    hit = _CACHE.get("nse-opener")
    if hit and time.time() - hit[0] < 600:
        return hit[1]
    import http.cookiejar
    op = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar()))
    op.addheaders = [("User-Agent", UA), ("Accept", "*/*"), ("Accept-Language", "en-US,en;q=0.9"),
                     ("Referer", "https://www.nseindia.com/market-data/live-market-indices")]
    op.open("https://www.nseindia.com/", timeout=12).read()
    _CACHE["nse-opener"] = (time.time(), op)
    return op


def _cached(key, ttl, fn):
    hit = _CACHE.get(key)
    if hit and time.time() - hit[0] < ttl:
        return hit[1]
    try:
        val = fn()
    except Exception:
        val = hit[1] if hit else None
    _CACHE[key] = (time.time(), val)
    return val


def nse_indices():
    """{our index name: snapshot} from NSE's allIndices."""
    def get():
        raw = json.loads(_nse().open("https://www.nseindia.com/api/allIndices", timeout=15).read())
        out = {}
        for r in raw.get("data") or []:
            name = ALIAS.get(norm(r.get("index"))) or ALIAS.get(norm(r.get("indexSymbol")))
            if not name or name in out:
                continue
            out[name] = {"last": _f(r.get("last")), "pct": _f(r.get("percentChange")),
                         "adv": int(_f(r.get("advances"))) if _f(r.get("advances")) is not None else None,
                         "dec": int(_f(r.get("declines"))) if _f(r.get("declines")) is not None else None,
                         "yh": _f(r.get("yearHigh")), "yl": _f(r.get("yearLow")),
                         "m1": _f(r.get("perChange30d")), "y1": _f(r.get("perChange365d")), "pe": _f(r.get("pe"))}
        return out or None
    return _cached("nse-indices", 240, get)


def nse_fii_cash():
    """Today's provisional FII/FPI and DII cash-market figures (₹ crore), after the close."""
    def get():
        rows = json.loads(_nse().open("https://www.nseindia.com/api/fiidiiTradeReact", timeout=15).read())
        out = {}
        for r in rows or []:
            cat = (r.get("category") or "").upper()
            key = "fii" if "FII" in cat or "FPI" in cat else "dii" if "DII" in cat else None
            if key:
                out[key] = {"buy": _f(r.get("buyValue")), "sell": _f(r.get("sellValue")), "net": _f(r.get("netValue"))}
                out["date"] = r.get("date")
        return out if out.get("fii") else None
    return _cached("nse-fii-cash", 1800, get)


def participant_oi(day):
    """NSE's participant-wise open interest for one day: {client type: {column: contracts}} or None."""
    key = "poi-" + day.isoformat()
    hit = _CACHE.get(key)
    if hit:
        return hit[1]
    url = f"https://nsearchives.nseindia.com/content/nsccl/fao_participant_oi_{day.strftime('%d%m%Y')}.csv"
    try:
        req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "text/csv,*/*"})
        with urllib.request.urlopen(req, timeout=15) as r:
            text = r.read().decode("utf-8", "replace")
        val = parse_participant_oi(text)
    except Exception:
        val = None
    if val:
        _CACHE[key] = (time.time(), val)               # a day's file never changes
    return val


def parse_participant_oi(text):
    lines = [ln for ln in text.splitlines() if ln.strip()]
    start = next((i for i, ln in enumerate(lines) if ln.lower().startswith("client type")), None)
    if start is None:
        return None
    out = {}
    for row in csv.DictReader(io.StringIO("\n".join(lines[start:]))):
        row = {(k or "").strip(): (v or "").strip() for k, v in row.items()}
        who = row.get("Client Type", "").upper()
        if who:
            out[who] = {k: _f(v) or 0 for k, v in row.items() if k != "Client Type"}
    return out or None


def fii_positioning(days=10, today=None):
    """FII index futures and options positioning for the last trading days (newest last)."""
    today = today or date.today()
    out, d, tries = [], today, 0
    while len(out) < days and tries < days * 2 + 6:
        tries += 1
        if d.weekday() < 5:
            p = participant_oi(d)
            if p and "FII" in p:
                out.append(position_row(d, p["FII"]))
        d -= timedelta(days=1)
    return list(reversed(out))


def position_row(d, f):
    def g(*names):
        for n in names:
            for k, v in f.items():
                if norm(k) == norm(n):
                    return v
        return 0
    fl, fs = g("Future Index Long"), g("Future Index Short")
    cl, cs = g("Option Index Call Long"), g("Option Index Call Short")
    pl, ps = g("Option Index Put Long"), g("Option Index Put Short")
    return {"date": d.isoformat(), "fut_long": fl, "fut_short": fs,
            "long_pct": round(100 * fl / (fl + fs), 1) if fl + fs else None, "fut_net": fl - fs,
            "call_net": cl - cs, "put_net": pl - ps}


# ── Pieces of the mood ─────────────────────────────────────────────────────────────────────
def trend_of(daily):
    """Close vs 20/50/200-day averages, 1-week / 1-month change, distance from the 52-week high."""
    c = [x["c"] for x in daily or []]
    if len(c) < 30:
        return None
    sma = lambda n: sum(c[-n:]) / n if len(c) >= n else None
    s20, s50, s200 = sma(20), sma(50), sma(200)
    last = c[-1]
    above = sum(1 for s in (s20, s50, s200) if s and last > s)
    n = sum(1 for s in (s20, s50, s200) if s)
    hi = max(x["h"] for x in daily[-250:])
    return {"last": round(last, 2), "w1": round((last / c[-6] - 1) * 100, 2) if len(c) > 6 else None,
            "m1": round((last / c[-22] - 1) * 100, 2) if len(c) > 22 else None,
            "m3": round((last / c[-64] - 1) * 100, 2) if len(c) > 64 else None,
            "above": above, "of": n, "s20": s20, "s50": s50, "s200": s200,
            "from_high": round((last / hi - 1) * 100, 1),
            "label": "Uptrend" if above == n else "Downtrend" if above == 0 else "Mixed"}


def rotation(sec, bench):
    """Sector rotation quadrant from 1-month strength vs NIFTY and 1-week momentum vs NIFTY."""
    if not sec or not bench or sec.get("m1") is None or bench.get("m1") is None or sec.get("w1") is None:
        return None
    rs, mom = sec["m1"] - bench["m1"], sec["w1"] - (bench.get("w1") or 0)
    return "Leading" if rs >= 0 and mom >= 0 else "Weakening" if rs >= 0 else "Improving" if mom >= 0 else "Lagging"


def _clip(x):
    return max(-1.0, min(1.0, x))


def mood(nifty_t, n500_snap, mid_breadth, fii_cash_hist, pos, pcr, vix, vix_pct, small_vs_large):
    """The mood score (-100..+100) from whatever pieces are available, with each part shown."""
    parts = []

    def add(name, weight, val, text):
        if val is not None:
            parts.append({"name": name, "weight": weight, "value": round(_clip(val), 2), "points": round(weight * _clip(val), 1), "text": text})
    if nifty_t:
        add("Trend", 20, (nifty_t["above"] / max(1, nifty_t["of"])) * 2 - 1 + (0.25 if (nifty_t.get("w1") or 0) > 0 else -0.25),
            f"NIFTY above {nifty_t['above']} of its {nifty_t['of']} averages (20/50/200-day), {nifty_t.get('w1') or 0:+.1f}% this week")
    br = []
    if n500_snap and n500_snap.get("adv") is not None and (n500_snap["adv"] + n500_snap["dec"]):
        ad = n500_snap["adv"] / (n500_snap["adv"] + n500_snap["dec"])
        br.append((ad - 0.5) * 2.5)
    if mid_breadth and mid_breadth.get("above50") is not None:
        br.append((mid_breadth["above50"] / 100 - 0.5) * 2.5)
    if br:
        txt = []
        if n500_snap and n500_snap.get("adv") is not None:
            txt.append(f"NIFTY 500 {n500_snap['adv']} up / {n500_snap['dec']} down today")
        if mid_breadth and mid_breadth.get("above50") is not None:
            txt.append(f"{mid_breadth['above50']}% of mid caps above their 50-day average")
        add("Breadth", 20, sum(br) / len(br), " · ".join(txt))
    if fii_cash_hist:
        last = fii_cash_hist[-1]["fii"]
        five = sum(x["fii"] for x in fii_cash_hist[-5:])
        add("FII cash", 15, 0.5 * math.tanh(last / 2500) + 0.5 * math.tanh(five / 8000),
            f"FIIs {'bought' if last >= 0 else 'sold'} ₹{abs(last):,.0f} cr on {fii_cash_hist[-1]['date']}; "
            f"{'+' if five >= 0 else '−'}₹{abs(five):,.0f} cr over the last {min(5, len(fii_cash_hist))} sessions")
    if pos:
        lp = pos[-1]["long_pct"]
        ch = lp - pos[-6]["long_pct"] if len(pos) >= 6 and pos[-6]["long_pct"] is not None and lp is not None else 0
        if lp is not None:
            add("FII index futures", 15, (lp - 45) / 25 + ch / 20,
                f"FIIs {lp:.0f}% long in index futures ({ch:+.0f} pts in a week)")
    if pcr:
        add("Options (PCR)", 10, (pcr - 0.95) / 0.35 if pcr <= 1.5 else (1.6 - pcr) / 0.3,
            f"NIFTY put/call ratio {pcr:.2f}" + (" (very high: crowded hedges)" if pcr > 1.5 else ""))
    if vix:
        add("VIX", 10, (16 - vix) / 6 + ((50 - vix_pct) / 100 if vix_pct is not None else 0),
            f"India VIX {vix:.1f}" + (f", {vix_pct}th percentile of the last 4 months" if vix_pct is not None else ""))
    if small_vs_large is not None:
        add("Risk appetite", 10, small_vs_large / 4,
            f"Small caps {small_vs_large:+.1f}% vs NIFTY over a month")
    if not parts:
        return None
    total_w = sum(p["weight"] for p in parts)
    score = round(100 * sum(p["points"] for p in parts) / total_w)
    label, advice = (("Risk-on", "Favour longs; normal-to-full size; buy breakouts.") if score >= 40 else
                     ("Bullish tilt", "Longs first; normal size; be pickier with shorts.") if score >= 15 else
                     ("Mixed", "Be selective and size down; take only A-grade setups, both sides.") if score > -15 else
                     ("Bearish tilt", "Shorts / puts first; smaller longs, tighter stops.") if score > -40 else
                     ("Risk-off", "Avoid fresh longs; shorts or cash; respect stops."))
    return {"score": score, "label": label, "advice": advice, "parts": parts, "weight_used": total_w}


# ── Demo data ──────────────────────────────────────────────────────────────────────────────
def demo_snapshot(seed=None):
    rng = random.Random(seed or date.today().isoformat())
    out = {}
    for name, _ in ALL:
        pct = rng.gauss(0.2, 1.1)
        adv = rng.randint(5, 45)
        out[name] = {"last": rng.uniform(5000, 60000), "pct": round(pct, 2), "adv": adv, "dec": 50 - adv,
                     "yh": None, "yl": None, "m1": round(rng.gauss(1, 4), 2), "y1": round(rng.gauss(10, 12), 2), "pe": round(rng.uniform(15, 45), 1)}
    out["NIFTY 500"].update(adv=rng.randint(150, 350))
    out["NIFTY 500"]["dec"] = 500 - out["NIFTY 500"]["adv"]
    return out


def demo_fii(n=8, seed=None):
    rng = random.Random(seed or "fii" + date.today().isoformat())
    d, out = date.today(), []
    while len(out) < n:
        d -= timedelta(days=1)
        if d.weekday() < 5:
            out.append({"date": d.strftime("%d-%b-%Y"), "fii": round(rng.gauss(-300, 2200)), "dii": round(rng.gauss(900, 1500))})
    return list(reversed(out))


def demo_positioning(n=10, seed=None):
    rng = random.Random(seed or "pos" + date.today().isoformat())
    d, out, lp = date.today(), [], rng.uniform(20, 60)
    while len(out) < n:
        d -= timedelta(days=1)
        if d.weekday() < 5:
            lp = max(8, min(85, lp + rng.gauss(0, 4)))
            total = 300000
            fl = total * lp / 100
            out.append(position_row(d, {"Future Index Long": fl, "Future Index Short": total - fl,
                                        "Option Index Call Long": rng.uniform(4e5, 9e5), "Option Index Call Short": rng.uniform(4e5, 9e5),
                                        "Option Index Put Long": rng.uniform(4e5, 9e5), "Option Index Put Short": rng.uniform(4e5, 9e5)}))
    return list(reversed(out))
