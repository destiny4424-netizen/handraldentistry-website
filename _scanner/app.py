"""OrderBlock Scanner: scans NSE F&O stocks with the Dhan API, finds order
blocks and liquidity, and serves a live ranked dashboard.

Run:  python app.py        (needs Python 3.9+, no extra packages)
Keys: put DHAN_CLIENT_ID and DHAN_ACCESS_TOKEN in a .env file next to this one.
      With no keys it starts in DEMO mode with made-up prices.
"""
import base64
import csv
import hashlib
import hmac
import io
import json
import secrets
import socket
import struct
import zlib
from http.cookies import SimpleCookie
import os
import pickle
import random
import sys
import threading
import time
import urllib.error
import urllib.request
import webbrowser
from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import analysis
import backtest
import breakout
import bullion
import btst
import events
import gex
import intraday
import journal
import market
import options
import orb
import picks
import risk
import trades

HERE = Path(__file__).resolve().parent
IST = timezone(timedelta(hours=5, minutes=30))
API = "https://api.dhan.co/v2"
SCRIP_MASTER = "https://images.dhan.co/api-data/api-scrip-master.csv"
VERSION = "31 Oct · easier on the phone"


def load_env():
    env = {}
    path = HERE / ".env"
    if path.exists():
        for line in path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                v = v.split(" #", 1)[0].split("\t#", 1)[0]     # drop trailing notes
                env[k.strip()] = v.strip().strip('"').strip("'")
    for k in list(env):
        env[k] = os.environ.get(k, env[k])
    return env


ENV = load_env()
# On the droplet (installed by _scanner/deploy/install.sh) the scanner sits behind Caddy
# with HTTPS, and every visitor, phone or PC, signs in with the scanner password.
CLOUD = os.environ.get("OBS_CLOUD") == "1"
PASSWORD = os.environ.get("OBS_PASSWORD", "")
PUBLIC_URL = os.environ.get("OBS_PUBLIC_URL", "")

# Saved settings (Dhan keys, phone PIN) live outside the app folder, so they survive
# deleting the folder or unzipping a new version: %APPDATA%\OrderBlockScanner on Windows.
SETTINGS_DIR = Path(os.environ.get("OBS_SETTINGS_DIR") or
                    (Path(os.environ["APPDATA"]) / "OrderBlockScanner" if os.environ.get("APPDATA")
                     else Path.home() / ".orderblock-scanner"))
SETTINGS_FILE = SETTINGS_DIR / "settings.json"
SETTINGS_LOCK = threading.Lock()


def load_settings():
    try:
        return json.loads(SETTINGS_FILE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def save_settings(**changes):
    with SETTINGS_LOCK:
        data = load_settings()
        data.update(changes)
        data = {k: v for k, v in data.items() if v is not None}
        SETTINGS_DIR.mkdir(parents=True, exist_ok=True)
        tmp = SETTINGS_FILE.with_suffix(".tmp")
        tmp.write_text(json.dumps(data, indent=2), encoding="utf-8")
        try:
            os.chmod(tmp, 0o600)                      # holds the Dhan token: owner only
        except OSError:
            pass
        os.replace(tmp, SETTINGS_FILE)
        SETTINGS.clear()
        SETTINGS.update(data)


SETTINGS = load_settings()
_env_id = ENV.get("DHAN_CLIENT_ID", os.environ.get("DHAN_CLIENT_ID", ""))
_env_tok = ENV.get("DHAN_ACCESS_TOKEN", os.environ.get("DHAN_ACCESS_TOKEN", ""))
if _env_id and _env_tok and not SETTINGS.get("access_token"):
    try:                                               # move keys from an old .env into saved settings
        save_settings(client_id=_env_id, access_token=_env_tok,
                      saved_at=datetime.now(IST).strftime("%d %b %Y %H:%M"))
    except OSError:
        pass
CLIENT_ID = SETTINGS.get("client_id") or _env_id
TOKEN = SETTINGS.get("access_token") or _env_tok
DEMO = not (CLIENT_ID and TOKEN) or ENV.get("DEMO", "").lower() in ("1", "true", "yes")
TIMEFRAME = ENV.get("TIMEFRAME", "5")                  # 1, 5, 15, 25 or 60 minutes
SCAN_EVERY = int(ENV.get("SCAN_EVERY_MIN", "15"))     # auto-scan, right after each 15-min candle closes
BREAKOUT_TF = int(ENV.get("BREAKOUT_TF", "15"))        # HTF key level breakout candles
DAYS = int(ENV.get("DAYS", "5"))                       # calendar days of candles
SWING = int(ENV.get("SWING", "3"))
TOP_N = int(ENV.get("TOP_N", "20"))
HOST = os.environ.get("OBS_HOST") or ENV.get("HOST", "0.0.0.0")   # other devices need the phone PIN
PORT = int(os.environ.get("OBS_PORT") or ENV.get("PORT", "8000"))
N_PICKS = int(ENV.get("PICKS", "6"))                  # how many top picks to show
MIN_RR = float(ENV.get("MIN_RR", "1.5"))               # minimum reward:risk to the first target
MIN_SETUP = float(ENV.get("MIN_SETUP_SCORE", "55"))    # order block / breakout score needed to go on
OI_CHECKS = int(ENV.get("OI_CHECKS", "30"))            # shortlisted stocks to check futures OI for
LIQ_DAYS = 20                                          # liquidity = averages over the last 20 sessions
MIN_TURNOVER_CR = 200                                  # default: ₹200 crore traded a day on average
MIN_VOLUME_LAKH = 5                                    # default: 5 lakh shares a day on average
ONLY = [s.strip().upper() for s in ENV.get("SYMBOLS", "").split(",") if s.strip()]


class DhanError(Exception):
    """fatal=True stops the whole scan (bad token, no plan, no internet);
    otherwise only the one stock is skipped."""
    def __init__(self, message, fatal=True):
        super().__init__(message)
        self.fatal = fatal


def dhan_post(path, body, retries=3):
    req = urllib.request.Request(
        API + path, data=json.dumps(body).encode(), method="POST",
        headers={"access-token": TOKEN, "client-id": CLIENT_ID,
                 "Content-Type": "application/json", "Accept": "application/json"})
    for attempt in range(retries):
        try:
            with urllib.request.urlopen(req, timeout=20) as r:
                return json.loads(r.read())
        except urllib.error.HTTPError as e:
            text = e.read().decode(errors="replace")
            if e.code == 429 or "DH-904" in text:          # rate limited
                time.sleep(2 * (attempt + 1))
                continue
            if e.code in (401, 403) or "DH-901" in text:
                raise DhanError("Dhan rejected the access token (it may have expired - "
                                "tokens from web.dhan.co last 24 hours). Click Update token "
                                "and paste a new one.") from None
            if "DH-902" in text:
                raise DhanError("This Dhan account has no Data API subscription. "
                                "Historical candles need the Data API plan.") from None
            raise DhanError(f"Dhan error {e.code}: {text[:200]}", fatal=False) from None
        except urllib.error.URLError as e:
            if attempt == retries - 1:
                raise DhanError(f"Can't reach Dhan: {e.reason}") from None
            time.sleep(2)
    raise DhanError("Dhan kept rate-limiting the requests")


MASTER_LOCK = threading.Lock()


def scrip_master():
    """Path of Dhan's instrument list, downloaded again when older than 20 hours."""
    SETTINGS_DIR.mkdir(parents=True, exist_ok=True)
    cache = SETTINGS_DIR / "scrip-master.csv"
    with MASTER_LOCK:                                  # the F&O and bullion scans both read it
        fresh = cache.exists() and time.time() - cache.stat().st_mtime < 20 * 3600
        if not fresh:
            try:
                with urllib.request.urlopen(SCRIP_MASTER, timeout=60) as r:
                    tmp = cache.with_suffix(".tmp")
                    tmp.write_bytes(r.read())
                    os.replace(tmp, cache)
            except (urllib.error.URLError, TimeoutError) as e:
                if not cache.exists():
                    raise DhanError("Couldn't download Dhan's list of instruments. Check your internet "
                                    f"connection and press Scan now. ({getattr(e, 'reason', e)})") from None
    return cache


def fno_stocks():
    """{symbol: NSE equity security id} for every stock with NSE stock futures."""
    cache = scrip_master()
    f = open(cache, encoding="utf-8", errors="replace", newline="")   # streamed: the file is ~30 MB
    rows = csv.DictReader(f)
    underlyings, equity, futures, names = set(), {}, {}, {}
    opts, lots, comm = {}, {}, {}
    today = datetime.now(IST).strftime("%Y-%m-%d")
    for row in rows:
        exch, inst = row.get("SEM_EXM_EXCH_ID"), row.get("SEM_INSTRUMENT_NAME")
        sym = row.get("SEM_TRADING_SYMBOL", "")
        if (exch == "MCX" and inst == "FUTCOM" and sym.startswith("CRUDEOIL-")) or \
                (exch == "NSE" and inst == "FUTCUR" and sym.startswith("USDINR-")):
            key = "CRUDE" if exch == "MCX" else "USDINR"
            expiry = (row.get("SEM_EXPIRY_DATE") or "")[:10]
            if expiry > today and (key not in comm or expiry < comm[key][0]):
                comm[key] = (expiry, row["SEM_SMST_SECURITY_ID"], inst)
            continue
        if exch != "NSE":
            continue
        if inst == "OPTSTK":                           # stock options: strikes, expiries, lot size
            expiry = (row.get("SEM_EXPIRY_DATE") or "")[:10]
            kind = row.get("SEM_OPTION_TYPE")
            if expiry < today or kind not in ("CE", "PE"):
                continue
            under = sym.rsplit("-", 3)[0]
            try:
                strike = float(row.get("SEM_STRIKE_PRICE") or 0)
                lots[under] = int(float(row.get("SEM_LOT_UNITS") or 0)) or lots.get(under)
            except ValueError:
                continue
            opts.setdefault(under, {}).setdefault(expiry, {})[(strike, kind)] = row["SEM_SMST_SECURITY_ID"]
            continue
        if inst == "FUTSTK":
            under = sym.rsplit("-", 2)[0]
            underlyings.add(under)
            expiry = (row.get("SEM_EXPIRY_DATE") or "")[:10]
            if expiry >= today and (under not in futures or expiry < futures[under][0]):
                futures[under] = (expiry, row["SEM_SMST_SECURITY_ID"])   # nearest expiry
        elif row.get("SEM_SEGMENT") == "E" and row.get("SEM_SERIES") == "EQ":
            equity[sym] = row["SEM_SMST_SECURITY_ID"]
            names[sym] = (row.get("SM_SYMBOL_NAME") or row.get("SEM_CUSTOM_SYMBOL") or sym).strip()
        elif row.get("SEM_INSTRUMENT_NAME") == "INDEX":
            key = sym.upper().replace(" ", "")
            if key in ("NIFTY", "NIFTY50", "BANKNIFTY", "NIFTYBANK", "INDIAVIX"):
                INDEXES[{"NIFTY50": "NIFTY", "NIFTYBANK": "BANKNIFTY"}.get(key, key)] = row["SEM_SMST_SECURITY_ID"]
        elif row.get("SEM_INSTRUMENT_NAME") == "OPTIDX" and sym.split("-")[0] == "NIFTY":
            expiry = (row.get("SEM_EXPIRY_DATE") or "")[:10]
            if expiry >= today and (not EXPIRY.get("nifty") or expiry < EXPIRY["nifty"]):
                EXPIRY["nifty"] = expiry
    f.close()
    stocks = {s: equity[s] for s in sorted(underlyings) if s in equity}
    if ONLY:
        stocks = {s: i for s, i in stocks.items() if s in ONLY}
    if not stocks:
        raise DhanError("Couldn't find F&O stocks in Dhan's scrip master file.")
    FUTURES.clear()
    FUTURES.update({u: fid for u, (_, fid) in futures.items()})
    OPTIONS.clear()
    OPTIONS.update(opts)
    LOTS.update({u: l for u, l in lots.items() if l})
    NAMES.update(names)
    COMMODITY.update({k: (sid, inst) for k, (_, sid, inst) in comm.items()})
    return stocks


FUTURES = {}                                           # symbol -> current-month futures security id
# Dhan security ids of the indices (IDX_I); read from the scrip master, these are fallbacks.
INDEXES = {"NIFTY": "13", "BANKNIFTY": "25", "INDIAVIX": "21"}
EXPIRY = {}                                            # "nifty" -> nearest NIFTY option expiry, YYYY-MM-DD
OPTIONS = {}                                           # symbol -> {expiry: {(strike, "CE"/"PE"): security id}}
LOTS = {}                                              # symbol -> F&O lot size
NAMES = {}                                             # symbol -> company name (for its news)
COMMODITY = {}                                         # "CRUDE" / "USDINR" -> (nearest futures id, instrument)


def _rows(d):
    keys = ("timestamp", "open", "high", "low", "close", "volume")
    if not all(isinstance(d.get(k), list) for k in keys):
        return []
    rows = [{"t": int(t), "o": o, "h": h, "l": l, "c": c, "v": v}
            for t, o, h, l, c, v in zip(*(d[k] for k in keys))]
    if isinstance(d.get("open_interest"), list) and len(d["open_interest"]) == len(rows):
        for r, oi in zip(rows, d["open_interest"]):
            r["oi"] = oi
    return rows


def dhan_futures_oi(fut_id):
    """15-minute futures candles with open interest, last few days."""
    now = datetime.now(IST)
    body = {"securityId": str(fut_id), "exchangeSegment": "NSE_FNO", "instrument": "FUTSTK",
            "interval": "15", "oi": True,
            "fromDate": (now - timedelta(days=DAYS)).strftime("%Y-%m-%d 09:15:00"),
            "toDate": now.strftime("%Y-%m-%d %H:%M:%S")}
    return _rows(dhan_post("/charts/intraday", body))


def dhan_daily(security_id, segment="NSE_EQ", instrument="EQUITY", days=120):
    """Daily candles, about 4 months by default (for the HTF key levels)."""
    now = datetime.now(IST)
    body = {"securityId": str(security_id), "exchangeSegment": segment, "instrument": instrument,
            "expiryCode": 0, "oi": False,
            "fromDate": (now - timedelta(days=days)).strftime("%Y-%m-%d"),
            "toDate": (now + timedelta(days=1)).strftime("%Y-%m-%d")}
    return _rows(dhan_post("/charts/historical", body))


def dhan_range(security_id, start, end, interval="5", segment="NSE_EQ", instrument="EQUITY"):
    """Intraday candles between two dates (inclusive), for the backtest."""
    body = {"securityId": str(security_id), "exchangeSegment": segment, "instrument": instrument,
            "interval": str(interval), "oi": False,
            "fromDate": start.strftime("%Y-%m-%d 09:15:00"), "toDate": end.strftime("%Y-%m-%d 15:30:00")}
    return _rows(dhan_post("/charts/intraday", body))


def dhan_candles(security_id, interval=TIMEFRAME, segment="NSE_EQ", instrument="EQUITY"):
    now = datetime.now(IST)
    body = {"securityId": str(security_id), "exchangeSegment": segment,
            "instrument": instrument, "interval": str(interval), "oi": False,
            "fromDate": (now - timedelta(days=DAYS)).strftime("%Y-%m-%d 09:15:00"),
            "toDate": now.strftime("%Y-%m-%d %H:%M:%S")}
    return _rows(dhan_post("/charts/intraday", body))


# ── Demo data (no keys) ─────────────────────────────────────────────────────
DEMO_SYMBOLS = sorted(picks.SECTOR_OF)                 # the whole F&O list, with made-up prices


def demo_candles(symbol):
    rng = random.Random(symbol + datetime.now(IST).strftime("%Y%m%d%H") + str(int(time.time() // 300)))
    price = rng.uniform(200, 4000)
    step = int(TIMEFRAME) * 60
    start = datetime.now(IST).replace(hour=9, minute=15, second=0, microsecond=0) - timedelta(days=DAYS - 1)
    out, drift = [], 0.0
    t = int(start.timestamp())
    for day in range(DAYS):
        for _ in range(375 // int(TIMEFRAME)):
            if rng.random() < 0.04:
                drift = rng.uniform(-0.0012, 0.0012)
            o = price
            c = o * (1 + drift + rng.gauss(0, 0.0016))
            h = max(o, c) * (1 + abs(rng.gauss(0, 0.0012)))
            l = min(o, c) * (1 - abs(rng.gauss(0, 0.0012)))
            out.append({"t": t, "o": round(o, 2), "h": round(h, 2), "l": round(l, 2),
                        "c": round(c, 2), "v": int(rng.uniform(5e4, 3e5) * (1 + abs(drift) * 900))})
            price, t = c, t + step
        t = int((start + timedelta(days=day + 1)).timestamp())
    return out


def demo_oi(symbol, pct):
    rng = random.Random(symbol + datetime.now(IST).strftime("%Y%m%d%H"))
    doi = rng.gauss(4 if rng.random() < 0.7 else -3, 3)  # OI usually rises with a trend
    kind = ("LONG BUILD-UP" if doi > 0 else "SHORT COVERING") if pct > 0 else \
           ("SHORT BUILD-UP" if doi > 0 else "LONG UNWINDING")
    return {"kind": kind, "oi_pct": round(doi, 2), "price_pct": round(pct, 2)}


def demo_daily(symbol, intraday):
    """Made-up daily candles ending just before the demo intraday data."""
    rng = random.Random(symbol + "daily")
    first = intraday[0]
    price, out = first["o"], []
    demo_vol = int(rng.uniform(1e5, 1.5e7) / max(1, first["o"] / 1000))   # some demo stocks trade thin
    day0 = (first["t"] + 19800) // 86400
    for back in range(1, 100):
        day = day0 - back
        if (day + 3) % 7 >= 5:                        # skip Sat/Sun (1970-01-01 was a Thursday)
            continue
        c = price
        o = c * (1 + rng.gauss(0, 0.012))
        h = max(o, c) * (1 + abs(rng.gauss(0, 0.006)))
        l = min(o, c) * (1 - abs(rng.gauss(0, 0.006)))
        out.append({"t": day * 86400 - 19800, "o": o, "h": h, "l": l, "c": c, "v": demo_vol})
        price = o
    out.reverse()
    by_day = {}
    for k in intraday:                                # the intraday days themselves
        by_day.setdefault((k["t"] + 19800) // 86400, []).append(k)
    for day, ks in sorted(by_day.items()):
        out.append({"t": day * 86400 - 19800, "o": ks[0]["o"], "h": max(k["h"] for k in ks),
                    "l": min(k["l"] for k in ks), "c": ks[-1]["c"], "v": sum(k["v"] for k in ks)})
    return out


# ── Scanner ─────────────────────────────────────────────────────────────────
STATE = {"status": "starting", "message": "Starting first scan…", "demo": DEMO,
         "timeframe": TIMEFRAME, "scanned": 0, "total": 0, "last_scan": None,
         "breakout_tf": BREAKOUT_TF, "scan_every": SCAN_EVERY, "min_rr": MIN_RR, "next_scan": None,
         "version": VERSION, "market": None, "trades": {"rows": [], "summary": {}},
         "btst": {"open": False, "from": "12:00", "picks": [], "book": {"rows": [], "summary": {}}},
         "schedule": "Breakout scans every 15 min 9:30–2:00 · BTST from 11:00, final at 3:00",
         "bullion": None, "bullish": [], "bearish": [], "scores": [],
         "breakouts": {"bullish": [], "bearish": [], "scores": []},
         "picks": {"funnel": [], "flow": [], "sectors": [], "picks": [], "watch": []}}
CANDLES = {}
BREAKOUTS = {}                                         # symbol -> (15m candles, setup, levels, result)
PICKS = {}                                             # symbol -> pick (with its 5m candles)
PERF_RESET = "2026-10-28-breakouts"                   # bump to start the performance record afresh


def reset_performance():
    """Once per PERF_RESET: move every recorded trade out of the performance record (into
    old-trades/<reset>/), so the new setup's results aren't mixed with the old one's."""
    if SETTINGS.get("perf_reset") == PERF_RESET:
        return
    old = SETTINGS_DIR / "old-trades" / PERF_RESET
    try:
        files = list(SETTINGS_DIR.glob("trades-*.json")) + [f for f in [SETTINGS_DIR / "btst.json"] if f.exists()]
        if files:
            old.mkdir(parents=True, exist_ok=True)
            for f in files:
                f.replace(old / f.name)
        save_settings(perf_reset=PERF_RESET)
        SETTINGS["perf_reset"] = PERF_RESET
        print(f"Performance reset: {len(files)} old trade files moved to {old}")
    except OSError as e:
        print(f"Performance reset failed: {e}", file=sys.stderr)


reset_performance()
TRADES = trades.TradeBook(SETTINGS_DIR)                # today's locked-in picks, followed to the end
BTSTS = trades.BtstBook(SETTINGS_DIR)                  # BTST/STBT picks, followed into the next session
BTST_PICKS = {}                                        # symbol -> (BTST pick, 5m candles) for the chart
PICK_SINCE = {}                                        # (day, symbol, side) -> first time it was picked
OPTION_PLANS = {}                                      # (day, symbol, side) -> option plan, made when first picked
STOCK_IDS = {}                                         # symbol -> NSE equity security id (last scan)
DAILY = {}                                             # security id -> (IST day fetched, candles)
LOCK = threading.Lock()
WAKE = threading.Event()


def market_open(now=None):
    now = now or datetime.now(IST)
    if now.weekday() >= 5:
        return False
    return (9, 15) <= (now.hour, now.minute) <= (15, 35)


def set_state(**kw):
    with LOCK:
        STATE.update(kw)


def liquidity(daily):
    """Average daily turnover (₹ crore) and volume (shares) over the last LIQ_DAYS finished sessions."""
    today = analysis._day(time.time())
    past = [d for d in daily if analysis._day(d["t"]) < today][-LIQ_DAYS:]
    if len(past) < 5:
        return None                                    # too new to judge: keep it
    return {"turnover_cr": round(sum(d["c"] * d["v"] for d in past) / len(past) / 1e7, 1),
            "volume": int(sum(d["v"] for d in past) / len(past))}


def liq_limits():
    """(minimum turnover in ₹ crore, minimum volume in lakh shares) from Settings; 0 switches one off."""
    t, v = SETTINGS.get("min_turnover_cr"), SETTINGS.get("min_volume_lakh")
    return (float(MIN_TURNOVER_CR if t is None else t), float(MIN_VOLUME_LAKH if v is None else v))


def liquid_enough(liq):
    if not liq:
        return True
    min_t, min_v = liq_limits()
    return liq["turnover_cr"] >= min_t and liq["volume"] >= min_v * 1e5


def fetch_stock(sym, sid, demo, keep=False):
    """Order-block candles, 15-minute breakout candles and daily candles for one stock.
    Returns (None, None, daily) for a stock that doesn't trade enough (unless keep is set)."""
    if demo:
        candles = demo_candles(sym)
        daily = demo_daily(sym, candles)
        if not keep and not liquid_enough(liquidity(daily)):
            return None, None, daily
        return candles, breakout.resample(candles, BREAKOUT_TF), daily
    today = datetime.now(IST).date()
    cached = DAILY.get(sid)
    if not cached or cached[0] != today:             # daily levels only change once a day
        DAILY[sid] = (today, dhan_daily(sid))
        time.sleep(0.25)
    if not keep and not liquid_enough(liquidity(DAILY[sid][1])):
        return None, None, DAILY[sid][1]             # thin stock: skip the intraday calls
    candles = dhan_candles(sid)
    time.sleep(0.25)                                 # Dhan data API: 5 requests a second
    tf = int(TIMEFRAME)
    if tf <= BREAKOUT_TF and BREAKOUT_TF % tf == 0:
        c15 = breakout.resample(candles, BREAKOUT_TF)  # build 15m from the same data, no extra call
    else:
        c15 = breakout.resample(dhan_candles(sid, BREAKOUT_TF), BREAKOUT_TF)
        time.sleep(0.25)
    return candles, c15, DAILY[sid][1]


def fetch_market(demo):
    """5-minute candles for NIFTY, BANK NIFTY and India VIX (plus VIX daily, once a day)."""
    out = {}
    for key in ("NIFTY", "BANKNIFTY", "INDIAVIX"):
        try:
            if demo:
                c = demo_candles(key)
                if key == "INDIAVIX":                  # make the demo VIX look like a VIX
                    f = 14 / c[0]["o"]
                    c = [dict(x, o=x["o"] * f, h=x["h"] * f, l=x["l"] * f, c=x["c"] * f) for x in c]
                out[key] = (c, demo_daily(key, c) if key == "INDIAVIX" else None)
                continue
            sid = INDEXES[key]
            c = dhan_candles(sid, "5", "IDX_I", "INDEX")
            time.sleep(0.25)
            daily = None
            if key == "INDIAVIX":
                today = datetime.now(IST).date()
                cached = DAILY.get("IDX" + sid)
                if not cached or cached[0] != today:
                    DAILY["IDX" + sid] = (today, dhan_daily(sid, "IDX_I", "INDEX"))
                    time.sleep(0.25)
                daily = DAILY["IDX" + sid][1]
            out[key] = (c, daily)
        except DhanError as e:
            if e.fatal:
                raise
            print(f"  {key}: {e}", file=sys.stderr)
    return out


def fetch_events(demo, vix):
    """Scheduled events, crude oil, USD/INR, risk headlines and the results calendar."""
    today = datetime.now(IST).date()
    cal = events.calendar(today)
    comm = {}
    for key, name, seg in (("CRUDE", "Crude oil (MCX)", "MCX_COMM"), ("USDINR", "USD/INR", "NSE_CURRENCY")):
        try:
            if demo:
                c = demo_candles(key)
                f = (6000 if key == "CRUDE" else 88) / c[0]["o"]
                c = [dict(x, o=x["o"] * f, h=x["h"] * f, l=x["l"] * f, c=x["c"] * f) for x in c]
            elif key in COMMODITY:
                sid, inst = COMMODITY[key]
                c = dhan_candles(sid, "5", seg, inst)
                time.sleep(0.25)
            else:
                continue
            today_c, before = market.session(c)
            comm[key] = events.commodity_view(name, today_c or c, before[-1]["c"] if before else None)
        except Exception as e:                         # optional: never stop the scan for it
            print(f"  {key}: {e}", file=sys.stderr)
    news = events.headlines()
    results = events.results_calendar()
    if demo and not results:                           # show what a results warning looks like
        results = {s: (today + timedelta(days=1 + i % 3)).isoformat() for i, s in enumerate(DEMO_SYMBOLS[::25])}
    risk = events.risk_level(news, cal, vix, comm.get("CRUDE"))
    return {"calendar": cal, "crude": comm.get("CRUDE"), "usdinr": comm.get("USDINR"),
            "news": news, "results": results, "risk": risk}


_last_chain = [0.0]


def option_chain(sid, expiry, segs=("NSE_EQ", "NSE_FNO")):
    """Dhan option chain for one stock (or index) and expiry (1 request every 3 seconds)."""
    for seg in segs:
        wait = 3.1 - (time.time() - _last_chain[0])
        if wait > 0:
            time.sleep(wait)
        _last_chain[0] = time.time()
        try:
            d = dhan_post("/optionchain", {"UnderlyingScrip": int(sid), "UnderlyingSeg": seg, "Expiry": expiry}, retries=2)
        except DhanError:
            continue
        data = d.get("data") if isinstance(d, dict) else None
        if isinstance(data, dict) and data.get("oc"):
            return data
    return None


GEX_CACHE = {}


def nifty_gex(demo, spot):
    """NIFTY dealer gamma exposure from the nearest-expiry option chain (refreshed every 5 minutes)."""
    if not spot:
        return None
    hit = GEX_CACHE.get("nifty")
    if hit and time.time() - hit[0] < 300:
        return hit[1]
    try:
        if demo:
            chain = gex.demo_chain(spot)
        else:
            expiry = EXPIRY.get("nifty")
            chain = option_chain(INDEXES["NIFTY"], expiry, ("IDX_I",)) if expiry else None
        days = (datetime.strptime(EXPIRY["nifty"], "%Y-%m-%d").date() - datetime.now(IST).date()).days \
            if EXPIRY.get("nifty") else 5
        g = gex.analyse(chain, float((chain or {}).get("last_price") or spot), days=max(days, 0) + 0.5) if chain else None
        if g:
            g["expiry"] = EXPIRY.get("nifty")
    except Exception as e:                             # extra information: never stop the scan
        print(f"  NIFTY GEX: {e}", file=sys.stderr)
        g = hit[1] if hit else None
    GEX_CACHE["nifty"] = (time.time(), g)
    return g


def option_plan(sym, sid, side, plan, spot, demo):
    """Call (buy) or put (sell) to trade instead of the stock, with entry/SL/targets."""
    kind = "CE" if side == "bull" else "PE"
    today = datetime.now(IST).date()
    try:
        if demo:
            exps, d0 = [], today.replace(day=1)
            for m in range(3):                         # last Tuesday of this and the next two months
                first = (d0 + timedelta(days=32 * m)).replace(day=1)
                last = (first + timedelta(days=32)).replace(day=1) - timedelta(days=1)
                exps.append((last - timedelta(days=(last.weekday() - 1) % 7)).isoformat())
            expiry, rolled = options.choose_expiry(exps, today)
            step = options.demo_strike_step(spot)
            strikes = [round(spot / step) * step + i * step for i in range(-5, 6)]
            strike = options.strikes_near(strikes, plan["entry"], kind)[0]
            days = (datetime.fromisoformat(expiry).date() - today).days
            iv = 28 + (hash(sym) % 12)
            ltp, delta, gamma = options.bs(spot, strike, days, iv, kind)
            opt = {"ltp": ltp, "delta": delta, "gamma": gamma, "iv": iv, "oi": 120000, "volume": 5000, "spread_pct": 1.2}
            lot = max(1, round(600000 / spot / 25) * 25)
            return options.plan(kind, opt, spot, plan, lot, expiry, rolled, strike)
        chains = OPTIONS.get(sym)
        if not chains:
            return None
        expiry, rolled = options.choose_expiry(list(chains), today)
        if not expiry:
            return None
        strikes = sorted({k for (k, t) in chains[expiry] if t == kind})
        near = options.strikes_near(strikes, plan["entry"], kind)
        data = option_chain(sid, expiry)
        if not data:
            return None
        spot = float(data.get("last_price") or spot)
        got = [(k, options.from_chain(data, kind, k)) for k in near]
        got = [(k, o) for k, o in got if o]
        best = next(((k, o) for k, o in got if options.usable(o)), got[0] if got else None)
        if not best:
            return None
        return options.plan(kind, best[1], spot, plan, LOTS.get(sym), expiry, rolled, best[0])
    except Exception as e:                             # options are extra: never stop the scan
        print(f"  {sym} option: {e}", file=sys.stderr)
        return None


def add_event_info(c, ev, demo, news=True):
    """Event / crude / rupee / results flags and the latest company news on one pick."""
    today = datetime.now(IST).date()
    c["flags"] = events.stock_flags(c["symbol"], picks.sector_of(c["symbol"]), c["side"], ev.get("crude"),
                                    ev.get("usdinr"), ev.get("calendar", []), ev.get("results", {}).get(c["symbol"]), today)
    if news and not demo:
        c["news"] = events.stock_news(NAMES.get(c["symbol"], c["symbol"]), c["symbol"])


def intraday_list(fresh=()):
    """Every intraday pick given today, once each (newest first), with its live trade status."""
    out = []
    for r in sorted(TRADES.rows, key=lambda r: -r["picked_at"]):
        c = dict(r.get("card") or {"symbol": r["symbol"], "side": r["side"], "plan": {}})
        c["plan"] = dict(c.get("plan") or {}, entry=r["entry"], sl=r["sl"], t1=r["t1"], t2=r["t2"])
        c["trade"] = {k: r.get(k) for k in ("status", "result_r", "revised", "t2_now", "last", "triggered_at", "exit_at")}
        c["since"] = datetime.fromtimestamp(r["picked_at"], IST).strftime("%H:%M")
        c["option"] = r.get("option") or c.get("option")
        if r.get("last") is not None:
            c["price"] = r["last"]
        c["fresh"] = r["symbol"] in fresh
        out.append(c)
    return out


# ── MCX gold & silver (positional, daily candles) ───────────────────────────
BULLION_EVERY = 30                                     # minutes between refreshes while MCX is open
BULLION_WAKE = threading.Event()
BULLION_CANDLES = {}                                   # "GOLD" / "SILVER" -> daily candles of the near contract
BULLION_SYMBOLS = {s for rows in bullion.CONTRACTS.values() for s, _, _ in rows}


def mcx_contracts():
    """{symbol: [(expiry, security id)]} for MCX gold and silver futures still trading, and the
    USD/INR futures id."""
    today = datetime.now(IST).strftime("%Y-%m-%d")
    out, usd = {}, None
    with open(scrip_master(), encoding="utf-8", errors="replace", newline="") as f:
        for row in csv.DictReader(f):
            exch, inst = row.get("SEM_EXM_EXCH_ID"), row.get("SEM_INSTRUMENT_NAME")
            sym = row.get("SEM_TRADING_SYMBOL", "")
            expiry = (row.get("SEM_EXPIRY_DATE") or "")[:10]
            if expiry <= today:
                continue
            if exch == "MCX" and inst == "FUTCOM" and sym.split("-")[0] in BULLION_SYMBOLS:
                out.setdefault(sym.split("-")[0], []).append((expiry, row["SEM_SMST_SECURITY_ID"]))
            elif exch == "NSE" and inst == "FUTCUR" and sym.startswith("USDINR-") and (not usd or expiry < usd[0]):
                usd = (expiry, row["SEM_SMST_SECURITY_ID"])
    return out, usd[1] if usd else None


def demo_bullion(metal):
    """Two years of made-up daily candles: gold trending up, silver up but stretched."""
    rng = random.Random(metal + "bullion")
    price, drift, vol = (72000, .0009, .009) if metal == "GOLD" else (82000, .0011, .015)
    if metal == "USDINR":
        price, drift, vol = 84.0, .0001, .002
    day, out = (int(time.time()) + 19800) // 86400 - 730, []
    while day <= (int(time.time()) + 19800) // 86400 - 1:
        day += 1
        if (day + 3) % 7 >= 5:
            continue
        o = price
        c = o * (1 + drift + rng.gauss(0, vol))
        out.append({"t": day * 86400 - 19800, "o": o, "h": max(o, c) * (1 + abs(rng.gauss(0, vol / 2))),
                    "l": min(o, c) * (1 - abs(rng.gauss(0, vol / 2))), "c": c, "v": int(rng.uniform(5e3, 2e4))})
        price = c
    return out


def mcx_open(now=None):
    now = now or datetime.now(IST)
    return now.weekday() < 5 and (9, 0) <= (now.hour, now.minute) <= (23, 30)


def bullion_once():
    demo, today = DEMO, datetime.now(IST).strftime("%Y-%m-%d")
    if demo:
        contracts = {s: [(_add_days(today, 38), s + "1"), (_add_days(today, 98), s + "2")]
                     for s in BULLION_SYMBOLS}
        daily = {m: demo_bullion(m) for m in ("GOLD", "SILVER", "USDINR")}
    else:
        contracts, usd_id = mcx_contracts()
        daily = {}
        for metal in ("GOLD", "SILVER"):
            rows = sorted(contracts.get(metal, []))
            if not rows:
                raise DhanError(f"No {metal} futures found in Dhan's MCX contract list.", fatal=False)
            daily[metal] = dhan_daily(rows[0][1], "MCX_COMM", "FUTCOM", days=730)
            time.sleep(0.25)
        try:
            daily["USDINR"] = dhan_daily(usd_id, "NSE_CURRENCY", "FUTCUR", days=150) if usd_id else []
        except DhanError as e:
            if e.fatal:
                raise
            daily["USDINR"] = []
    metals = []
    for metal in ("GOLD", "SILVER"):
        r = bullion.screen(metal, daily[metal])
        if r:
            metals.append(r)
            with LOCK:
                BULLION_CANDLES[metal] = daily[metal]
    view = {"metals": metals, "ratio": bullion.ratio_view(daily["GOLD"], daily["SILVER"]),
            "usdinr": bullion.usdinr_view(daily.get("USDINR") or []),
            "contracts": bullion.contract_plan({s: contracts.get(s, []) for s in sorted(BULLION_SYMBOLS)}, today),
            "updated": datetime.now(IST).strftime("%d %b %H:%M"), "error": None}
    if not metals:
        view["error"] = "Dhan returned too little daily history for gold and silver futures."
    set_state(bullion=view)


def _add_days(d, n):
    return (datetime.strptime(d, "%Y-%m-%d") + timedelta(days=n)).strftime("%Y-%m-%d")


def bullion_loop():
    while True:
        try:
            bullion_once()
        except DhanError as e:
            set_state(bullion=dict(STATE.get("bullion") or {}, error=str(e)))
        except Exception as e:
            set_state(bullion=dict(STATE.get("bullion") or {}, error=f"Gold & silver scan failed: {e}"))
        wait = BULLION_EVERY * 60 if mcx_open() or DEMO else 3 * 3600
        BULLION_WAKE.wait(wait)
        BULLION_WAKE.clear()


def bullion_payload(symbol):
    with LOCK:
        candles = BULLION_CANDLES.get(symbol)
        m = next((x for x in (STATE.get("bullion") or {}).get("metals", []) if x["symbol"] == symbol), None)
    if not candles or not m:
        return None
    return {"symbol": symbol, "price": m["price"], "pct": m["pct"], "side": "bull", "bullion": m,
            "candles": [{k: x[k] for k in "tohlcv"} for x in candles[-180:]]}


BACKTEST = {"status": "idle", "message": ""}
BACKTEST_SESSIONS = 60


def _history(sid, segment="NSE_EQ", instrument="EQUITY", days=90):
    """5-minute candles for the last `days` calendar days before today, in 30-day pieces."""
    today = datetime.now(IST).date()
    out, end = {}, today - timedelta(days=1)
    start = today - timedelta(days=days)
    while end >= start:
        s0 = max(start, end - timedelta(days=29))
        for c in dhan_range(sid, s0, end, "5", segment, instrument):
            out[c["t"]] = c
        time.sleep(0.6 if market_open() else 0.25)    # gentle while the live scanner is running
        end = s0 - timedelta(days=1)
    return [out[t] for t in sorted(out)]


def load_history(demo):
    """~3 months of 5-minute history for the liquid F&O stocks + NIFTY. Kept on disk for the day,
    so comparing rule variants or re-running doesn't download it again."""
    if demo:
        BACKTEST.update(message="Making demo history…")
        return backtest.demo_data(DEMO_SYMBOLS[:150], sessions=BACKTEST_SESSIONS + 5)
    cache = SETTINGS_DIR / "history.pkl"
    today = datetime.now(IST).strftime("%Y-%m-%d")
    try:
        with open(cache, "rb") as f:
            saved = pickle.load(f)                     # our own file, written below
        if saved.get("made") == today:
            return saved["data"], saved["nifty"]
    except (OSError, ValueError, EOFError, pickle.UnpicklingError, AttributeError, KeyError):
        pass
        stocks = fno_stocks()
        data = {}
        for n, (sym, sid) in enumerate(stocks.items(), 1):
            BACKTEST.update(message=f"Downloading history {n}/{len(stocks)}: {sym}…")
            try:
                daily = dhan_daily(sid, days=230)
                time.sleep(0.25)
                if not liquid_enough(liquidity(daily)):
                    continue
                data[sym] = {"c5": backtest.pack(_history(sid)), "daily": daily}   # compact: ~48 B a candle
            except DhanError as e:
                if e.fatal:
                    raise
        BACKTEST.update(message="Downloading NIFTY history…")
        nifty = _history(INDEXES["NIFTY"], "IDX_I", "INDEX")
    try:
        with open(cache, "wb") as f:
            pickle.dump({"made": today, "data": data, "nifty": nifty}, f)
    except OSError:
        pass
    return data, nifty


OPEN_VOL = {}                                          # security id -> (day, {setup: {IST day: opening volume}})


def opening_volumes(sym, sid, setup, demo):
    """Opening-range volume of the last sessions (for "in play"), downloaded once a day per stock."""
    today = datetime.now(IST).strftime("%Y-%m-%d")
    hit = OPEN_VOL.get(sid)
    if not hit or hit[0] != today:
        if demo:
            c5 = demo_candles(sym)
        else:
            c5 = dhan_range(sid, datetime.now(IST).date() - timedelta(days=30), datetime.now(IST).date() - timedelta(days=1))
            time.sleep(0.25)
        hit = (today, {s: orb.opening_volumes(c5, kk) for s, kk in orb.ORB_MINUTES.items()})
        OPEN_VOL[sid] = hit
    return hit[1][setup]


def orb_picks(setup, results, candles_by, daily_by, demo):
    """Opening range breakout on stocks in play: picked once, right after the opening range closes."""
    k = orb.ORB_MINUTES[setup]
    rl = live_rules().get("intraday") or {}
    rcfg = risk.config(SETTINGS)
    now_hm = datetime.now(IST).strftime("%H:%M")
    cands = []
    in_window = demo or (f"09:{15 + 5 * k:02d}" <= now_hm <= "10:00")   # decided only on the first scans
    if in_window:
        for sym, r in results.items():
            today5, _ = market.session(candles_by[sym])
            if len(today5) < k:
                continue
            try:
                vols = opening_volumes(sym, STOCK_IDS.get(sym, sym), setup, demo)
            except DhanError as e:
                if e.fatal:
                    raise
                continue
            day = analysis._day(today5[0]["t"])
            if demo:                                   # demo history is short: average what there is
                prev = [v for d, v in vols.items() if d < day]
                usual = sum(prev) / len(prev) if prev else None
            else:
                usual = orb.usual_opening(vols, day)
            daily = [d for d in daily_by.get(sym, []) if analysis._day(d["t"]) < day]
            c = orb.candidate(sym, today5[:k], usual, orb.daily_atr(daily), setup, rl.get("orb_stop", "atr10"),
                              picks.sector_of(sym))
            if c:
                c.update(price=r["price"], pct=r["pct"], sector_pct=0, sector_rank=0, sources=[c["plan"]["basis"]])
                cands.append(c)
    chosen = orb.rank(cands, min(N_PICKS, rcfg["max_open"]))
    names, picked = {c["symbol"] for c in cands}, {c["symbol"] for c in chosen}

    def finish(oi):
        flow = [{"symbol": s, "pct": r["pct"], "side": "bull" if r["pct"] >= 0 else "bear",
                 "stage": 2 if s in picked else 1 if s in names else 0} for s, r in results.items()]
        return {"funnel": [{"name": "Scanned", "count": len(results)}, {"name": "In play (RVOL ≥ 1)", "count": len(cands)},
                           {"name": "Intraday picks", "count": len(chosen)}],
                "flow": sorted(flow, key=lambda f: -f["pct"]),
                "sectors": picks.sector_table({s: r["pct"] for s, r in results.items()}),
                "picks": chosen, "watch": [], "picks_open": True, "kind": setup}
    return [], finish


def apply_best(cmp):
    """You asked Claude to decide: switch to the ⭐ variants from a comparison, unless you picked one yourself.
    No BTST variant with a tested edge -> BTST is marked paper only."""
    rules, names = dict(SETTINGS.get("rules") or {}), dict(SETTINGS.get("rules_names") or {})
    manual = SETTINGS.get("rules_manual") or {}
    btst_edge = True
    for kind in ("intraday", "btst"):
        star = next((r for r in cmp.get(kind) or [] if r.get("pick")), None)
        if kind == "btst" and not star:
            btst_edge = False
        if star and not manual.get(kind):
            rules[kind], names[kind] = star["rules"], star["name"]
    save_settings(rules=rules, rules_names=names, btst_edge=btst_edge, auto_applied=cmp.get("ran_at") or "yes")


def live_rules():
    """The rule variants chosen in Performance → Compare rules (empty = the default rules)."""
    return SETTINGS.get("rules") or {}


def backtest_job(compare=False):
    """Replay the intraday and BTST setups on ~3 months of history (or compare rule variants)."""
    try:
        demo = DEMO
        data, nifty = load_history(demo)
        if compare:
            cmp = backtest.compare(data, nifty, N_PICKS, BACKTEST_SESSIONS, progress=lambda m: BACKTEST.update(message=m),
                                   rcfg=risk.config(SETTINGS))
            cmp.update(ran_at=datetime.now(IST).strftime("%d %b %Y %H:%M"), demo=demo)
            (SETTINGS_DIR / "compare.json").write_text(json.dumps(cmp), encoding="utf-8")
            apply_best(cmp)
            BACKTEST.update(status="done", message="")
            return
        rep = backtest.run(data, nifty, N_PICKS, BACKTEST_SESSIONS, progress=lambda m: BACKTEST.update(message=m),
                           rcfg=risk.config(SETTINGS), rules=live_rules())
        rep.update(rules=SETTINGS.get("rules_names") or {}, ran_at=datetime.now(IST).strftime("%d %b %Y %H:%M"), demo=demo)
        (SETTINGS_DIR / "backtest.json").write_text(json.dumps(rep), encoding="utf-8")
        BACKTEST.update(status="done", message="")
    except DhanError as e:
        BACKTEST.update(status="error", message=str(e))
    except Exception as e:
        BACKTEST.update(status="error", message=f"Backtest failed: {e}")


def scan_once():
    demo = DEMO                                      # keys can be added mid-scan
    stocks = {s: s for s in DEMO_SYMBOLS} if demo else fno_stocks()
    STOCK_IDS.clear()
    STOCK_IDS.update(stocks)
    set_state(status="scanning", total=len(stocks), scanned=0,
              message=f"Scanning {len(stocks)} F&O stocks (liquid ones only)…")
    results, candles_by, bo, funnel_in, daily_by = {}, {}, {}, {}, {}
    thin, liq_of = [], {}
    # Stocks with a trade still being followed are always fetched, liquid or not.
    keep = {r["symbol"] for r in TRADES.rows} | {r["symbol"] for r in BTSTS.rows if r.get("result_r") is None}
    idx = fetch_market(demo)
    for n, (sym, sid) in enumerate(stocks.items(), 1):
        try:
            if demo != DEMO:
                return                                   # switched to live keys: stop this scan
            candles, c15, daily = fetch_stock(sym, sid, demo, keep=sym in keep)
        except DhanError as e:
            if e.fatal:
                raise
            print(f"  {sym}: {e}", file=sys.stderr)
            continue
        except Exception as e:                       # one bad stock shouldn't stop the scan
            print(f"  {sym}: {e}", file=sys.stderr)
            continue
        liq_of[sym] = liquidity(daily)
        if candles is None:
            thin.append(sym)
            set_state(scanned=n)
            continue
        res = analysis.analyse(candles, SWING)
        if res and sym in keep and not liquid_enough(liq_of[sym]):
            candles_by[sym] = candles                  # followed for its trade only, not scanned for new ones
            res = None
        if res:
            res["vwap"] = market.vwap(candles)
            res["rvol"] = market.rvol(candles, daily)
            res["liq"] = liq_of[sym]
            results[sym], candles_by[sym], daily_by[sym] = res, candles, daily
            now_ts = c15[-1]["end"] if demo else time.time()
            setup, levels = breakout.detect(c15, daily, now_ts, res["price"])
            if setup:
                bo[sym] = (c15, setup, levels, res)
            prev_days = [x for x in daily if analysis._day(x["t"]) < analysis._day(candles[-1]["t"])]
            funnel_in[sym] = {"res": res, "bo": setup, "levels": levels,
                              "pdh": prev_days[-1]["h"] if prev_days else None,
                              "pdl": prev_days[-1]["l"] if prev_days else None,
                              "pdc": prev_days[-1]["c"] if prev_days else None,
                              "c15": [c for c in c15 if c["end"] <= now_ts], "now": now_ts}
        set_state(scanned=n)

    def row(sym, side):
        r, ob = results[sym], results[sym][side]
        return {"symbol": sym, "price": r["price"], "pct": r["pct"], "score": ob["score"],
                "zone": [round(ob["bottom"], 2), round(ob["top"], 2)], "tests": ob["tests"]}

    def side_of(sym):                                # each stock goes on its stronger side
        b, s = results[sym]["bull"], results[sym]["bear"]
        if b and (not s or b["score"] >= s["score"]):
            return "bull"
        return "bear" if s else None

    sides = {s: side_of(s) for s in results}
    bull = sorted((s for s in results if sides[s] == "bull"), key=lambda s: -results[s]["bull"]["score"])
    bear = sorted((s for s in results if sides[s] == "bear"), key=lambda s: -results[s]["bear"]["score"])
    scores = [{"side": "bull", "score": results[s]["bull"]["score"]} for s in bull] + \
             [{"side": "bear", "score": results[s]["bear"]["score"]} for s in bear]
    # Intraday picks: funnel, then futures OI for the shortlisted stocks only.
    # Picks come from the 9:30, 9:45, 10:00 and 10:15 scans; after that the day's list is final.
    picks_from = SETTINGS.get("intraday_from") or INTRADAY_FROM
    clock = max((d["c15"][-1]["end"] for d in funnel_in.values() if d["c15"]), default=time.time())
    session_time = datetime.fromtimestamp(clock, IST).strftime("%H:%M")
    if demo and os.environ.get("OBS_DEMO_CLOCK"):     # testing: pretend it is this time of day
        session_time = os.environ["OBS_DEMO_CLOCK"]
    ev = fetch_events(demo, market.vix_view(*idx["INDIAVIX"]) if "INDIAVIX" in idx else None)
    nifty = market.index_view("NIFTY 50", idx["NIFTY"][0]) if "NIFTY" in idx else None
    # Intraday = 15-minute PDH/PDL breakouts on volume only (order blocks have their own tab).
    window_closed = session_time > INTRADAY_UNTIL and (not demo or os.environ.get("OBS_DEMO_CLOCK"))
    setup = (live_rules().get("intraday") or {}).get("setup", "breakout")
    if setup in orb.ORB_MINUTES:
        cands, finish = orb_picks(setup, results, candles_by, daily_by, demo)
    else:
        cands, finish = intraday.build(funnel_in, N_PICKS, picks_open=picks_from <= session_time and not window_closed,
                                       nifty_trend=nifty["trend"] if nifty else None, rules=live_rules().get("intraday"))
    oi = {}
    checks = cands[:OI_CHECKS]
    for n, c in enumerate(checks, 1):
        if demo != DEMO:
            return
        set_state(message=f"Checking futures OI for the shortlist… {n}/{len(checks)}")
        if demo:
            oi[c["symbol"]] = demo_oi(c["symbol"], c["pct"])
            continue
        fid = FUTURES.get(c["symbol"])
        if not fid:
            continue
        try:
            oi[c["symbol"]] = picks.oi_build(dhan_futures_oi(fid))
        except DhanError as e:
            if e.fatal:
                raise
        time.sleep(0.25)
    top = finish(oi)
    top["intraday_until"], top["picks_from"] = INTRADAY_UNTIL, picks_from
    if window_closed:
        top["window_closed"], top["watch"] = True, []
    # Each stock's real path today (every 15 minutes, % from yesterday's close) for the flow chart.
    for f in top["flow"]:
        cs, r = candles_by.get(f["symbol"]), results.get(f["symbol"])
        if not cs or not r:
            continue
        today = [c for c in cs if analysis._day(c["t"]) == analysis._day(cs[-1]["t"])]
        pc = r["prev_close"] or today[0]["o"]
        f["path"] = [round((c["c"] - pc) / pc * 100, 2) for c in today[2::3]]
        if not f["path"] or f["path"][-1] != r["pct"]:
            f["path"].append(r["pct"])
        f["price"], f["sector"] = r["price"], picks.sector_of(f["symbol"])
        o = oi.get(f["symbol"])
        if o:
            f["oi"] = f'{o["kind"]} · OI {o["oi_pct"]:+.2f}%'
    # Market context: index direction, VIX, breadth, movers, and how each pick sits against it.
    bank = market.index_view("BANK NIFTY", idx["BANKNIFTY"][0]) if "BANKNIFTY" in idx else None
    vix = market.vix_view(*idx["INDIAVIX"]) if "INDIAVIX" in idx else None
    br = market.breadth([r["pct"] for r in results.values()])
    mv = market.movers([{"symbol": s, "price": r["price"], "pct": r["pct"], "rvol": r.get("rvol"),
                         "sector": picks.sector_of(s)} for s, r in results.items()])
    today_s = datetime.now(IST).strftime("%Y-%m-%d")
    gx = nifty_gex(demo, nifty["price"] if nifty else None)
    mkt = {"nifty": nifty, "bank": bank, "vix": vix, "breadth": br, "movers": mv, "gex": gx,
           "verdict": market.verdict(nifty, bank, vix, br),
           "expiry": EXPIRY.get("nifty"), "expiry_today": EXPIRY.get("nifty") == today_s}
    for c in top["picks"] + [f for f in top["flow"] if f.get("stage")]:
        r = results.get(c["symbol"]) or {}
        is_bull = c["side"] == "bull"
        if r.get("vwap"):
            c["vwap"] = round(r["vwap"], 2)
            c["vwap_ok"] = (r["price"] >= r["vwap"]) if is_bull else (r["price"] <= r["vwap"])
        c["rvol"] = r.get("rvol")
        c["liq"] = r.get("liq")
        if gx:
            c["gex"] = gx["regime"]                    # recorded with the trade, for the Learning journal
        if nifty:
            c["market"] = ("with" if nifty["trend"] == ("Bullish" if is_bull else "Bearish")
                           else "neutral" if nifty["trend"] == "Sideways" else "against")
            c["rs"] = round(r.get("pct", 0) - nifty["pct"], 2)   # strength vs NIFTY today
    # BTST / STBT from 12:00: strong closers, checked against futures OI, followed into tomorrow.
    btst_from = "11:00"
    btst_open = demo or session_time >= btst_from
    btst_picks, skipped, btst_caution = [], [], ""
    if btst_open:
        sec_pct = {s: next((x["pct"] for x in top["sectors"] if x["name"] == picks.sector_of(s)), 0) for s in results}
        bc = btst.candidates(results, candles_by, daily_by, {s: d["levels"] for s, d in funnel_in.items()},
                             sec_pct, nifty["trend"] if nifty else "Sideways",
                             time.time() if not demo else max(cs[-1]["t"] + 300 for cs in candles_by.values()),
                             live_rules().get("btst"))
        boi = {}
        for c in bc[:15]:
            sym = c["symbol"]
            if sym in oi:
                boi[sym] = oi[sym]
            elif demo:
                boi[sym] = demo_oi(sym, c["pct"])
            elif FUTURES.get(sym):
                try:
                    boi[sym] = picks.oi_build(dhan_futures_oi(FUTURES[sym]))
                    time.sleep(0.25)
                except DhanError as e:
                    if e.fatal:
                        raise
        btst_picks = btst.finish(bc[:15], boi, 8, live_rules().get("btst"))
        # Events: no BTST into a company's results; fewer, stronger BTSTs before a big overnight event.
        nxt = events._next_session(datetime.now(IST).date()).isoformat()
        skipped = []
        for c in btst_picks:
            add_event_info(c, ev, demo, news=False)
            r_on = ev["results"].get(c["symbol"])
            if r_on and r_on <= nxt:
                skipped.append({"symbol": c["symbol"], "why": f"results on {r_on}: overnight gap risk"})
        btst_picks = [c for c in btst_picks if c["symbol"] not in {x["symbol"] for x in skipped}]
        overnight = [e for e in ev["calendar"] if e["overnight"]]
        keep_n = 3 if overnight or ev["risk"]["level"] == "High" else 5
        btst_caution = (("Big event before tomorrow's open: " + ", ".join(e["name"] for e in overnight) + ". ")
                        if overnight else "") + \
                       ("Risk is high today. " if ev["risk"]["level"] == "High" else "")
        if btst_caution:
            btst_caution += f"Showing only the strongest {keep_n}; use smaller size."
        if SETTINGS.get("btst_edge") is False:
            btst_caution = ("Paper only: no BTST rule has made money after costs in the backtest yet. "
                            "Follow these on paper; don't trade them with real money. ") + btst_caution
        btst_picks = btst_picks[:keep_n]
        rcfg = risk.config(SETTINGS)
        for i, c in enumerate(btst_picks):
            if gx:
                c["gex"] = gx["regime"]
            if not demo:
                c["news"] = events.stock_news(NAMES.get(c["symbol"], c["symbol"]), c["symbol"])
            c["option"] = option_plan(c["symbol"], STOCK_IDS.get(c["symbol"]), c["side"], c["plan"], c["price"], demo)
            c["size"] = risk.size_stock(c["plan"], rcfg, btst=True)
            c["opt_size"] = risk.size_option(c["option"], rcfg)
            c["take"] = i < rcfg["btst_max"]               # the risk engine takes only the best few overnight
    btst_now = max((cs[-1]["t"] + 300 for cs in candles_by.values()), default=time.time()) if demo else time.time()
    if not demo and session_time >= "15:00":            # lock in the 3 PM list as today's BTST trades
        for c in btst_picks:
            if c.get("take"):
                BTSTS.record(c, btst_now)
    BTSTS.update(candles_by, btst_now)
    btst_state = {"open": btst_open, "from": btst_from, "picks": btst_picks, "book": BTSTS.view(),
                  "skipped": skipped, "caution": btst_caution}

    # Lock every new pick into today's trades and follow them all through the day's candles.
    book_now = max((cs[-1]["t"] + 300 for cs in candles_by.values()), default=time.time()) if demo else time.time()
    day_s = datetime.now(IST).strftime("%Y-%m-%d")
    rcfg = risk.config(SETTINGS)
    TRADES.update(candles_by, book_now)                # up-to-date results before the risk check
    blocked = []
    for c in top["picks"]:
        add_event_info(c, ev, demo)
        key = (day_s, c["symbol"], c["side"])
        if key not in OPTION_PLANS:                    # worked out once, when first picked
            OPTION_PLANS[key] = option_plan(c["symbol"], STOCK_IDS.get(c["symbol"]), c["side"], c["plan"], c["price"], demo)
        c["option"] = OPTION_PLANS[key]
        c["size"] = risk.size_stock(c["plan"], rcfg)
        c["opt_size"] = risk.size_option(c["option"], rcfg)
        if any(r["symbol"] == c["symbol"] and r["side"] == c["side"] for r in TRADES.rows):
            continue                                   # already in today's list
        st = risk.day_status(TRADES.rows, rcfg)
        if st["blocked"]:
            blocked.append({"symbol": c["symbol"], "side": c["side"], "why": st["blocked"]})
            continue
        c["locked_new"] = TRADES.record(c, book_now)
    TRADES.update(candles_by, book_now)
    risk_state = dict(rcfg, **risk.day_status(TRADES.rows, rcfg), skipped=blocked)
    day = datetime.now(IST).date()
    for c in top["picks"]:
        key = (day, c["symbol"], c["side"])
        PICK_SINCE.setdefault(key, datetime.now(IST).strftime("%H:%M"))
        c["since"] = PICK_SINCE[key]
        if c["symbol"] in candles_by:
            c["spark"] = [round(k["c"], 2) for k in candles_by[c["symbol"]][-60:]]
    # The Intraday tab always shows the day's list: every breakout given today, once each, newest first.
    fresh = {c["symbol"] for c in top["picks"]}
    top["picks"] = intraday_list(fresh)
    top["list"] = True
    for c in top["picks"]:
        if c["symbol"] in candles_by:
            c["spark"] = [round(k["c"], 2) for k in candles_by[c["symbol"]][-60:]]

    def bo_row(sym):
        _, st, _, r = bo[sym]
        return {"symbol": sym, "price": r["price"], "pct": r["pct"], "score": st["score"],
                "level": st["level"], "level_price": round(st["level_price"], 2),
                "status": st["status"], "time": datetime.fromtimestamp(st["time"], IST).strftime("%H:%M"),
                "key": f'{sym}|{st["level"]}|{st["time"]}'}

    bo_bull = sorted((s for s in bo if bo[s][1]["side"] == "bull"), key=lambda s: -bo[s][1]["score"])
    bo_bear = sorted((s for s in bo if bo[s][1]["side"] == "bear"), key=lambda s: -bo[s][1]["score"])
    breakouts = {"bullish": [bo_row(s) for s in bo_bull[:TOP_N]],
                 "bearish": [bo_row(s) for s in bo_bear[:TOP_N]],
                 "scores": [{"side": bo[s][1]["side"], "score": bo[s][1]["score"]} for s in bo]}
    with LOCK:
        if demo != DEMO:
            return
        CANDLES.clear()
        CANDLES.update({s: (candles_by[s], results[s]) for s in results})
        BREAKOUTS.clear()
        BREAKOUTS.update(bo)
        PICKS.clear()
        PICKS.update({c["symbol"]: (c, candles_by[c["symbol"]]) for c in top["picks"] if c["symbol"] in candles_by})
        BTST_PICKS.clear()
        BTST_PICKS.update({c["symbol"]: (c, candles_by[c["symbol"]]) for c in btst_picks})
        STATE.update(status="live", message="", scores=scores,
                     events={k: ev[k] for k in ("calendar", "crude", "usdinr", "news", "risk")}, risk=risk_state,
                     universe={"total": len(stocks), "liquid": len(stocks) - len(thin), "skipped": len(thin),
                               "skipped_list": sorted(thin),
                               "min_turnover_cr": liq_limits()[0], "min_volume_lakh": liq_limits()[1]}, breakouts=breakouts, picks=top, market=mkt,
                     trades=TRADES.view(), btst=btst_state,
                     last_scan=datetime.now(IST).strftime("%H:%M"),
                     bullish=[row(s, "bull") for s in bull[:TOP_N]],
                     bearish=[row(s, "bear") for s in bear[:TOP_N]])


INTRADAY_FROM, INTRADAY_UNTIL = "09:30", intraday.LAST_ENTRY
# Every 15 minutes from 9:30 to 14:00 (right after each 15-min candle closes) for breakouts;
# then 14:30 and 15:00 for BTST. BTST is also worked out in every scan from 11:00.
INTRADAY_SCANS = [(9, 20)] + [(9 + (30 + 15 * i) // 60, (30 + 15 * i) % 60) for i in range(19)]   # 9:20 (ORB), 9:30 ... 14:00
BTST_SCANS = [(14, 30), (15, 0)]
SCHEDULE = INTRADAY_SCANS + BTST_SCANS
TRACK_EVERY = 5                                        # minutes: follow the day's trades between scans


def track_once():
    """Between scans: fetch only the stocks with an open trade and move their trades along."""
    if DEMO:
        return
    now = time.time()
    open_intra = {r["symbol"] for r in TRADES.rows if r.get("exit_at") is None}
    open_btst = {r["symbol"] for r in BTSTS.rows if r.get("result_r") is None}
    candles_by = {}
    for sym in sorted(open_intra | open_btst):
        sid = STOCK_IDS.get(sym)
        if not sid:
            continue
        try:
            candles_by[sym] = dhan_candles(sid)
        except DhanError as e:
            if e.fatal:
                raise
        time.sleep(0.25)
    TRADES.update(candles_by, now)
    BTSTS.update(candles_by, now)
    with LOCK:
        for sym, cs in candles_by.items():
            if sym in PICKS:
                PICKS[sym] = (PICKS[sym][0], cs)
        pk = STATE.get("picks") or {}
        if not pk.get("list") and datetime.now(IST).strftime("%H:%M") > INTRADAY_UNTIL and "funnel" in pk:
            pk.update(list=True, window_closed=True, watch=[])   # 10:20/10:30: the day's list, no waiting for 11:00
        if pk.get("list"):
            pk["picks"] = intraday_list()
            for c in pk["picks"]:
                if c["symbol"] in candles_by:
                    PICKS[c["symbol"]] = (c, candles_by[c["symbol"]])
        STATE["trades"] = TRADES.view()
        rcfg = risk.config(SETTINGS)
        STATE["risk"] = dict(rcfg, **risk.day_status(TRADES.rows, rcfg), skipped=(STATE.get("risk") or {}).get("skipped", []))
        if STATE.get("btst"):
            STATE["btst"]["book"] = BTSTS.view()
        STATE["last_track"] = datetime.now(IST).strftime("%H:%M")


def next_scan_time(now):
    """Next scheduled scan (20 seconds after the candle closes), skipping nights and weekends."""
    day = now
    for _ in range(8):
        if day.weekday() < 5:
            for h, m in SCHEDULE:
                t = day.replace(hour=h, minute=m, second=20, microsecond=0)
                if t > now:
                    return t
        day = (day + timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0)
    return now + timedelta(hours=1)


def scanner():
    first = forced = True
    while True:
        if first or forced or market_open() or DEMO:
            try:
                scan_once()
            except DhanError as e:
                if "access token" in str(e) and keep_token_once(force=True):
                    WAKE.set()                         # renewed: scan again right away
                    set_state(status="starting", message="Dhan token renewed automatically. Scanning…")
                else:
                    set_state(status="error", message=str(e))
            except Exception as e:
                set_state(status="error", message=f"Scan failed: {e}")
            first = False
            if not market_open() and not DEMO and STATE["status"] == "live":
                set_state(message="Market closed - showing the last scan.")
        now = datetime.now(IST)
        nxt = now + timedelta(minutes=SCAN_EVERY) if DEMO else next_scan_time(now)
        set_state(next_scan=nxt.strftime("%H:%M") if nxt.date() == now.date() else nxt.strftime("%a %H:%M"))
        while True:
            now = datetime.now(IST)
            # After the 10:15 scan, follow the day's trades every 5 minutes until the next scan.
            tracking = not DEMO and market_open(now) and now.strftime("%H:%M") > INTRADAY_UNTIL
            step = now.replace(second=20, microsecond=0) + timedelta(minutes=TRACK_EVERY - now.minute % TRACK_EVERY)
            until = min(nxt, step) if tracking else nxt
            forced = WAKE.wait(max(0, (until - now).total_seconds()))  # True when "Scan now" or new keys
            WAKE.clear()
            if forced or datetime.now(IST) >= nxt:
                break
            try:
                track_once()
            except Exception as e:
                print(f"  tracking: {e}", file=sys.stderr)


def save_keys(client_id, token):
    """Save the Dhan keys on this PC and switch from demo to live data."""
    global CLIENT_ID, TOKEN, DEMO
    save_settings(client_id=client_id, access_token=token,
                  saved_at=datetime.now(IST).strftime("%d %b %Y %H:%M"))
    CLIENT_ID, TOKEN, DEMO = client_id, token, False
    set_state(demo=False, status="starting", message="Keys saved. Starting a scan with your Dhan data…",
              bullish=[], bearish=[], scores=[], last_scan=None)
    with LOCK:
        CANDLES.clear()
    WAKE.set()
    BULLION_WAKE.set()


def forget_keys():
    global CLIENT_ID, TOKEN, DEMO
    save_settings(client_id=None, access_token=None, saved_at=None)
    CLIENT_ID, TOKEN, DEMO = "", "", True
    set_state(demo=True, status="starting", message="Dhan keys removed. Showing demo data.",
              bullish=[], bearish=[], scores=[], last_scan=None)
    WAKE.set()
    BULLION_WAKE.set()


# ── Keeping the Dhan token alive ────────────────────────────────────────────
# Dhan tokens last 24 hours. GET /v2/RenewToken swaps a still-valid token for a new
# 24-hour one, so the scanner renews it whenever less than RENEW_BEFORE is left:
# a token pasted once keeps going for as long as the scanner keeps running.
RENEW_BEFORE = 4 * 3600
TOKEN_STATUS = {"note": "", "last_renewed": None}


def token_expiry(token):
    """Expiry (epoch seconds) from the token's JWT payload, or None."""
    try:
        part = token.split(".")[1]
        payload = json.loads(base64.urlsafe_b64decode(part + "=" * (-len(part) % 4)))
        return int(payload["exp"])
    except (IndexError, ValueError, KeyError, TypeError):
        return None


def _token_from(resp):
    """Find the new token in Dhan's reply, whatever the field is called."""
    if isinstance(resp, dict):
        for k in ("accessToken", "access_token", "token"):
            if isinstance(resp.get(k), str) and len(resp[k]) > 20:
                return resp[k]
        for v in resp.values():
            t = _token_from(v)
            if t:
                return t
    return None


def renew_token():
    req = urllib.request.Request(API + "/RenewToken", method="GET",
                                 headers={"access-token": TOKEN, "dhanClientId": CLIENT_ID,
                                          "Accept": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=20) as r:
            return _token_from(json.loads(r.read() or b"{}"))
    except urllib.error.HTTPError as e:
        raise DhanError(f"Dhan said {e.code}: {e.read().decode(errors='replace')[:200]}") from None
    except urllib.error.URLError as e:
        raise DhanError(f"Can't reach Dhan: {e.reason}") from None


def keep_token_once(force=False):
    """Renew the token when it is close to expiring. Returns True if a new token is in use."""
    global TOKEN
    if DEMO or not TOKEN:
        return False
    exp = token_expiry(TOKEN)
    left = (exp - time.time()) if exp else None
    if not force and left is not None and left > RENEW_BEFORE:
        return False
    if left is not None and left <= 0:
        TOKEN_STATUS["note"] = "The token has already expired; renewing only works on a valid one. Paste a new token once."
        return False
    try:
        new = renew_token()
    except DhanError as e:
        TOKEN_STATUS["note"] = f"Renewing failed: {e}"
        print("  " + TOKEN_STATUS["note"])
        return False
    if not new:
        TOKEN_STATUS["note"] = "Renewing failed: Dhan sent no token back."
        return False
    TOKEN = new
    now = datetime.now(IST).strftime("%d %b %H:%M")
    save_settings(access_token=new, saved_at=now)
    TOKEN_STATUS.update(note="", last_renewed=now)
    print(f"  Dhan token renewed at {now}")
    return True


def token_keeper():
    while True:
        try:
            if keep_token_once():
                WAKE.set()                             # rescan straight away with the new token
        except Exception as e:                         # never let the keeper die
            TOKEN_STATUS["note"] = f"Renewal check failed: {e}"
        time.sleep(15 * 60)


def token_info():
    exp = token_expiry(TOKEN) if TOKEN and not DEMO else None
    return {"expires": datetime.fromtimestamp(exp, IST).strftime("%d %b %H:%M") if exp else None,
            "expired": bool(exp and exp < time.time()),
            "last_renewed": TOKEN_STATUS["last_renewed"], "note": TOKEN_STATUS["note"]}


# ── Phone access ────────────────────────────────────────────────────────────
FAILED_LOGINS = {}                                     # ip -> (count, locked until)


def loose(password):
    """Forgiving form of a password for comparing: phones capitalise the first letter,
    add spaces when pasting and change dashes, so those don't count."""
    return "".join(ch for ch in password.lower() if ch.isalnum())


def hash_pin(pin, salt):
    return hashlib.pbkdf2_hmac("sha256", pin.encode(), bytes.fromhex(salt), 120_000).hex()


def session_token():
    key = SETTINGS.get("session_key")
    if not key:
        return None
    if CLOUD:                                          # changing the password logs everyone out
        if not PASSWORD:
            return None
        return hmac.new(bytes.fromhex(key), b"cloud:" + hashlib.sha256(PASSWORD.encode()).digest(),
                        "sha256").hexdigest()
    return hmac.new(bytes.fromhex(key), b"phone-access", "sha256").hexdigest()


if CLOUD and not SETTINGS.get("session_key"):
    save_settings(session_key=secrets.token_hex(32))


def set_phone_access(enabled, pin=None):
    if pin:
        salt = secrets.token_hex(16)
        save_settings(pin_salt=salt, pin_hash=hash_pin(pin, salt),
                      session_key=secrets.token_hex(32))   # a new PIN logs out every phone
    save_settings(phone=bool(enabled))


def local_addresses():
    """This PC's addresses a phone can use: home Wi-Fi and Tailscale (100.x)."""
    ips = set()
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        ips.add(s.getsockname()[0])
        s.close()
    except OSError:
        pass
    try:
        ips.update(socket.gethostbyname_ex(socket.gethostname())[2])
    except OSError:
        pass
    out = []
    for ip in sorted(ips):
        if ip.startswith("127.") or ip.startswith("169.254."):
            continue
        a, b = (int(x) for x in ip.split(".")[:2])
        kind = "Tailscale - works from anywhere" if a == 100 and 64 <= b <= 127 else "Home Wi-Fi"
        out.append({"url": f"http://{ip}:{PORT}", "kind": kind})
    out.sort(key=lambda u: not u["kind"].startswith("Tailscale"))
    return out


def _png(size, pixel):
    raw = b"".join(b"\x00" + b"".join(bytes(pixel(x, y)) for x in range(size)) for y in range(size))
    chunk = lambda t, d: struct.pack(">I", len(d)) + t + d + struct.pack(">I", zlib.crc32(t + d) & 0xffffffff)
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", size, size, 8, 2, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(raw, 9)) + chunk(b"IEND", b""))


def _icon_pixel(x, y):
    """Home-screen icon: the three-bar logo on the dark background."""
    bars = [(52, 70, 128, (34, 211, 138)), (82, 100, 104, (34, 211, 138)), (112, 130, 128, (240, 67, 93))]
    for top, bottom, width, rgb in bars:
        if top <= y < bottom and 40 <= x < 40 + width:
            return rgb
    return (11, 14, 19)


ICON = _png(180, _icon_pixel)
MANIFEST = json.dumps({"name": "OrderBlock Scanner", "short_name": "OrderBlock", "start_url": "/",
                       "display": "standalone", "background_color": "#0b0e13", "theme_color": "#0b0e13",
                       "icons": [{"src": "/icon.png", "sizes": "180x180", "type": "image/png"}]}).encode()

PAGE_STYLE = """<meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover">
<meta name="apple-mobile-web-app-capable" content="yes"><meta name="theme-color" content="#0b0e13">
<link rel="apple-touch-icon" href="/icon.png"><link rel="manifest" href="/manifest.webmanifest">
<style>body{margin:0;min-height:100vh;display:grid;place-items:center;background:#0b0e13;color:#e8edf3;
font:16px system-ui,-apple-system,sans-serif;padding:16px;box-sizing:border-box}
.box{width:min(340px,100%);box-sizing:border-box;background:#11151c;border:1px solid #232a35;border-radius:18px;padding:24px;text-align:center}
h1{font-size:20px;margin:0 0 6px}p{color:#8a94a3;font-size:14px;margin:0 0 16px}
input{width:100%;box-sizing:border-box;padding:14px;border-radius:12px;border:1px solid #343c49;background:#161b23;
color:#e8edf3;font:600 24px ui-monospace,monospace;text-align:center;letter-spacing:.3em}
button{margin-top:14px;width:100%;padding:13px;border:0;border-radius:12px;font:600 16px system-ui;color:#fff;
background:linear-gradient(135deg,#7b6cf6,#a08bff)}.err{color:#ffb3bf;font-size:14px;min-height:20px;margin-top:10px}</style>"""
LOGIN_PAGE = f"""<!doctype html><html><head><meta charset="utf-8"><title>OrderBlock Scanner</title>{PAGE_STYLE}</head>
<body><form class="box" id="f"><h1>OrderBlock Scanner</h1><p>{"Enter your scanner password." if CLOUD else "Enter the phone PIN you set on your PC."}</p>
<input id="pin" type="password" autocapitalize="off" autocorrect="off" spellcheck="false" {'autocomplete="current-password" style="font-size:18px;letter-spacing:.05em"' if CLOUD else 'inputmode="numeric" autocomplete="current-password" maxlength="8"'} autofocus>
<label style="display:flex;gap:8px;align-items:center;justify-content:center;margin-top:10px;font-size:14px;color:#8a94a3">
<input type="checkbox" onchange="pin.type=this.checked?'text':'password'" style="width:18px;height:18px">Show password</label>
<button>Open</button><div class="err" id="e"></div></form>
<script>f.onsubmit=async e=>{{e.preventDefault();const r=await fetch('/api/login',{{method:'POST',
headers:{{'Content-Type':'application/json'}},body:JSON.stringify({{pin:pin.value}})}});
if(r.ok)location.reload();else{{e=await r.json();document.getElementById('e').textContent=e.error;pin.value=''}}}}</script>
</body></html>""".encode()
OFF_PAGE = f"""<!doctype html><html><head><meta charset="utf-8"><title>OrderBlock Scanner</title>{PAGE_STYLE}</head>
<body><div class="box"><h1>Phone access is off</h1><p>On your PC, open the scanner, click <b>Settings</b>
and turn on <b>Phone access</b> with a PIN. Then reload this page.</p></div></body></html>""".encode()


def chart_payload(symbol, side):
    with LOCK:
        item = CANDLES.get(symbol)
    if not item:
        return None
    candles, res = item
    ob = res.get(side) or res.get("bull") or res.get("bear")
    view = candles[-90:]
    offset = len(candles) - len(view)
    return {"symbol": symbol, "price": res["price"], "pct": res["pct"], "side": ob["side"],
            "candles": view,
            "zone": {"top": ob["top"], "bottom": ob["bottom"], "start": max(0, ob["index"] - offset)},
            "support": ob["support"], "resistance": ob["resistance"], "liquidity": ob["liquidity"],
            "score": ob["score"], "tests": ob["tests"]}


def pick_payload(symbol, book=None):
    with LOCK:
        item = (book if book is not None else PICKS).get(symbol)
    if not item:
        return None
    c, candles = item
    view = candles[-90:]
    return {"symbol": symbol, "price": c["price"], "pct": c["pct"], "side": c["side"], "pick": c,
            "candles": [{k: x[k] for k in "tohlcv"} for x in view]}


def breakout_payload(symbol):
    with LOCK:
        item = BREAKOUTS.get(symbol)
    if not item:
        return None
    c15, st, levels, res = item
    view = c15[-60:]
    lo, hi = min(c["l"] for c in view), max(c["h"] for c in view)
    span = hi - lo
    near = [l for l in levels if lo - span * .5 <= l["price"] <= hi + span * .5]
    return {"symbol": symbol, "price": res["price"], "pct": res["pct"], "side": st["side"],
            "candles": [{k: c[k] for k in "tohlcv"} for c in view],
            "breakout_index": st["index"] - (len(c15) - len(view)) if st["index"] >= len(c15) - len(view) else None,
            "level": st["level"], "level_price": st["level_price"], "status": st["status"],
            "stop": st["stop"], "target": st["target"], "vol_ratio": st["vol_ratio"],
            "score": st["score"], "time": datetime.fromtimestamp(st["time"], IST).strftime("%d %b %H:%M"),
            "levels": [{"name": l["name"], "price": l["price"]} for l in near]}


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def send(self, code, body, ctype="application/json"):
        data = body if isinstance(body, bytes) else json.dumps(body).encode()
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def is_local(self):
        if CLOUD:                                       # behind Caddy every request looks local
            return False
        ip = self.client_address[0]
        return ip.startswith("127.") or ip in ("::1", "::ffff:127.0.0.1")

    def client_ip(self):
        if CLOUD:
            return (self.headers.get("X-Forwarded-For") or self.client_address[0]).split(",")[0].strip()
        return self.client_address[0]

    def can_admin(self):
        """Who may change settings: this PC, or anyone signed in to the cloud copy."""
        return self.is_local() or (CLOUD and self.signed_in())

    def signed_in(self):
        """This PC always; a phone only with phone access on and a valid PIN cookie.
        In the cloud: anyone with a valid password cookie."""
        if self.is_local():
            return True
        token = session_token()
        if CLOUD:
            if not token:
                return False
        elif not (SETTINGS.get("phone") and SETTINGS.get("pin_hash") and token):
            return False
        cookie = SimpleCookie(self.headers.get("Cookie", "")).get("obs")
        return bool(cookie) and hmac.compare_digest(cookie.value, token)

    def json_body(self):
        if not self.headers.get("Content-Type", "").startswith("application/json"):
            raise ValueError("JSON only")               # other websites can't send JSON without asking
        body = json.loads(self.rfile.read(min(int(self.headers.get("Content-Length", 0)), 10000)) or b"{}")
        if not isinstance(body, dict):
            raise ValueError("bad body")
        return body

    def do_GET(self):
        url = urlparse(self.path)
        q = parse_qs(url.query)
        if url.path == "/icon.png":
            return self.send(200, ICON, "image/png")
        if url.path.startswith("/static/vendor/") and url.path.endswith(".js"):
            f = HERE / "static" / "vendor" / url.path.rsplit("/", 1)[1]   # the chart library (no paths in names)
            return self.send(200, f.read_bytes(), "text/javascript") if f.is_file() else self.send(404, {"error": "not found"})
        if url.path == "/manifest.webmanifest":
            return self.send(200, MANIFEST, "application/manifest+json")
        if url.path == "/healthz":
            return self.send(200, b"ok", "text/plain")
        if not self.signed_in():
            if url.path.startswith("/api/"):
                return self.send(401, {"error": "Sign in first."})
            page = LOGIN_PAGE if CLOUD or (SETTINGS.get("phone") and SETTINGS.get("pin_hash")) else OFF_PAGE
            return self.send(200, page, "text/html; charset=utf-8")
        if url.path in ("/", "/index.html"):
            self.send(200, (HERE / "static" / "index.html").read_bytes(), "text/html; charset=utf-8")
        elif url.path == "/api/state":
            with LOCK:
                self.send(200, dict(STATE, local=self.is_local(), admin=self.can_admin(), cloud=CLOUD))
        elif url.path == "/api/settings":
            if not self.can_admin():
                return self.send(403, {"error": "Settings can only be changed on the PC."})
            tok = SETTINGS.get("access_token") or ""
            self.send(200, {"client_id": SETTINGS.get("client_id") or CLIENT_ID,
                            "token_tail": tok[-4:] if tok else "", "saved_at": SETTINGS.get("saved_at"),
                            "saved_where": str(SETTINGS_FILE), "phone": bool(SETTINGS.get("phone")),
                            "has_pin": bool(SETTINGS.get("pin_hash")), "listening": HOST == "0.0.0.0",
                            "cloud": CLOUD, "public_url": PUBLIC_URL,
                            "picks_from": SETTINGS.get("intraday_from") or INTRADAY_FROM,
                            "risk": risk.config(SETTINGS),
                            "min_turnover_cr": liq_limits()[0], "min_volume_lakh": liq_limits()[1],
                            "token": token_info(),
                            "urls": [] if CLOUD else local_addresses()})
        elif url.path == "/api/journal":
            live = []
            for f in sorted(SETTINGS_DIR.glob("trades-*.json")):
                try:
                    live += json.loads(f.read_text(encoding="utf-8"))
                except (OSError, ValueError):
                    pass
            try:
                bt = json.loads((SETTINGS_DIR / "backtest.json").read_text(encoding="utf-8"))
            except (OSError, ValueError):
                bt = None
            self.send(200, {
                "live": {"intraday": journal.analyse(live), "btst": journal.analyse(BTSTS.rows, "btst")},
                "backtest": {"intraday": journal.analyse(bt["intraday"]["trades"]),
                             "btst": journal.analyse(bt["btst"]["trades"], "btst"), "ran_at": bt.get("ran_at")} if bt else None})
        elif url.path == "/api/backtest":
            try:
                rep = json.loads((SETTINGS_DIR / "backtest.json").read_text(encoding="utf-8"))
            except (OSError, ValueError):
                rep = None
            try:
                cmp = json.loads((SETTINGS_DIR / "compare.json").read_text(encoding="utf-8"))
            except (OSError, ValueError):
                cmp = None
            self.send(200, {"job": dict(BACKTEST), "report": rep, "compare": cmp,
                            "rules": SETTINGS.get("rules_names") or {}, "manual": SETTINGS.get("rules_manual") or {},
                            "btst_edge": SETTINGS.get("btst_edge", True)})
        elif url.path == "/api/performance":
            month = (q.get("month", [""])[0] or datetime.now(IST).strftime("%Y-%m"))[:7]
            if not (len(month) == 7 and month[4] == "-" and month.replace("-", "").isdigit()):
                return self.send(400, {"error": "month must be YYYY-MM"})
            self.send(200, trades.month_report(SETTINGS_DIR, month, BTSTS.rows))
        elif url.path == "/api/btst":
            p = pick_payload(q.get("symbol", [""])[0].upper(), BTST_PICKS)
            self.send(200 if p else 404, p or {"error": "not found"})
        elif url.path == "/api/pick":
            p = pick_payload(q.get("symbol", [""])[0].upper())
            self.send(200 if p else 404, p or {"error": "not found"})
        elif url.path == "/api/breakout":
            p = breakout_payload(q.get("symbol", [""])[0].upper())
            self.send(200 if p else 404, p or {"error": "not found"})
        elif url.path == "/api/bullion":
            p = bullion_payload(q.get("symbol", [""])[0].upper())
            self.send(200 if p else 404, p or {"error": "not found"})
        elif url.path == "/api/chart":
            p = chart_payload(q.get("symbol", [""])[0].upper(), q.get("side", ["bull"])[0])
            self.send(200 if p else 404, p or {"error": "not found"})
        else:
            self.send(404, {"error": "not found"})

    def do_POST(self):
        path = urlparse(self.path).path
        try:
            body = self.json_body()
        except ValueError:
            return self.send(400, {"error": "Bad request"})
        if path == "/api/login":
            return self.login(body)
        if not self.signed_in():
            return self.send(401, {"error": "Sign in first."})
        if path == "/api/scan":
            WAKE.set()
            return self.send(200, {"ok": True})
        if path == "/api/bullion/scan":
            BULLION_WAKE.set()
            return self.send(200, {"ok": True})
        if path == "/api/logout":
            return self.send_cookie("", 0)
        if not self.can_admin():                        # everything below changes settings
            return self.send(403, {"error": "Settings can only be changed on the PC."})
        if path == "/api/backtest":
            if BACKTEST["status"] == "running":
                return self.send(200, {"ok": True, "running": True})
            BACKTEST.update(status="running", message="Starting…")
            threading.Thread(target=backtest_job, args=(bool(body.get("compare")),), daemon=True).start()
            return self.send(200, {"ok": True})
        if path == "/api/rules":
            kind, name = body.get("kind"), body.get("name")
            if name == "auto" and kind in ("intraday", "btst"):
                save_settings(rules_manual=dict(SETTINGS.get("rules_manual") or {}, **{kind: False}))
                try:
                    apply_best(json.loads((SETTINGS_DIR / "compare.json").read_text(encoding="utf-8")))
                except (OSError, ValueError):
                    pass
                WAKE.set()
                return self.send(200, {"ok": True})
            variants = dict(backtest.INTRADAY_VARIANTS if kind == "intraday" else backtest.BTST_VARIANTS if kind == "btst" else [])
            if name not in variants:
                return self.send(400, {"error": "Unknown rule variant."})
            rules, names = dict(SETTINGS.get("rules") or {}), dict(SETTINGS.get("rules_names") or {})
            rules[kind], names[kind] = variants[name], name
            save_settings(rules=rules, rules_names=names,
                          rules_manual=dict(SETTINGS.get("rules_manual") or {}, **{kind: True}))
            WAKE.set()
            return self.send(200, {"ok": True})
        if path == "/api/prefs":
            t = str(body.get("picks_from") or "").strip()
            if t not in ("09:30", "09:45", "10:00", "10:15"):
                return self.send(400, {"error": "Pick 09:30, 09:45, 10:00 or 10:15."})
            liq = {}
            for key, lo, hi in (("min_turnover_cr", 0, 5000), ("min_volume_lakh", 0, 500)):
                if body.get(key) in (None, ""):
                    continue
                try:
                    v = float(body[key])
                except (TypeError, ValueError):
                    v = -1
                if not lo <= v <= hi:
                    return self.send(400, {"error": "Liquidity: turnover 0–5000 crore, volume 0–500 lakh."})
                liq[key] = v
            for k, (lo, hi) in risk.LIMITS.items():
                v = body.get("risk_" + k)
                if v in (None, ""):
                    continue
                try:
                    v = float(v)
                except (TypeError, ValueError):
                    v = None
                if v is None or not lo <= v <= hi:
                    return self.send(400, {"error": f"Risk setting {k.replace('_', ' ')} must be between {lo:g} and {hi:g}."})
                liq["risk_" + k] = int(v) if k in ("capital", "max_open", "max_trades", "btst_max") else v
            save_settings(intraday_from=t, **liq)
            WAKE.set()                                  # rescan so the change shows now
            return self.send(200, {"ok": True})
        if path == "/api/forget":
            forget_keys()
            self.send(200, {"ok": True})
        elif path == "/api/phone":
            if CLOUD:
                return self.send(400, {"error": "Not needed in the cloud: every device signs in with the password."})
            pin = str(body.get("pin") or "").strip()
            if pin and not (pin.isdigit() and 4 <= len(pin) <= 8):
                return self.send(400, {"error": "The PIN should be 4 to 8 digits."})
            if body.get("enabled") and not pin and not SETTINGS.get("pin_hash"):
                return self.send(400, {"error": "Choose a PIN first."})
            set_phone_access(body.get("enabled"), pin or None)
            self.send(200, {"ok": True})
        elif path == "/api/keys":
            cid = str(body.get("client_id", "")).strip()
            tok = str(body.get("access_token", "")).strip()
            if not tok and SETTINGS.get("access_token") and cid == SETTINGS.get("client_id"):
                return self.send(400, {"error": "Paste a new access token, or press Close to keep the saved one."})
            if not cid.isdigit():
                self.send(400, {"error": "Client ID should be numbers only."})
            elif len(tok) < 20 or any(c.isspace() for c in tok):
                self.send(400, {"error": "That access token doesn't look complete. Copy the whole token."})
            else:
                save_keys(cid, tok)
                self.send(200, {"ok": True})
        else:
            self.send(404, {"error": "not found"})

    def login(self, body):
        ip, now = self.client_ip(), time.time()
        count, until = FAILED_LOGINS.get(ip, (0, 0))
        word = "password" if CLOUD else "PIN"
        if until > now:
            return self.send(429, {"error": f"Too many wrong {word}s. Try again in {int(until - now) // 60 + 1} min."})
        given = str(body.get("pin") or "")
        if CLOUD:
            if not PASSWORD:
                return self.send(403, {"error": "No password is set on the server."})
            ok = hmac.compare_digest(hashlib.sha256(loose(given).encode()).digest(),
                                     hashlib.sha256(loose(PASSWORD).encode()).digest())
        else:
            if not (SETTINGS.get("phone") and SETTINGS.get("pin_hash")):
                return self.send(403, {"error": "Phone access is off on the PC."})
            ok = hmac.compare_digest(hash_pin(given, SETTINGS["pin_salt"]), SETTINGS["pin_hash"])
        if not ok:
            count += 1
            FAILED_LOGINS[ip] = (count, now + 600 if count >= 5 else 0)
            time.sleep(1)
            return self.send(401, {"error": f"Wrong {word}."})
        FAILED_LOGINS.pop(ip, None)
        self.send_cookie(session_token(), 7776000)

    def send_cookie(self, value, max_age):
        data = b'{"ok": true}'
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        secure = "; Secure" if CLOUD else ""
        self.send_header("Set-Cookie", f"obs={value}; Max-Age={max_age}; Path=/; HttpOnly; SameSite=Lax{secure}")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)


def main():
    try:                                               # a comparison made before auto-pick existed: use it once
        if not SETTINGS.get("auto_applied") and (SETTINGS_DIR / "compare.json").exists():
            apply_best(json.loads((SETTINGS_DIR / "compare.json").read_text(encoding="utf-8")))
    except (OSError, ValueError, KeyError, TypeError) as e:
        print(f"Couldn't apply the saved comparison: {e}", file=sys.stderr)
    threading.Thread(target=scanner, daemon=True).start()
    threading.Thread(target=token_keeper, daemon=True).start()
    threading.Thread(target=bullion_loop, daemon=True).start()
    try:
        server = ThreadingHTTPServer((HOST, PORT), Handler)
    except OSError:
        print(f"\n  Port {PORT} is already in use - the scanner is probably already running.")
        print(f"  Open http://localhost:{PORT} in your browser, or close the other black window first.\n")
        if "--no-browser" not in sys.argv:
            webbrowser.open(f"http://localhost:{PORT}")
        sys.exit(1)
    url = f"http://{'localhost' if HOST in ('127.0.0.1', '0.0.0.0') else HOST}:{PORT}"
    print(f"OrderBlock Scanner running at {url}" + ("   (DEMO data - click Connect Dhan on the dashboard)" if DEMO else ""))
    print(f"  Keys and phone PIN are saved in {SETTINGS_FILE}")
    if CLOUD:
        print(f"  Cloud mode: {PUBLIC_URL or 'behind Caddy'}; everyone signs in with the password.")
    elif SETTINGS.get("phone"):
        for u in local_addresses():
            print(f"  On your iPhone: {u['url']}   ({u['kind']})")
    if "--no-browser" not in sys.argv and not CLOUD:
        threading.Timer(1, webbrowser.open, [url]).start()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
