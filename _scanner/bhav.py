"""NSE / BSE F&O bhavcopy (the exchanges' end-of-day files) and a CAS check on past expiries.

The bhavcopy (UDiFF format, NSE and BSE alike since July 2024) lists every derivatives contract of the
day with, among others: TckrSymb, FinInstrmTp (IDO = index option), XpryDt, StrkPric, OptnTp,
ClsPric, LastPric, SttlmPric, UndrlygPric, TtlTradgVol.

On an expiry day the expiring options' SttlmPric is their final settlement value, and LastPric is their
last trade (near the end of trading, after the closing auction had set the index close). So for every
past expiry since the closing auction began (3 Aug 2026), the gap between the last traded price and the
settlement price, after charges, shows how much the CAS trade really offered - a rough upper bound: a
last trade can be a small lot you could not have matched.

NSE files: nsearchives.nseindia.com/content/fo/BhavCopy_NSE_FO_0_0_0_<YYYYMMDD>_F_0000.csv.zip
BSE files: www.bseindia.com/download/Bhavcopy/Derivative/BhavCopy_BSE_FO_0_0_0_<YYYYMMDD>_F_0000.CSV
"""
import csv
import io
import time
import urllib.request
import zipfile
from datetime import date, timedelta

UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124 Safari/537.36"
CAS_START = date(2026, 8, 3)                           # the closing auction sets the close from this day
CAS_END = date(2026, 12, 6)                            # expiries stop settling on the auction after this
STT_EXERCISE, STT_SELL, CHARGES, BROKERAGE = 0.00125, 0.001, 0.0006, 20.0


def url_for(exchange, d):
    ymd = d.strftime("%Y%m%d")
    if exchange == "NSE":
        return f"https://nsearchives.nseindia.com/content/fo/BhavCopy_NSE_FO_0_0_0_{ymd}_F_0000.csv.zip"
    return f"https://www.bseindia.com/download/Bhavcopy/Derivative/BhavCopy_BSE_FO_0_0_0_{ymd}_F_0000.CSV"


def fetch(exchange, d, timeout=15):
    """The day's F&O bhavcopy rows (list of dicts), or None if there is no file (holiday) / no access."""
    req = urllib.request.Request(url_for(exchange, d), headers={
        "User-Agent": UA, "Accept": "*/*", "Referer": "https://www.nseindia.com/" if exchange == "NSE" else "https://www.bseindia.com/"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            raw = r.read()
    except Exception:
        return None
    return parse(raw)


def parse(raw):
    """CSV or zipped CSV bytes -> rows."""
    if raw[:2] == b"PK":
        with zipfile.ZipFile(io.BytesIO(raw)) as z:
            name = next((n for n in z.namelist() if n.lower().endswith(".csv")), None)
            if not name:
                return None
            raw = z.read(name)
    text = raw.decode("utf-8", "replace").lstrip("﻿")
    rows = list(csv.DictReader(io.StringIO(text)))
    return [{(k or "").strip(): (v or "").strip() for k, v in r.items()} for r in rows] or None


def _f(x):
    try:
        return float(x)
    except (TypeError, ValueError):
        return None


def expiry_check(rows, symbol, day, lot):
    """For one index's options expiring `day`: how far the last trades were from settlement, after charges.
    Returns None if there are no such contracts in the file."""
    iso = day.isoformat()
    opts = [r for r in rows if r.get("TckrSymb") == symbol and r.get("FinInstrmTp") in ("IDO", "OPTIDX")
            and (r.get("XpryDt") or "")[:10] == iso and r.get("OptnTp") in ("CE", "PE")]
    if not opts:
        return None
    under = next((_f(r.get("UndrlygPric")) for r in opts if _f(r.get("UndrlygPric"))), None)
    found = []
    for r in opts:
        last, settle, vol = _f(r.get("LastPric")), _f(r.get("SttlmPric")), _f(r.get("TtlTradgVol")) or 0
        if last is None or settle is None or vol <= 0:
            continue
        buy = settle - last - (last * CHARGES + settle * STT_EXERCISE + BROKERAGE / lot)    # bought at the last price
        sell = last - settle - (last * (STT_SELL + CHARGES) + BROKERAGE / lot)            # sold at the last price
        edge, side = (buy, "BUY") if buy >= sell else (sell, "SELL")
        if edge > 0.5:
            found.append({"strike": _f(r.get("StrkPric")), "kind": r["OptnTp"], "side": side, "last": last,
                          "settle": settle, "edge": round(edge, 2), "edge_lot": round(edge * lot), "volume": int(vol)})
    found.sort(key=lambda x: -x["edge_lot"])
    return {"date": iso, "symbol": symbol, "close": under, "count": len(found),
            "best": found[0] if found else None, "top": found[:5],
            "sum_top3_lot": sum(x["edge_lot"] for x in found[:3])}


def expiry_days(start, end, weekday):
    """Candidate expiry days: that weekday each week (a holiday moves it to the day before: tried in order)."""
    d = start + timedelta(days=(weekday - start.weekday()) % 7)
    while d <= end:
        yield [d, d - timedelta(days=1), d - timedelta(days=2)]
        d += timedelta(days=7)


def cas_backtest(today, lots, cache=None, sleep=1.0, fetcher=fetch):
    """Every NIFTY (Tuesday) and SENSEX (Thursday) expiry from CAS_START to min(today, CAS_END).
    cache: {key: result} of days already checked (files never change). Returns (results, cache)."""
    cache = dict(cache or {})
    end = min(today - timedelta(days=0 if today.weekday() >= 5 else 1), CAS_END)
    out = []
    for symbol, exch, wd in (("NIFTY", "NSE", 1), ("SENSEX", "BSE", 3)):
        lot = lots.get(symbol) or (75 if symbol == "NIFTY" else 20)
        misses = 0                                     # downloads that failed in a row: give up after 6
        for tries in expiry_days(CAS_START, end, wd):
            if misses >= 6:
                break
            key = f"{symbol}:{tries[0].isoformat()}"
            if key in cache:
                if cache[key]:
                    out.append(cache[key])
                continue
            res, any_file = None, False
            for d in tries:
                if d.weekday() >= 5:
                    continue
                rows = fetcher(exch, d)
                time.sleep(sleep)
                if rows is None:
                    misses += 1
                    continue
                misses, any_file = 0, True
                res = expiry_check(rows, symbol, d, lot)
                if res:
                    break
            if res or any_file:                        # a day with files but no expiry is settled too
                cache[key] = res
            if res:
                out.append(res)
    out.sort(key=lambda r: r["date"])
    return out, cache


def summary(results):
    if not results:
        return None
    best = [r["best"]["edge_lot"] for r in results if r.get("best")]
    return {"expiries": len(results), "with_edge": len(best),
            "avg_best_lot": round(sum(best) / len(best)) if best else 0,
            "median_best_lot": sorted(best)[len(best) // 2] if best else 0,
            "total_top3_lot": sum(r["sum_top3_lot"] for r in results)}
