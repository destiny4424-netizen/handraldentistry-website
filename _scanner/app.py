"""OrderBlock Scanner: scans NSE F&O stocks with the Dhan API, finds order
blocks and liquidity, and serves a live ranked dashboard.

Run:  python app.py        (needs Python 3.9+, no extra packages)
Keys: put DHAN_CLIENT_ID and DHAN_ACCESS_TOKEN in a .env file next to this one.
      With no keys it starts in DEMO mode with made-up prices.
"""
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
import breakout
import picks

HERE = Path(__file__).resolve().parent
IST = timezone(timedelta(hours=5, minutes=30))
API = "https://api.dhan.co/v2"
SCRIP_MASTER = "https://images.dhan.co/api-data/api-scrip-master.csv"


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
SCAN_EVERY = int(ENV.get("SCAN_EVERY_MIN", "5"))      # auto-scan, lined up with candle closes
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


def fno_stocks():
    """{symbol: NSE equity security id} for every stock with NSE stock futures."""
    SETTINGS_DIR.mkdir(parents=True, exist_ok=True)
    cache = SETTINGS_DIR / "scrip-master.csv"          # Dhan's instrument list, refreshed daily
    fresh = cache.exists() and time.time() - cache.stat().st_mtime < 20 * 3600
    if not fresh:
        try:
            with urllib.request.urlopen(SCRIP_MASTER, timeout=60) as r:
                cache.write_bytes(r.read())
        except (urllib.error.URLError, TimeoutError) as e:
            if not cache.exists():
                raise DhanError("Couldn't download Dhan's list of F&O stocks. Check your internet "
                                f"connection and press Scan now. ({getattr(e, 'reason', e)})") from None
    f = open(cache, encoding="utf-8", errors="replace", newline="")   # streamed: the file is ~30 MB
    rows = csv.DictReader(f)
    underlyings, equity, futures = set(), {}, {}
    today = datetime.now(IST).strftime("%Y-%m-%d")
    for row in rows:
        if row.get("SEM_EXM_EXCH_ID") != "NSE":
            continue
        sym = row.get("SEM_TRADING_SYMBOL", "")
        if row.get("SEM_INSTRUMENT_NAME") == "FUTSTK":
            under = sym.rsplit("-", 2)[0]
            underlyings.add(under)
            expiry = (row.get("SEM_EXPIRY_DATE") or "")[:10]
            if expiry >= today and (under not in futures or expiry < futures[under][0]):
                futures[under] = (expiry, row["SEM_SMST_SECURITY_ID"])   # nearest expiry
        elif row.get("SEM_SEGMENT") == "E" and row.get("SEM_SERIES") == "EQ":
            equity[sym] = row["SEM_SMST_SECURITY_ID"]
    f.close()
    stocks = {s: equity[s] for s in sorted(underlyings) if s in equity}
    if ONLY:
        stocks = {s: i for s, i in stocks.items() if s in ONLY}
    if not stocks:
        raise DhanError("Couldn't find F&O stocks in Dhan's scrip master file.")
    FUTURES.clear()
    FUTURES.update({u: fid for u, (_, fid) in futures.items()})
    return stocks


FUTURES = {}                                           # symbol -> current-month futures security id


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


def dhan_daily(security_id):
    """About 4 months of daily candles, for the HTF key levels."""
    now = datetime.now(IST)
    body = {"securityId": str(security_id), "exchangeSegment": "NSE_EQ", "instrument": "EQUITY",
            "expiryCode": 0, "oi": False,
            "fromDate": (now - timedelta(days=120)).strftime("%Y-%m-%d"),
            "toDate": (now + timedelta(days=1)).strftime("%Y-%m-%d")}
    return _rows(dhan_post("/charts/historical", body))


def dhan_candles(security_id, interval=TIMEFRAME):
    now = datetime.now(IST)
    body = {"securityId": str(security_id), "exchangeSegment": "NSE_EQ",
            "instrument": "EQUITY", "interval": str(interval), "oi": False,
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
    day0 = (first["t"] + 19800) // 86400
    for back in range(1, 100):
        day = day0 - back
        if (day + 3) % 7 >= 5:                        # skip Sat/Sun (1970-01-01 was a Thursday)
            continue
        c = price
        o = c * (1 + rng.gauss(0, 0.012))
        h = max(o, c) * (1 + abs(rng.gauss(0, 0.006)))
        l = min(o, c) * (1 - abs(rng.gauss(0, 0.006)))
        out.append({"t": day * 86400 - 19800, "o": o, "h": h, "l": l, "c": c, "v": 1})
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
         "bullish": [], "bearish": [], "scores": [],
         "breakouts": {"bullish": [], "bearish": [], "scores": []},
         "picks": {"funnel": [], "flow": [], "sectors": [], "picks": []}}
CANDLES = {}
BREAKOUTS = {}                                         # symbol -> (15m candles, setup, levels, result)
PICKS = {}                                             # symbol -> pick (with its 5m candles)
PICK_SINCE = {}                                        # (day, symbol, side) -> first time it was picked
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


def fetch_stock(sym, sid, demo):
    """Order-block candles, 15-minute breakout candles and daily candles for one stock."""
    if demo:
        candles = demo_candles(sym)
        return candles, breakout.resample(candles, BREAKOUT_TF), demo_daily(sym, candles)
    candles = dhan_candles(sid)
    time.sleep(0.25)                                 # Dhan data API: 5 requests a second
    tf = int(TIMEFRAME)
    if tf <= BREAKOUT_TF and BREAKOUT_TF % tf == 0:
        c15 = breakout.resample(candles, BREAKOUT_TF)  # build 15m from the same data, no extra call
    else:
        c15 = breakout.resample(dhan_candles(sid, BREAKOUT_TF), BREAKOUT_TF)
        time.sleep(0.25)
    today = datetime.now(IST).date()
    cached = DAILY.get(sid)
    if not cached or cached[0] != today:             # daily levels only change once a day
        DAILY[sid] = (today, dhan_daily(sid))
        time.sleep(0.25)
    return candles, c15, DAILY[sid][1]


def scan_once():
    demo = DEMO                                      # keys can be added mid-scan
    stocks = {s: s for s in DEMO_SYMBOLS} if demo else fno_stocks()
    set_state(status="scanning", total=len(stocks), scanned=0,
              message=f"Scanning {len(stocks)} F&O stocks…")
    results, candles_by, bo, funnel_in = {}, {}, {}, {}
    for n, (sym, sid) in enumerate(stocks.items(), 1):
        try:
            if demo != DEMO:
                return                                   # switched to live keys: stop this scan
            candles, c15, daily = fetch_stock(sym, sid, demo)
        except DhanError as e:
            if e.fatal:
                raise
            print(f"  {sym}: {e}", file=sys.stderr)
            continue
        except Exception as e:                       # one bad stock shouldn't stop the scan
            print(f"  {sym}: {e}", file=sys.stderr)
            continue
        res = analysis.analyse(candles, SWING)
        if res:
            results[sym], candles_by[sym] = res, candles
            now_ts = c15[-1]["end"] if demo else time.time()
            setup, levels = breakout.detect(c15, daily, now_ts, res["price"])
            if setup:
                bo[sym] = (c15, setup, levels, res)
            funnel_in[sym] = {"res": res, "bo": setup, "levels": levels}
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
    # Top picks: funnel, then futures OI for the shortlisted stocks only.
    cands, finish = picks.build(funnel_in, MIN_RR, N_PICKS, MIN_SETUP)
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
    day = datetime.now(IST).date()
    for c in top["picks"]:
        key = (day, c["symbol"], c["side"])
        PICK_SINCE.setdefault(key, datetime.now(IST).strftime("%H:%M"))
        c["since"] = PICK_SINCE[key]
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
        PICKS.update({c["symbol"]: (c, candles_by[c["symbol"]]) for c in top["picks"]})
        STATE.update(status="live", message="", scores=scores, breakouts=breakouts, picks=top,
                     last_scan=datetime.now(IST).strftime("%H:%M"),
                     bullish=[row(s, "bull") for s in bull[:TOP_N]],
                     bearish=[row(s, "bear") for s in bear[:TOP_N]])


def next_scan_time(now):
    """Next candle close (every SCAN_EVERY minutes from 09:15) plus 20 seconds,
    so each scan sees the candle that just closed. Skips nights and weekends."""
    start = now.replace(hour=9, minute=15, second=20, microsecond=0)
    if now < start:
        t = start + timedelta(minutes=SCAN_EVERY)
    else:
        steps = int((now - start).total_seconds() // (SCAN_EVERY * 60)) + 1
        t = start + timedelta(minutes=steps * SCAN_EVERY)
    close = now.replace(hour=15, minute=30, second=20, microsecond=0)
    if t > close or t.weekday() >= 5:
        t = (now + timedelta(days=1)).replace(hour=9, minute=15, second=20, microsecond=0) \
            + timedelta(minutes=SCAN_EVERY)
        while t.weekday() >= 5:
            t += timedelta(days=1)
    return t


def scanner():
    first = forced = True
    while True:
        if first or forced or market_open() or DEMO:
            try:
                scan_once()
            except DhanError as e:
                set_state(status="error", message=str(e))
            except Exception as e:
                set_state(status="error", message=f"Scan failed: {e}")
            first = False
            if not market_open() and not DEMO and STATE["status"] == "live":
                set_state(message="Market closed - showing the last scan.")
        now = datetime.now(IST)
        nxt = now + timedelta(minutes=SCAN_EVERY) if DEMO else next_scan_time(now)
        set_state(next_scan=nxt.strftime("%H:%M") if nxt.date() == now.date() else nxt.strftime("%a %H:%M"))
        forced = WAKE.wait((nxt - now).total_seconds())  # True when "Scan now" or new keys
        WAKE.clear()


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


def forget_keys():
    global CLIENT_ID, TOKEN, DEMO
    save_settings(client_id=None, access_token=None, saved_at=None)
    CLIENT_ID, TOKEN, DEMO = "", "", True
    set_state(demo=True, status="starting", message="Dhan keys removed. Showing demo data.",
              bullish=[], bearish=[], scores=[], last_scan=None)
    WAKE.set()


# ── Phone access ────────────────────────────────────────────────────────────
FAILED_LOGINS = {}                                     # ip -> (count, locked until)


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
<input id="pin" type="password" {'autocomplete="current-password" style="font-size:18px;letter-spacing:.05em"' if CLOUD else 'inputmode="numeric" autocomplete="current-password" maxlength="8"'} autofocus>
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


def pick_payload(symbol):
    with LOCK:
        item = PICKS.get(symbol)
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
                            "urls": [] if CLOUD else local_addresses()})
        elif url.path == "/api/pick":
            p = pick_payload(q.get("symbol", [""])[0].upper())
            self.send(200 if p else 404, p or {"error": "not found"})
        elif url.path == "/api/breakout":
            p = breakout_payload(q.get("symbol", [""])[0].upper())
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
        if path == "/api/logout":
            return self.send_cookie("", 0)
        if not self.can_admin():                        # everything below changes settings
            return self.send(403, {"error": "Settings can only be changed on the PC."})
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
            ok = hmac.compare_digest(hashlib.sha256(given.encode()).digest(), hashlib.sha256(PASSWORD.encode()).digest())
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
    threading.Thread(target=scanner, daemon=True).start()
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
