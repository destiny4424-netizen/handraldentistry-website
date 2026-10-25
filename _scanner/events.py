"""Market events that move Indian stocks: Fed (FOMC) and RBI policy days, the Union Budget,
crude oil and the rupee, risk headlines (war, sanctions, tariffs...), and company results.

Everything here is a caution, not a signal: it flags picks and trims BTST when an event can
gap the market overnight. Network sources (news, NSE's results calendar) are optional; if they
can't be reached the scanner simply carries on without them.
"""
import json
import re
import time
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from datetime import date, datetime, timedelta
from email.utils import parsedate_to_datetime

# FOMC decision days (the 2nd day of each meeting). The statement comes out at 2:00 PM New York
# time = 11:30 PM IST (12:30 AM IST in US winter time), so Indian stocks react the NEXT morning.
FOMC = ["2026-01-28", "2026-03-18", "2026-04-29", "2026-06-17", "2026-07-29", "2026-09-16",
        "2026-10-28", "2026-12-09",
        "2027-01-27", "2027-03-17", "2027-04-28", "2027-06-09", "2027-07-28", "2027-09-15",
        "2027-10-27", "2027-12-08"]
# RBI Monetary Policy decision days (last day of each MPC meeting), announced at 10:00 AM IST.
RBI = ["2026-04-08", "2026-06-05", "2026-08-05", "2026-10-07", "2026-12-04", "2027-02-05"]
BUDGET = ["2027-02-01"]

RATE_SENSITIVE = {"Banks", "PSU Banks", "Private Banks", "Financials", "NBFC", "Realty", "Auto",
                  "Finance", "Housing Finance", "Insurance"}
# Crude oil up hurts these; crude down helps them. Oil producers are the other way round.
CRUDE_USERS = {"BPCL", "HINDPETRO", "IOC", "ASIANPAINT", "BERGEPAINT", "INDIGO", "PIDILITIND",
               "APOLLOTYRE", "MRF", "BALKRISIND", "CEATLTD", "GAIL", "IGL", "MGL", "PETRONET", "UPL",
               "SRF", "DEEPAKNTR", "AARTIIND", "BATAINDIA"}
CRUDE_PRODUCERS = {"ONGC", "OIL", "RELIANCE"}
# Rupee weaker helps exporters (IT, pharma); hurts importers.
IT_PHARMA = {"IT", "Pharma"}

RISK_WORDS = ["war", "missile", "airstrike", "air strike", "invasion", "invade", "attack", "terror",
              "nuclear", "sanction", "tariff", "escalat", "military", "conflict", "border clash",
              "strait of hormuz", "oil supply", "opec", "recession", "default", "crash", "sell-off",
              "selloff", "emergency", "ceasefire collapse", "shutdown", "rate hike", "bank failure"]
CALM_WORDS = ["ceasefire", "truce", "peace talks", "deal reached", "rate cut", "eases"]

STOCK_GOOD = ["order win", "bags order", "wins order", "contract", "upgrade", "buyback", "record profit",
              "profit rises", "profit jumps", "beats estimates", "dividend", "bonus", "approval", "acquire"]
STOCK_BAD = ["downgrade", "target cut", "sebi", "probe", "raid", "fraud", "penalty", "block deal",
             "stake sale", "profit falls", "loss widens", "misses estimates", "resigns", "pledge",
             "default", "ban", "fire at", "plant shut", "recall", "warning letter", "import alert"]
RESULT_WORDS = ["q1", "q2", "q3", "q4", "quarter", "results", "earnings", "net profit"]

UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124 Safari/537.36"
_CACHE = {}


def _get(url, ttl, headers=None, timeout=12):
    hit = _CACHE.get(url)
    if hit and time.time() - hit[0] < ttl:
        return hit[1]
    req = urllib.request.Request(url, headers=dict({"User-Agent": UA}, **(headers or {})))
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            body = r.read()
    except Exception:                                  # news is optional
        body = hit[1] if hit else None
    _CACHE[url] = (time.time(), body)
    return body


def _next_session(d):
    d += timedelta(days=1)
    while d.weekday() >= 5:
        d += timedelta(days=1)
    return d


def calendar(today):
    """Scheduled events in the next 7 days, and what each means for today's trades."""
    nxt = _next_session(today)
    out = []
    for name, days, when, note in (
            ("FOMC (US Fed) decision", FOMC, "11:30 PM IST", "US rate decision overnight: global markets can gap."),
            ("RBI policy decision", RBI, "10:00 AM", "Rate decision: banks, NBFCs, realty and autos swing."),
            ("Union Budget", BUDGET, "11:00 AM", "Budget day: whole market swings; sector news.")):
        for d in days:
            dd = date.fromisoformat(d)
            if today - timedelta(days=1) <= dd <= today + timedelta(days=7):
                if name.startswith("FOMC") and not 3 <= dd.month <= 10:
                    when = "12:30 AM IST, after midnight"   # US winter time
                ev = {"name": name, "date": d, "when": when, "note": note,
                      "label": "Today" if dd == today else "Tomorrow" if dd == nxt else
                               "Yesterday" if dd < today else dd.strftime("%a %d %b")}
                if name.startswith("FOMC"):
                    # US decision tonight -> India reacts on the next session.
                    ev["overnight"] = dd == today
                    ev["reacting"] = _next_session(dd) == today
                    ev["intraday"] = ev["reacting"]
                    if ev["reacting"]:
                        ev["label"] = "Last night"
                else:
                    ev["overnight"] = dd == nxt     # BTST would carry the event
                    ev["intraday"] = dd == today
                    ev["reacting"] = False
                out.append(ev)
    out.sort(key=lambda e: e["date"])
    return out


def commodity_view(name, candles, daily_close_prev):
    """Today's % change of crude / USDINR from 5-minute candles."""
    if not candles:
        return None
    price = candles[-1]["c"]
    prev = daily_close_prev or candles[0]["o"]
    return {"name": name, "price": round(price, 2), "pct": round((price - prev) / prev * 100, 2)}


def headlines(max_items=8):
    """Market and world risk headlines from Google News (RSS), newest first."""
    queries = ["stock market India Nifty Sensex", "war OR missile OR sanctions OR tariffs markets",
               "crude oil prices", "Federal Reserve OR RBI policy"]
    seen, items = set(), []
    for q in queries:
        url = "https://news.google.com/rss/search?" + urllib.parse.urlencode(
            {"q": q + " when:1d", "hl": "en-IN", "gl": "IN", "ceid": "IN:en"})
        items += _rss(url, 900)
    out = []
    for it in sorted(items, key=lambda x: -x["ts"]):
        key = it["title"].lower()[:60]
        if key in seen:
            continue
        seen.add(key)
        t = it["title"].lower()
        risk = [w for w in RISK_WORDS if w in t]
        calm = [w for w in CALM_WORDS if w in t]
        it["tone"] = "risk" if risk and not calm else "calm" if calm else ""
        it["tags"] = risk[:2]
        out.append(it)
    return out[:max_items]


def _rss(url, ttl):
    body = _get(url, ttl)
    if not body:
        return []
    try:
        root = ET.fromstring(body)
    except ET.ParseError:
        return []
    out = []
    for item in root.iter("item"):
        title = (item.findtext("title") or "").strip()
        link = (item.findtext("link") or "").strip()
        src = (item.findtext("source") or "").strip()
        try:
            ts = parsedate_to_datetime(item.findtext("pubDate") or "").timestamp()
        except (TypeError, ValueError):
            ts = 0
        if src and title.endswith(" - " + src):
            title = title[: -len(src) - 3]
        if title:
            out.append({"title": title, "link": link, "source": src, "ts": ts})
    return out


def risk_level(news, events, vix, crude):
    """Low / Medium / High, with the reasons."""
    why, score = [], 0
    hot = [n for n in news if n["tone"] == "risk"]
    if len(hot) >= 3:
        score += 2; why.append(f"{len(hot)} risk headlines (war/sanctions/tariffs…)")
    elif hot:
        score += 1; why.append("risk headline in the news")
    if any(e["overnight"] or e["intraday"] or e.get("reacting") for e in events):
        score += 1; why.append("big scheduled event: " + ", ".join(
            f'{e["name"]} {e["label"].lower()}' for e in events if e["overnight"] or e["intraday"] or e.get("reacting")))
    if vix and vix.get("level") == "High":
        score += 1; why.append("India VIX is high")
    if crude and abs(crude["pct"]) >= 2.5:
        score += 1; why.append(f"crude oil {crude['pct']:+.1f}% today")
    level = "High" if score >= 3 else "Medium" if score >= 1 else "Low"
    return {"level": level, "why": why}


def stock_flags(symbol, sector, side, crude, usdinr, events, results_on, today):
    """Warnings and tailwinds for one pick from events, crude, the rupee and its results date."""
    flags = []
    bull = side == "bull"
    if crude and abs(crude["pct"]) >= 1.5:
        up = crude["pct"] > 0
        if symbol in CRUDE_USERS:
            good = (not up) == bull
            flags.append({"ok": good, "text": f"Crude {crude['pct']:+.1f}%: {'tailwind' if good else 'headwind'}"})
        elif symbol in CRUDE_PRODUCERS:
            good = up == bull
            flags.append({"ok": good, "text": f"Crude {crude['pct']:+.1f}%: {'tailwind' if good else 'headwind'}"})
    if usdinr and abs(usdinr["pct"]) >= 0.4 and sector in IT_PHARMA:
        good = (usdinr["pct"] > 0) == bull
        flags.append({"ok": good, "text": f"Rupee {'weaker' if usdinr['pct'] > 0 else 'stronger'}: {'tailwind' if good else 'headwind'}"})
    if sector in RATE_SENSITIVE:
        for e in events:
            if e["name"].startswith("RBI") and (e["intraday"] or e["overnight"]):
                flags.append({"ok": False, "text": f"RBI policy {e['label'].lower()} {e['when']}: rate-sensitive"})
    if results_on:
        d = date.fromisoformat(results_on)
        if 0 <= (d - today).days <= 3:
            label = "today" if d == today else "tomorrow" if d == _next_session(today) else d.strftime("%a %d %b")
            flags.append({"ok": False, "results": True, "date": results_on,
                          "text": f"Results {label}: expect a big move/gap"})
    return flags


def stock_news(name, symbol, n=2):
    """Latest headlines for one company, tagged good/bad/results."""
    q = f'"{name}" OR "{symbol}" share when:3d'
    url = "https://news.google.com/rss/search?" + urllib.parse.urlencode(
        {"q": q, "hl": "en-IN", "gl": "IN", "ceid": "IN:en"})
    out = []
    for it in sorted(_rss(url, 1800), key=lambda x: -x["ts"])[:n]:
        t = it["title"].lower()
        it["tone"] = "bad" if any(w in t for w in STOCK_BAD) else "good" if any(w in t for w in STOCK_GOOD) else ""
        it["results"] = any(re.search(r"\b" + re.escape(w) + r"\b", t) for w in RESULT_WORDS)
        out.append(it)
    return out


def results_calendar():
    """{symbol: 'YYYY-MM-DD'} of upcoming board meetings for financial results, from NSE.
    NSE needs a browser-like visit first (cookies); if it refuses, returns {}."""
    hit = _CACHE.get("nse-results")
    if hit and time.time() - hit[0] < 6 * 3600:
        return hit[1]
    out = {}
    try:
        import http.cookiejar
        jar = http.cookiejar.CookieJar()
        op = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(jar))
        hdr = {"User-Agent": UA, "Accept": "*/*", "Accept-Language": "en-US,en;q=0.9",
               "Referer": "https://www.nseindia.com/companies-listing/corporate-filings-event-calendar"}
        op.open(urllib.request.Request("https://www.nseindia.com/", headers=hdr), timeout=12).read()
        raw = op.open(urllib.request.Request("https://www.nseindia.com/api/event-calendar?index=equities",
                                             headers=hdr), timeout=12).read()
        for row in json.loads(raw):
            purpose = (row.get("purpose") or "") + " " + (row.get("bm_desc") or "")
            if "result" not in purpose.lower():
                continue
            try:
                d = datetime.strptime(row.get("date") or "", "%d-%b-%Y").date().isoformat()
            except ValueError:
                continue
            sym = row.get("symbol")
            if sym and (sym not in out or d < out[sym]):
                out[sym] = d
    except Exception:
        out = hit[1] if hit else {}
    _CACHE["nse-results"] = (time.time(), out)
    return out
