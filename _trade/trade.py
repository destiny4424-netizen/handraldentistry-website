"""Handral Trading Desk (installed as meridian-trade): a personal trading app on your own Dhan account. Watchlists, every stock,
index, future and option, option chains, charts, real orders (with a confirm step and safety
checks), positions, holdings, funds, and insights into your own trading.

Run:  python3 trade.py            (Python 3.9+, nothing else to install)
      python3 trade.py --demo     with a pretend Dhan account (nothing real is touched)
"""
import hashlib
import hmac
import json
import os
import secrets
import sys
import threading
import time
import traceback
from datetime import datetime, timedelta, timezone
from http.cookies import SimpleCookie
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import dhan as dhanlib                                  # noqa: E402
import insights                                         # noqa: E402
from autotrader import AutoTrader                       # noqa: E402
from rules import Rules                                 # noqa: E402
import mcx                                              # noqa: E402
from webpush import Push                                # noqa: E402
from alerts import Alerts, KINDS as ALERT_KINDS          # noqa: E402
from movers import Movers                               # noqa: E402
from instruments import Instruments, MASTER_URL, public  # noqa: E402

VERSION = "24 Nov 2026 · trail stops with − / +"
IST = timezone(timedelta(hours=5, minutes=30))
DEMO = "--demo" in sys.argv or os.environ.get("MT_DEMO") == "1"
CLOUD = os.environ.get("MT_CLOUD") == "1"
PASSWORD = os.environ.get("MT_PASSWORD", "")
PUBLIC_IP = os.environ.get("MT_PUBLIC_IP", "")
HOST = os.environ.get("MT_HOST", "127.0.0.1")
PORT = int(os.environ.get("MT_PORT", "3050"))
DATA = Path(os.environ.get("MT_DATA_DIR") or (HERE / ".data-demo" if DEMO else Path.home() / ".meridian-trade"))
API_BASE = os.environ.get("MT_DHAN_API", "https://api.dhan.co/v2")
MASTER = os.environ.get("MT_MASTER_URL", MASTER_URL)

DATA.mkdir(parents=True, exist_ok=True)
SETTINGS_FILE = DATA / "settings.json"
AUDIT = DATA / "orders-log.jsonl"
LOCK = threading.RLock()

DEFAULT_LIMITS = {"max_order_value": 1000000, "daily_loss_limit": 0, "max_trades_day": 5,
                  "lots_warn": 10}
DEFAULT_WATCH = [{"name": "Indices", "keys": []}, {"name": "My stocks", "keys": []}]


def now():
    return datetime.now(IST)


def load_settings():
    try:
        return json.loads(SETTINGS_FILE.read_text())
    except (OSError, ValueError):
        return {}


def save_settings(**kw):
    with LOCK:
        s = load_settings()
        s.update(kw)
        tmp = SETTINGS_FILE.with_suffix(".tmp")
        tmp.write_text(json.dumps(s, indent=1))
        os.chmod(tmp, 0o600)
        os.replace(tmp, SETTINGS_FILE)
        SETTINGS.clear()
        SETTINGS.update(s)


SETTINGS = load_settings()
if not SETTINGS.get("session_key"):
    save_settings(session_key=secrets.token_hex(32))

D = dhanlib.Dhan(base=API_BASE)
INST = Instruments(str(DATA), MASTER)
STORE = insights.Store(str(DATA / "trades.db"))
STATUS = {"token_source": "", "token_note": "", "sync": "", "sync_at": None, "feed_error": ""}


# ---- Dhan login ------------------------------------------------------------------------------
def pick_token():
    if SETTINGS.get("client_id") and SETTINGS.get("access_token"):
        D.client_id, D.token = SETTINGS["client_id"], SETTINGS["access_token"]
        STATUS["token_source"] = "own"
    else:
        D.client_id = D.token = ""
        STATUS["token_source"] = ""


def keep_token():
    """Renew the token when less than 4 hours are left: a token pasted once keeps going."""
    while True:
        try:
            pick_token()
            if STATUS["token_source"] == "own" and not DEMO:
                exp = dhanlib.token_expiry(D.token)
                left = exp - time.time() if exp else None
                if left is not None and 0 < left < 4 * 3600:
                    new = D.renew()
                    if new:
                        save_settings(access_token=new, saved_at=now().strftime("%d %b %Y %H:%M"))
                        D.token = new
                        STATUS["token_note"] = "Renewed " + now().strftime("%d %b %H:%M")
                elif left is not None and left <= 0:
                    STATUS["token_note"] = "The token has expired. Paste a new one in Settings."
        except dhanlib.DhanError as e:
            STATUS["token_note"] = f"Renewing failed: {e}"
        except Exception as e:                         # never let this thread die
            STATUS["token_note"] = f"Token check failed: {e}"
        time.sleep(600)


# ---- live prices -----------------------------------------------------------------------------
QUOTES = {}                                            # key -> quote dict
WANT = {}                                              # key -> last time a screen asked for it
_flip = [0]


def want(keys):
    t = time.time()
    for k in keys:
        if k in INST.by_key:
            WANT[k] = t


def feed():
    while True:
        time.sleep(1.05)
        try:
            if not D.ready:
                continue
            t = time.time()
            keys = [k for k, w in list(WANT.items()) if t - w < 20]
            for k in [k for k, w in list(WANT.items()) if t - w > 120]:
                WANT.pop(k, None)
            if not keys:
                continue
            idx = [k for k in keys if k.startswith("IDX_I:")]
            rest = [k for k in keys if not k.startswith("IDX_I:")]
            _flip[0] ^= 1
            # One request a second: the rest (full quotes) most seconds, indices (OHLC) every other.
            if idx and (not rest or _flip[0]):
                got = D.quote({"IDX_I": [k.split(":")[1] for k in idx]}, "ohlc")
            else:
                by = {}
                for k in rest[:1000]:
                    seg, sid = k.split(":")
                    by.setdefault(seg, []).append(sid)
                got = D.quote(by, "quote")
            for (seg, sid), q in got.items():
                QUOTES[f"{seg}:{sid}"] = shape_quote(q)
            STATUS["feed_error"] = ""
        except dhanlib.DhanError as e:
            STATUS["feed_error"] = str(e)
            time.sleep(3)
        except Exception as e:
            STATUS["feed_error"] = f"Price feed problem: {e}"
            time.sleep(3)


def shape_quote(q):
    ltp = float(q.get("last_price") or 0)
    o = q.get("ohlc") or {}
    prev = float(o.get("close") or 0)
    chg = float(q["net_change"]) if q.get("net_change") not in (None, "") else (ltp - prev if prev else 0)
    base = prev or (ltp - chg)
    d = q.get("depth") or {}
    return {"ltp": ltp, "chg": round(chg, 2), "pct": round(100 * chg / base, 2) if base else 0,
            "open": o.get("open"), "high": o.get("high"), "low": o.get("low"), "prev": prev,
            "vol": q.get("volume"), "oi": q.get("oi"), "avg": q.get("average_price"),
            "uc": q.get("upper_circuit_limit"), "lc": q.get("lower_circuit_limit"),
            "bids": [[b.get("price"), b.get("quantity"), b.get("orders")] for b in (d.get("buy") or [])][:5],
            "asks": [[a.get("price"), a.get("quantity"), a.get("orders")] for a in (d.get("sell") or [])][:5],
            "t": time.time()}


# ---- orders: checks before anything goes to Dhan ----------------------------------------------
RECENT = {}                                            # order signature -> time (double-tap guard)
EQ_PRODUCTS = ("CNC", "INTRADAY", "MTF")
FNO_PRODUCTS = ("INTRADAY", "MARGIN")


def limits():
    return dict(DEFAULT_LIMITS, **(SETTINGS.get("limits") or {}))


def round_tick(p, tick):
    if not p:
        return 0.0
    tick = tick or 0.05
    return round(round(float(p) / tick) * tick, 2)


def today_pnl(positions):
    return sum(float(p.get("realizedProfit") or 0) + float(p.get("unrealizedProfit") or 0) for p in positions)


def build_order(b):
    """Check an order from the ticket and turn it into Dhan's fields. Returns (order, instrument,
    problems that stop it, warnings to show on the confirm screen)."""
    stop, warn = [], []
    it = INST.get(str(b.get("key") or ""))
    if not it:
        return None, None, ["Pick an instrument first."], []
    if it["kind"] == "INDEX":
        return None, it, ["An index itself can't be traded. Trade its futures or options."], []
    side = str(b.get("side") or "").upper()
    product = str(b.get("product") or "").upper()
    otype = str(b.get("type") or "").upper()
    if side not in dhanlib.SIDES:
        stop.append("Choose Buy or Sell.")
    eq = it["seg"] in ("NSE_EQ", "BSE_EQ")
    if product not in (EQ_PRODUCTS if eq else FNO_PRODUCTS):
        stop.append("Choose " + ("Delivery, Intraday or MTF." if eq else "Intraday or Carry forward (NRML)."))
    if otype not in dhanlib.ORDER_TYPES:
        stop.append("Choose the order type.")
    try:
        qty = int(b.get("qty") or 0)
    except (TypeError, ValueError):
        qty = 0
    lot = it["lot"] if not eq else 1
    if qty <= 0:
        stop.append("Enter the quantity.")
    elif lot > 1 and qty % lot:
        stop.append(f"Quantity must be a multiple of the lot size ({lot}).")
    tick = it["tick"]
    price = round_tick(b.get("price"), tick)
    trig = round_tick(b.get("trigger"), tick)
    if otype in ("LIMIT", "STOP_LOSS") and price <= 0:
        stop.append("Enter the limit price.")
    if otype in ("STOP_LOSS", "STOP_LOSS_MARKET") and trig <= 0:
        stop.append("Enter the trigger price.")
    if otype == "STOP_LOSS" and price > 0 and trig > 0:
        if side == "BUY" and trig > price:
            stop.append("For a buy stop-loss the trigger must be at or below the limit price.")
        if side == "SELL" and trig < price:
            stop.append("For a sell stop-loss the trigger must be at or above the limit price.")
    validity = "IOC" if str(b.get("validity") or "").upper() == "IOC" else "DAY"
    q = QUOTES.get(it["key"]) or {}
    ltp = float(q.get("ltp") or 0)
    ref = price or trig or ltp
    o = {"securityId": it["id"], "exchangeSegment": it["seg"], "transactionType": side, "quantity": qty,
         "orderType": otype, "productType": product, "price": price if otype in ("LIMIT", "STOP_LOSS") else 0,
         "triggerPrice": trig if otype in ("STOP_LOSS", "STOP_LOSS_MARKET") else 0, "validity": validity,
         "afterMarketOrder": bool(b.get("amo")), "amoTime": str(b.get("amo_time") or "OPEN"),
         "slice": bool(b.get("slice")), "correlationId": str(b.get("cid") or "")[:25]}
    sup = b.get("super") or None
    if sup and (sup.get("target") or sup.get("sl")):
        if otype not in ("LIMIT", "MARKET"):
            stop.append("A super order's entry must be Limit or Market.")
        entry = price if otype == "LIMIT" else round_tick(ltp, tick)
        if entry <= 0:
            stop.append("No live price yet for the market entry; use a limit price.")
        tgt, sl = round_tick(sup.get("target"), tick), round_tick(sup.get("sl"), tick)
        if side == "BUY" and ((tgt and tgt <= entry) or (sl and sl >= entry)):
            stop.append("For a buy: target above the entry, stop-loss below it.")
        if side == "SELL" and ((tgt and tgt >= entry) or (sl and sl <= entry)):
            stop.append("For a sell: target below the entry, stop-loss above it.")
        o["super"] = {"price": entry, "targetPrice": tgt, "stopLossPrice": sl,
                      "trailingJump": round_tick(sup.get("trail"), tick)}
        if not sl:
            warn.append("This super order has no stop-loss leg.")
    mult = 1
    if it["seg"] == "MCX_COMM":
        mult = it.get("mult")
        if not mult:
            stop.append(f"The app doesn't know the contract size of {it['ul']} yet, so it can't work out the value "
                        "or the risk. Use Dhan's own app for this one.")
            mult = 1
    value = qty * ref * mult
    lim = limits()
    if lim.get("max_order_value") and value > float(lim["max_order_value"]) and not b.get("big_ok"):
        stop.append(f"This order is worth {insights.rupees(value)}, above your limit of "
                    f"{insights.rupees(float(lim['max_order_value']))}. Tick 'I'm sure' to send it anyway.")
        o["_needs_big_ok"] = True
    if lot > 1 and lim.get("lots_warn") and qty // lot >= int(lim["lots_warn"]):
        warn.append(f"{qty // lot} lots: a big position.")
    if otype == "MARKET" and not eq and it["opt"]:
        warn.append("Market orders on options can fill far from the last price when the spread is wide. A limit order is safer.")
    if ltp and price and otype == "LIMIT" and abs(price - ltp) / ltp > 0.05:
        warn.append(f"Your limit price is {abs(price - ltp) / ltp * 100:.1f}% away from the last price ₹{ltp:,.2f}.")
    if ltp and q.get("uc") and q.get("lc") and price and not (float(q["lc"]) <= price <= float(q["uc"])):
        stop.append(f"The price is outside today's circuit limits (₹{q['lc']} – ₹{q['uc']}).")
    o["_value"] = round(value, 2)
    o["_mult"] = mult
    return o, it, stop, warn


def get_positions():
    """Dhan's positions, with MCX open P&L corrected (see mcx.py). Used everywhere: screens, loss limit, alerts."""
    return mcx.fix_positions(D.positions(), QUOTES)


def positions_view(pos):
    """Dhan's positions with names and the live price for display. The P&L is Dhan's own
    (realizedProfit + unrealizedProfit): Dhan applies each contract's multiplier (MCX, currency) itself.
    Working it out here from the "multiplier" field gave options P&L 1,000 times too big."""
    keys = []
    for p in pos:
        k = f"{p.get('exchangeSegment')}:{p.get('securityId')}"
        it = INST.get(k)
        p["_key"] = k
        p["_name"] = it["name"] if it else p.get("tradingSymbol")
        p["_lot"] = it["lot"] if it else 1
        p["_tick"] = it["tick"] if it else 0.05
        p["_mult"] = p.get("_mult") or ((it.get("mult") or 1) if it and it["seg"] == "MCX_COMM" else 1)
        if int(p.get("netQty") or 0):
            keys.append(k)
            lq = QUOTES.get(k)
            if lq and lq.get("ltp"):
                p["_ltp"] = lq["ltp"]
            else:                                      # no live price yet: work it back from Dhan's P&L
                net = int(p["netQty"])
                cost = float(p.get("buyAvg") or 0) if net > 0 else float(p.get("sellAvg") or 0)
                if cost:
                    p["_ltp"] = round(cost + float(p.get("unrealizedProfit") or 0) / (net * float(p.get("_mult") or 1)), 2)
    return pos, keys


WORKING = ("PENDING", "TRANSIT", "TRIGGERED", "PART_TRADED")


def attach_stops(pos):
    """The stop-loss working for each open position: a pending SL / SL-M order on the closing side, or the
    stop-loss leg of a Super order. Each position gets p["_stops"] (usually one)."""
    open_ = [p for p in pos if int(p.get("netQty") or 0)]
    if not open_:
        return pos
    try:
        orders = D.orders()
    except dhanlib.DhanError:
        orders = []
    try:
        sup = D.super_orders()
    except dhanlib.DhanError:
        sup = []
    for p in open_:
        net, sid, seg = int(p["netQty"]), str(p.get("securityId")), p.get("exchangeSegment")
        close_side, entry_side = ("SELL", "BUY") if net > 0 else ("BUY", "SELL")
        stops = []
        for o in orders:
            if str(o.get("securityId")) == sid and o.get("exchangeSegment") == seg and o.get("transactionType") == close_side \
                    and o.get("orderType") in ("STOP_LOSS", "STOP_LOSS_MARKET") and o.get("orderStatus") in WORKING \
                    and not o.get("legName"):
                stops.append({"kind": "order", "order_id": str(o["orderId"]), "trigger": float(o.get("triggerPrice") or 0),
                              "price": float(o.get("price") or 0), "type": o["orderType"],
                              "qty": int(o.get("remainingQuantity") or o.get("quantity") or 0)})
        for s in sup:
            if str(s.get("securityId")) != sid or s.get("exchangeSegment") != seg or s.get("transactionType") != entry_side:
                continue
            for leg in s.get("legDetails") or []:
                if leg.get("legName") == "STOP_LOSS_LEG" and leg.get("orderStatus") in WORKING:
                    stops.append({"kind": "super", "order_id": str(s["orderId"]), "trigger": float(leg.get("price") or 0),
                                  "trail": float(leg.get("trailingJump") or 0),
                                  "qty": int(leg.get("remainingQuantity") or 0)})
        p["_stops"] = stops
    return pos


def stop_side_ok(net, trigger, ltp):
    """A stop must stay on the losing side of the price, or it fills at once."""
    if not ltp:
        return ""
    if net > 0 and trigger >= ltp:
        return f"A stop for a long position must be below the price (₹{ltp:,.2f}); ₹{trigger:,.2f} would sell right away."
    if net < 0 and trigger <= ltp:
        return f"A stop for a short position must be above the price (₹{ltp:,.2f}); ₹{trigger:,.2f} would buy right away."
    return ""


def move_stop(b):
    """Move a position's stop-loss to a new trigger (the +/- buttons), or add one if it has none."""
    key = str(b.get("key") or "")
    it = INST.get(key)
    if not it:
        return 400, {"error": "Unknown instrument"}
    pos = positions_view(get_positions())[0]
    p = next((x for x in pos if x.get("_key") == key and int(x.get("netQty") or 0)), None)
    if not p:
        return 400, {"error": "There is no open position in this instrument any more."}
    net = int(p["netQty"])
    trigger = round_tick(float(b.get("trigger") or 0), it["tick"])
    if trigger <= 0:
        return 400, {"error": "Choose the stop price."}
    ltp = float((QUOTES.get(key) or {}).get("ltp") or p.get("_ltp") or 0)
    bad = stop_side_ok(net, trigger, ltp)
    if bad:
        return 400, {"error": bad}
    oid, kind = str(b.get("order_id") or ""), b.get("kind")
    if not oid:                                        # no stop yet: place an SL-M for the whole position
        return place({"key": key, "side": "SELL" if net > 0 else "BUY", "qty": abs(net), "product": p.get("productType"),
                      "type": "STOP_LOSS_MARKET", "trigger": trigger, "validity": "DAY"})
    if kind == "super":
        r = D.modify_super_leg(oid, "STOP_LOSS_LEG", {"stopLossPrice": trigger, "trailingJump": float(b.get("trail") or 0)})
    else:
        o = D.order(oid)
        if o.get("orderStatus") not in WORKING:
            return 400, {"error": f"That stop order is {str(o.get('orderStatus') or 'gone').lower()} now; refresh."}
        otype = o.get("orderType") or "STOP_LOSS_MARKET"
        price = 0.0
        if otype == "STOP_LOSS":                       # keep the same gap between trigger and limit price
            gap = float(o.get("price") or 0) - float(o.get("triggerPrice") or 0)
            price = round_tick(trigger + gap, it["tick"])
        r = D.modify_order(oid, {"orderType": otype, "quantity": int(o.get("quantity") or abs(net)), "price": price,
                                 "triggerPrice": trigger, "validity": o.get("validity") or "DAY"})
    audit("stop_moved", sym=it["sym"], order_id=oid, trigger=trigger, reply=r)
    return 200, {"ok": True, "trigger": trigger}


def reduces_position(o, positions):
    for p in positions:
        if str(p.get("securityId")) == o["securityId"] and p.get("exchangeSegment") == o["exchangeSegment"]:
            net = int(p.get("netQty") or 0)
            if (net > 0 and o["transactionType"] == "SELL" and o["quantity"] <= net) or \
                    (net < 0 and o["transactionType"] == "BUY" and o["quantity"] <= -net):
                return True
    return False


def audit(event, **kw):
    try:
        with open(AUDIT, "a") as f:
            f.write(json.dumps(dict(kw, event=event, at=now().strftime("%Y-%m-%d %H:%M:%S"))) + "\n")
    except OSError:
        pass


def is_closing(o, positions):
    """An order that only reduces what you already hold: an open position, or delivery shares you own."""
    if reduces_position(o, positions):
        return True
    if o["transactionType"] == "SELL" and o["exchangeSegment"] in ("NSE_EQ", "BSE_EQ") and o["productType"] in ("CNC", "MTF"):
        it = INST.get(f"{o['exchangeSegment']}:{o['securityId']}") or {}
        try:
            for h in D.holdings():
                if str(h.get("securityId")) == o["securityId"] or (it and h.get("tradingSymbol") == it.get("sym")):
                    return int(h.get("availableQty") or h.get("totalQty") or 0) >= o["quantity"]
        except dhanlib.DhanError:
            pass
    return False


def rules_level(info):
    lv, level = info.get("levels") or {}, info.get("level")
    return dict(level, _ukey=lv.get("ukey"), _under=lv.get("under"), _tol=lv.get("tol")) if level else None


def place(b, internal=False, level=None):
    """internal: an order the rules engine sends itself after a confirmation (level already checked)."""
    o, it, stop, warn = build_order(b)
    if stop:
        return 400, {"error": " ".join(stop), "problems": stop, "needs_big_ok": bool(o and o.get("_needs_big_ok"))}
    lim = limits()
    positions = []
    try:
        positions = get_positions()
    except dhanlib.DhanError:
        pass
    opening = not is_closing(o, positions)
    loss_lim = float(lim.get("daily_loss_limit") or 0)
    pnl = today_pnl(positions)
    if loss_lim and pnl <= -loss_lim and opening:
        return 400, {"error": f"Today's P&L is {insights.rupees(pnl)}, past your daily loss limit of "
                              f"{insights.rupees(loss_lim)}. Only orders that close a position are allowed today."}
    if BOT and BOT.hands_off() and opening:
        return 400, {"error": "Hands off: the auto-trader is on, so by hand you can only close positions. "
                              "Pause the bot (or turn Hands off off after the market closes) to trade yourself."}
    if RULES and opening:
        probs, info = RULES.check(b, o, it, lim.get("max_trades_day"), internal=internal)
        if probs:
            return 400, {"error": " ".join(probs), "problems": probs, "rules": info}
        level = level or info.get("level")
        if info.get("rules") and b.get("confirm") and not internal:
            bull = (o["transactionType"] == "BUY") != (it.get("opt") == "PE")
            a = RULES.arm(b, o, it, rules_level(info), bull)
            return 200, {"ok": True, "armed": a, "sym": it["name"],
                         "message": f"Waiting for a 15-minute candle to react at {a['level']['name']} "
                                    f"{a['level']['price']:,} on {a['under']}. Nothing is sent to Dhan until then."}
    sig = (o["securityId"], o["transactionType"], o["quantity"], o["price"], o["orderType"])
    t = time.time()
    if t - RECENT.get(sig, 0) < 4 and not b.get("repeat_ok"):
        return 409, {"error": "You sent this exact order a moment ago. It wasn't sent again (double tap?)."}
    RECENT[sig] = t
    clean = {k: v for k, v in o.items() if not k.startswith("_")}
    try:
        if o.get("super"):
            s = o["super"]
            r = D.place_super_order(dict(clean, price=s["price"], targetPrice=s["targetPrice"],
                                         stopLossPrice=s["stopLossPrice"], trailingJump=s["trailingJump"]))
        else:
            r = D.place_order(clean)
    except dhanlib.DhanError as e:
        audit("order_failed", order=clean, sym=it["sym"], error=str(e), code=e.code)
        RECENT.pop(sig, None)
        msg = str(e)
        if e.code == "DH-903" or "static" in msg.lower() or " ip" in msg.lower():
            msg += f" If Dhan says the IP isn't allowed, set this server's IP ({PUBLIC_IP or 'see Settings'}) as your static IP on web.dhan.co → DhanHQ Trading APIs."
        return 400, {"error": msg, "code": e.code}
    rows = r if isinstance(r, list) else [r]
    audit("order_sent", order=clean, sym=it["sym"], reply=rows, opening=opening, internal=internal)
    if RULES:
        RULES.record_sent(o, it, rows, level, opening, confirmed=internal)
    return 200, {"ok": True, "orders": rows, "sym": it["name"], "warnings": warn}


def preview(b):
    o, it, stop, warn = build_order(dict(b, big_ok=True))
    out = {"problems": stop, "warnings": warn}
    if not o:
        return out
    out.update(value=o["_value"], instrument=public(it) if it else None)
    big = limits().get("max_order_value")
    out["needs_big_ok"] = bool(big and o["_value"] > float(big))
    if not stop:
        try:
            m = D.margin({**o, "price": o["price"] or (o.get("super") or {}).get("price") or
                          float((QUOTES.get(it["key"]) or {}).get("ltp") or 0)})
            out["margin"] = {k: m.get(k) for k in ("totalMargin", "availableBalance", "insufficientBalance",
                                                   "brokerage", "leverage", "spanMargin", "exposureMargin")}
            if float(m.get("insufficientBalance") or 0) > 0:
                out["warnings"].append(f"Not enough funds: {insights.rupees(float(m['insufficientBalance']))} short.")
        except dhanlib.DhanError as e:
            out["margin_error"] = str(e)
        kind = it["kind"]
        out["charges"] = insights.estimate_charges(kind, o["productType"], o["transactionType"], o["quantity"],
                                                   o["price"] or float((QUOTES.get(it["key"]) or {}).get("ltp") or 0))
        sup = o.get("super")
        if sup and sup.get("stopLossPrice"):
            risk = abs(sup["price"] - sup["stopLossPrice"]) * o["quantity"] * o["_mult"]
            out["risk"] = round(risk, 2)
            if sup.get("targetPrice"):
                out["reward"] = round(abs(sup["targetPrice"] - sup["price"]) * o["quantity"] * o["_mult"], 2)
        try:
            positions = get_positions()
            lim = float(limits().get("daily_loss_limit") or 0)
            pnl = today_pnl(positions)
            out["day_pnl"] = round(pnl, 2)
            opening = not is_closing(o, positions)
            out["opening"] = opening
            if lim and pnl <= -lim and opening:
                out["problems"].append("You're past today's loss limit: only closing orders are allowed.")
            if RULES and opening:
                probs, info = RULES.check(b, o, it, limits().get("max_trades_day"))
                out["problems"] += probs
                info.pop("levels", None)
                out["rules"] = info
        except dhanlib.DhanError:
            pass
    return out


# ---- insights sync ---------------------------------------------------------------------------
SYNC_LOCK = threading.Lock()


def sync(full=False):
    if not D.ready or not SYNC_LOCK.acquire(blocking=False):
        return
    try:
        today = now().date()
        STATUS["sync"] = "Reading today's trades"
        STORE.put_day(D.trades_today(), today.isoformat())
        done = STORE.meta("history_until")
        start = (today - timedelta(days=int(SETTINGS.get("history_days") or 365)))
        if done and not full:
            start = max(start, datetime.strptime(done, "%Y-%m-%d").date() - timedelta(days=3))
        end = today - timedelta(days=1)
        d = start
        total = 0
        while d <= end:
            chunk_end = min(end, d + timedelta(days=30))
            for page in range(500):
                STATUS["sync"] = f"Importing your trades: {d:%d %b %Y} – {chunk_end:%d %b %Y}"
                rows = D.trade_history(d.isoformat(), chunk_end.isoformat(), page)
                if not rows:
                    break
                total += STORE.put_history(rows)
                if len(rows) < 20:
                    break
            d = chunk_end + timedelta(days=1)
        if end >= start:
            STORE.set_meta("history_until", end.isoformat())
        STATUS["sync"] = ""
        STATUS["sync_at"] = now().strftime("%d %b %H:%M")
    except dhanlib.DhanError as e:
        STATUS["sync"] = f"Couldn't read your trades: {e}"
    except Exception as e:
        STATUS["sync"] = f"Trade import failed: {e}"
        traceback.print_exc()
    finally:
        SYNC_LOCK.release()


def sync_loop():
    time.sleep(5)
    while True:
        t = now()
        market = t.weekday() < 5 and (9, 0) <= (t.hour, t.minute) <= (16, 0)
        sync()
        time.sleep(120 if market else 1800)


def instruments_loop():
    while True:
        try:
            INST.load()
        except Exception as e:
            INST.error = f"Instrument list failed: {e}"
        time.sleep(3600 * 6)


# ---- option chain ----------------------------------------------------------------------------
CHAIN_CACHE = {}


def chain(ul, expiry):
    expiries = INST.chain_info(ul)
    if not expiries:
        return {"error": f"No options found for {ul}."}
    expiry = expiry if expiry in expiries else expiries[0]
    keys = INST.chain_keys(ul, expiry)
    under = INST.underlying(ul)
    spot, data = 0.0, {}
    ck = (ul, expiry)
    hit = CHAIN_CACHE.get(ck)
    if hit and time.time() - hit[0] < 3:
        spot, data = hit[1], hit[2]
    elif under:
        try:
            d = D.option_chain(under[1], under[0], expiry)
            spot, data = float(d.get("last_price") or 0), d.get("oc") or {}
            CHAIN_CACHE[ck] = (time.time(), spot, data)
        except dhanlib.DhanError as e:
            if hit:
                spot, data = hit[1], hit[2]
            else:
                return {"error": str(e), "expiries": expiries, "expiry": expiry}
    strikes = sorted({k[0] for k in keys})
    by_strike = {}
    for k, v in data.items():
        try:
            by_strike[round(float(k), 2)] = v
        except ValueError:
            continue
    rows = []
    for s in strikes:
        row = {"strike": s}
        d = by_strike.get(round(s, 2)) or {}
        for opt in ("CE", "PE"):
            key = keys.get((s, opt))
            x = d.get(opt.lower()) or {}
            g = x.get("greeks") or {}
            oi, poi = x.get("oi") or 0, x.get("previous_oi") or 0
            row[opt] = {"key": key, "ltp": x.get("last_price"), "oi": oi, "chg_oi": (oi - poi) if poi else None,
                        "iv": x.get("implied_volatility"), "delta": g.get("delta"), "theta": g.get("theta"),
                        "bid": x.get("top_bid_price"), "ask": x.get("top_ask_price"), "vol": x.get("volume"),
                        "prev": x.get("previous_close_price")} if key else None
        rows.append(row)
    ce_oi = sum((r["CE"] or {}).get("oi") or 0 for r in rows)
    pe_oi = sum((r["PE"] or {}).get("oi") or 0 for r in rows)
    lot = next((INST.by_key[k]["lot"] for k in keys.values()), 1)
    ukey = INST.under.get(ul)
    if ukey:
        want([ukey])
        if not spot:
            spot = float((QUOTES.get(ukey) or {}).get("ltp") or 0)
    return {"ul": ul, "expiry": expiry, "expiries": expiries, "spot": spot, "rows": rows, "lot": lot,
            "pcr": round(pe_oi / ce_oi, 2) if ce_oi else None, "ukey": ukey,
            "futures": [k for _, k in (INST.futs.get(ul) or [])][:3]}


# ---- charts ----------------------------------------------------------------------------------
def candles(key, tf):
    it = INST.get(key)
    if not it:
        return {"error": "Unknown instrument"}
    t = now()
    inst = it["kind"]
    try:
        if tf == "D":
            d = D.daily(it["id"], it["seg"], inst, (t - timedelta(days=365)).strftime("%Y-%m-%d"),
                        (t + timedelta(days=1)).strftime("%Y-%m-%d"))
        else:
            back = {"1": 3, "5": 6, "15": 20, "60": 60}.get(tf, 6)
            d = D.intraday(it["id"], it["seg"], inst, tf, (t - timedelta(days=back)).strftime("%Y-%m-%d 09:15:00"),
                           t.strftime("%Y-%m-%d %H:%M:%S"))
    except dhanlib.DhanError as e:
        return {"error": str(e)}
    ks = ("timestamp", "open", "high", "low", "close", "volume")
    if not all(isinstance(d.get(k), list) for k in ks):
        return {"rows": []}
    return {"rows": [[int(a), o, h, l, c, v] for a, o, h, l, c, v in zip(*(d[k] for k in ks))]}


# ---- rules mode -----------------------------------------------------------------------------
RULES = None


def rules_loop():
    time.sleep(10)
    while True:
        try:
            if D.ready and RULES and RULES.market_open():
                waiting = [a for a in RULES.s["armed"] if a["status"] == "waiting"]
                want([a["key"] for a in waiting] + [a["ukey"] for a in waiting])
                RULES.tick()
        except Exception:
            traceback.print_exc()
        time.sleep(30)


# ---- auto-trader ------------------------------------------------------------------------------
BOT = None


def bot_loop():
    time.sleep(8)
    while True:
        try:
            if D.ready and INST.by_key:
                want([p["key"] for p in BOT.s["positions"]] + [x["key"] for x in BOT.s["setups"]])
                BOT.tick()
        except Exception:
            traceback.print_exc()
        time.sleep(20)


# ---- phone notifications --------------------------------------------------------------------
PUSH = None
ALERTS = None
MOVERS = None


def alerts_loop():
    time.sleep(15)
    while True:
        try:
            if D.ready and ALERTS:
                orders, names = [], {}
                try:
                    orders = D.orders()
                    for o in orders:
                        it = INST.get(f"{o.get('exchangeSegment')}:{o.get('securityId')}")
                        names[str(o.get("orderId"))] = it["name"] if it else o.get("tradingSymbol")
                except dhanlib.DhanError:
                    orders = None
                try:
                    pnl = today_pnl(get_positions())
                except dhanlib.DhanError:
                    pnl = None
                ALERTS.tick(orders=orders, names=names, pnl=pnl,
                            loss_limit=float(limits().get("daily_loss_limit") or 0),
                            bot_log=BOT.s["log"] if BOT else None, rules_log=RULES.s["log"] if RULES else None,
                            journal_done=bool(RULES and RULES.today() in RULES.s["journal"]))
        except Exception:
            traceback.print_exc()
        time.sleep(30)


# ---- PIN lock: a second, short lock on top of the 30-day sign-in ----------------------------
PIN_HOURS = 8
OPEN_WITHOUT_PIN = {"/api/status", "/api/pin", "/api/pin/unlock", "/api/pin/reset"}


def hash_pin(pin, salt):
    return hashlib.pbkdf2_hmac("sha256", pin.encode(), bytes.fromhex(salt), 200000).hex()


def pin_set():
    return bool(SETTINGS.get("pin_hash"))


def pin_token(exp):
    key = bytes.fromhex(SETTINGS["session_key"])
    return f"{exp}.{hmac.new(key, b'pin:' + str(exp).encode() + SETTINGS.get('pin_hash', '').encode(), 'sha256').hexdigest()}"


def pin_ok(token):
    if not pin_set():
        return True
    try:
        exp = int(str(token).split(".")[0])
    except ValueError:
        return False
    return exp > time.time() and hmac.compare_digest(pin_token(exp), str(token))


# ---- web server ------------------------------------------------------------------------------
FAILED = {}


def session_token():
    key = bytes.fromhex(SETTINGS["session_key"])
    return hmac.new(key, b"mt:" + hashlib.sha256(PASSWORD.encode()).digest(), "sha256").hexdigest()


def needs_login():
    return bool(PASSWORD)


def mask(cid):
    return (cid[:2] + "•••" + cid[-3:]) if cid and len(cid) > 5 else cid


def status():
    exp = dhanlib.token_expiry(D.token) if D.token else None
    return {"version": VERSION, "demo": DEMO, "connected": D.ready, "client": mask(D.client_id),
            "token_source": STATUS["token_source"], "token_note": STATUS["token_note"],
            "token_expires": datetime.fromtimestamp(exp, IST).strftime("%d %b %H:%M") if exp else None,
            "ip": PUBLIC_IP, "limits": limits(), "instruments": len(INST.by_key), "inst_error": INST.error,
            "sync": STATUS["sync"], "sync_at": STATUS["sync_at"], "feed_error": STATUS["feed_error"],
            "fills": STORE.count()[0], "time": now().strftime("%H:%M:%S"),
            "watchlists": SETTINGS.get("watchlists") or default_watch()}


def default_watch():
    wl = [dict(w) for w in DEFAULT_WATCH]
    wl[0]["keys"] = [INST.under[u] for u in ("NIFTY", "BANKNIFTY", "FINNIFTY", "SENSEX", "INDIAVIX") if u in INST.under]
    wl[1]["keys"] = [INST.under[u] for u in ("RELIANCE", "HDFCBANK", "ICICIBANK", "INFY", "SBIN", "TCS") if u in INST.under]
    return wl


class Handler(BaseHTTPRequestHandler):
    server_version = "HandralTradingDesk"
    sys_version = ""

    def log_message(self, fmt, *a):
        if "/api/quotes" in (a[0] if a else ""):
            return
        sys.stderr.write("%s %s\n" % (now().strftime("%H:%M:%S"), fmt % a))

    # -- plumbing
    def send(self, code, obj, ctype="application/json", extra=None):
        data = obj if isinstance(obj, bytes) else (obj.encode() if isinstance(obj, str) else json.dumps(obj, default=str).encode())
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("X-Frame-Options", "DENY")
        self.send_header("Content-Security-Policy", "default-src 'self'; img-src 'self' data:; style-src 'self' 'unsafe-inline'; "
                                                    "frame-ancestors 'none'; base-uri 'none'; form-action 'self'")
        for k, v in (extra or {}).items():
            self.send_header(k, v)
        self.end_headers()
        if self.command != "HEAD":
            try:
                self.wfile.write(data)
            except (BrokenPipeError, ConnectionResetError):
                pass                                   # the browser left before the answer was ready

    def signed_in(self):
        if not needs_login():
            return True
        c = SimpleCookie(self.headers.get("Cookie", "")).get("mt")
        return bool(c) and hmac.compare_digest(c.value, session_token())

    def client_ip(self):
        return (self.headers.get("X-Forwarded-For") or self.client_address[0]).split(",")[0].strip()

    def body(self):
        if not self.headers.get("Content-Type", "").startswith("application/json"):
            raise ValueError("JSON only")
        if self.headers.get("X-Requested-With") != "meridian-trade":
            raise PermissionError("bad origin")
        n = min(int(self.headers.get("Content-Length") or 0), 100000)
        return json.loads(self.rfile.read(n) or b"{}")

    def static(self, name):
        p = (HERE / "static" / name).resolve()
        if not str(p).startswith(str((HERE / "static").resolve())) or not p.is_file():
            return self.send(404, "Not found", "text/plain")
        ctype = {".html": "text/html; charset=utf-8", ".js": "application/javascript; charset=utf-8",
                 ".css": "text/css; charset=utf-8", ".png": "image/png", ".svg": "image/svg+xml",
                 ".webmanifest": "application/manifest+json"}.get(p.suffix, "application/octet-stream")
        self.send(200, p.read_bytes(), ctype)

    # -- routes
    def do_HEAD(self):
        self.do_GET()

    def do_GET(self):
        u = urlparse(self.path)
        q = {k: v[0] for k, v in parse_qs(u.query).items()}
        path = u.path
        if path == "/healthz":
            return self.send(200, "ok", "text/plain")
        if path in ("/", "/index.html"):
            return self.static("index.html" if self.signed_in() else "login.html")
        if path.startswith("/static/"):
            return self.static(path[len("/static/"):])
        if path in ("/manifest.webmanifest", "/icon.svg"):
            return self.static(path[1:])
        if path == "/sw.js":                             # the service worker must come from the site's root
            return self.static("sw.js")
        if not path.startswith("/api/"):
            return self.send(404, "Not found", "text/plain")
        if not self.signed_in():
            return self.send(401, {"error": "Sign in first."})
        if path not in OPEN_WITHOUT_PIN and not pin_ok(self.headers.get("X-Pin-Token")):
            return self.send(423, {"error": "Locked: enter your PIN.", "locked": True})
        try:
            return self.api_get(path, q)
        except dhanlib.DhanError as e:
            return self.send(502, {"error": str(e), "code": e.code})
        except Exception as e:
            traceback.print_exc()
            return self.send(500, {"error": f"Something went wrong: {e}"})

    def api_get(self, path, q):
        if path == "/api/status":
            return self.send(200, status())
        if path == "/api/search":
            res = INST.search(q.get("q", ""))
            fno = MOVERS.fno_stocks() if MOVERS else set()
            for r in res:
                r["fno"] = r["kind"] == "EQUITY" and r["sym"] in fno
            return self.send(200, {"results": res})
        if path == "/api/instrument":
            it = INST.get(q.get("key", ""))
            return self.send(200, public(it) if it else {"error": "Unknown"})
        if path == "/api/quotes":
            keys = [k for k in q.get("keys", "").split(",") if k][:400]
            want(keys)
            info = {}
            if q.get("info"):
                info = {k: public(INST.by_key[k]) for k in keys if k in INST.by_key}
            liq = MOVERS.liquidity_for(keys, QUOTES) if q.get("liq") and MOVERS else {}
            return self.send(200, {"quotes": {k: QUOTES.get(k) for k in keys}, "info": info, "liq": liq,
                                   "error": STATUS["feed_error"]})
        if path == "/api/chain":
            r = chain(q.get("ul", "NIFTY"), q.get("expiry", ""))
            return self.send(200, r)
        if path == "/api/underlyings":
            return self.send(200, {"list": INST.option_underlyings()})
        if path == "/api/chart":
            return self.send(200, candles(q.get("key", ""), q.get("tf", "5")))
        if path == "/api/orders":
            orders = D.orders()
            try:
                sup = D.super_orders()
            except dhanlib.DhanError:
                sup = []
            for o in orders + sup:
                k = f"{o.get('exchangeSegment')}:{o.get('securityId')}"
                it = INST.get(k)
                o["_key"] = k
                o["_name"] = it["name"] if it else o.get("tradingSymbol")
                o["_lot"] = it["lot"] if it else 1
                o["_tick"] = it["tick"] if it else 0.05
            orders.sort(key=lambda o: str(o.get("updateTime") or o.get("createTime") or ""), reverse=True)
            return self.send(200, {"orders": orders, "super": sup})
        if path == "/api/trades":
            return self.send(200, {"trades": D.trades_today()})
        if path == "/api/positions":
            pos, keys = positions_view(get_positions())
            attach_stops(pos)
            want(keys)
            return self.send(200, {"positions": pos, "pnl": round(today_pnl(pos), 2), "limits": limits()})
        if path == "/api/holdings":
            h = D.holdings()
            keys = []
            for x in h:
                k = INST.under.get(str(x.get("tradingSymbol") or ""))
                x["_key"] = k
                if k:
                    keys.append(k)
                    lq = QUOTES.get(k) or {}
                    x["_ltp"] = lq.get("ltp")
                    x["_pct"] = lq.get("pct")
            want(keys)
            return self.send(200, {"holdings": h})
        if path == "/api/funds":
            return self.send(200, D.funds())
        if path == "/api/insights":
            days = int(q.get("days") or 0) or None
            r = insights.report(STORE.fills(), days=days, max_trades_day=int(limits().get("max_trades_day") or 5))
            c = STORE.count()
            r["fills"], r["first"], r["last"] = c
            r["sync"], r["sync_at"] = STATUS["sync"], STATUS["sync_at"]
            return self.send(200, r)
        if path == "/api/killswitch":
            return self.send(200, D.kill_switch_status())
        if path == "/api/auto":
            return self.send(200, BOT.view())
        if path == "/api/pin":
            return self.send(200, {"set": pin_set(), "hours": PIN_HOURS})
        if path == "/api/market":
            return self.send(200, MOVERS.snapshot())
        if path == "/api/push":
            return self.send(200, {"available": PUSH.available(), "key": PUSH.public_key(),
                                   "phones": [{"device": x["device"], "since": x["since"], "endpoint": x["endpoint"]}
                                              for x in PUSH.subs],
                                   "log": PUSH.log[-8:][::-1], "settings": ALERTS.cfg(), "kinds": ALERT_KINDS,
                                   "history": ALERTS.s["history"][:30]})
        if path == "/api/rules/levels":
            return self.send(200, dict(RULES.levels_for(q.get("key", "")), cfg=RULES.cfg()))
        if path == "/api/rules":
            day = RULES.today()
            return self.send(200, {"cfg": RULES.cfg(), "armed": [a for a in RULES.s["armed"] if a["day"] == day],
                                   "log": RULES.s["log"][:20], "locked": RULES.locked()})
        if path == "/api/rules/plan":
            try:
                pos = get_positions()
            except dhanlib.DhanError:
                pos = []
            wl = SETTINGS.get("watchlists") or default_watch()
            keys = [k for w in wl for k in w.get("keys", [])]
            return self.send(200, RULES.plan(keys, BOT.view() if BOT else None, pos, limits(), today_pnl(pos)))
        if path == "/api/rules/journal":
            day = q.get("day") or RULES.today()
            card = insights.day_card(insights.round_trips(STORE.fills(since=day))[0], day)
            v = RULES.journal_view(day, card["trades"])
            v["summary"], v["notes"] = card["summary"], card["notes"]
            return self.send(200, v)
        if path == "/api/audit":
            try:
                lines = AUDIT.read_text().splitlines()[-100:]
            except OSError:
                lines = []
            return self.send(200, {"log": [json.loads(x) for x in reversed(lines)]})
        return self.send(404, {"error": "No such page"})

    def do_POST(self):
        self.mutate("POST")

    def mutate(self, method):
        path = urlparse(self.path).path
        try:
            b = self.body()
        except PermissionError:
            return self.send(403, {"error": "Refused."})
        except (ValueError, json.JSONDecodeError):
            return self.send(400, {"error": "Bad request."})
        if path == "/api/login":
            return self.login(b)
        if path == "/api/logout":
            return self.send(200, {"ok": True}, extra={"Set-Cookie": "mt=; Max-Age=0; Path=/; HttpOnly; SameSite=Strict"})
        if not self.signed_in():
            return self.send(401, {"error": "Sign in first."})
        if path not in OPEN_WITHOUT_PIN and not pin_ok(self.headers.get("X-Pin-Token")):
            return self.send(423, {"error": "Locked: enter your PIN.", "locked": True})
        try:
            return self.api_post(path, b)
        except dhanlib.DhanError as e:
            audit("dhan_error", path=path, error=str(e))
            return self.send(502, {"error": str(e), "code": e.code})
        except (KeyError, ValueError, TypeError) as e:
            return self.send(400, {"error": f"Missing or wrong value: {e}"})
        except Exception as e:
            traceback.print_exc()
            return self.send(500, {"error": f"Something went wrong: {e}"})

    def api_post(self, path, b):
        if path == "/api/order/preview":
            return self.send(200, preview(b))
        if path == "/api/order":
            code, r = place(b)
            return self.send(code, r)
        if path == "/api/order/modify":
            oid = str(b["order_id"])
            tick = float(b.get("tick") or 0.05)
            if b.get("super_leg"):
                leg = b["super_leg"]
                r = D.modify_super_leg(oid, leg, {"targetPrice": round_tick(b.get("price"), tick),
                                                  "stopLossPrice": round_tick(b.get("price"), tick),
                                                  "trailingJump": round_tick(b.get("trail"), tick),
                                                  "orderType": b.get("type") or "LIMIT", "quantity": b.get("qty") or 0,
                                                  "price": round_tick(b.get("price"), tick)})
            else:
                otype = str(b["type"]).upper()
                if otype not in dhanlib.ORDER_TYPES:
                    return self.send(400, {"error": "Order type"})
                r = D.modify_order(oid, {"orderType": otype, "quantity": int(b["qty"]),
                                         "price": round_tick(b.get("price"), tick) if otype in ("LIMIT", "STOP_LOSS") else 0,
                                         "triggerPrice": round_tick(b.get("trigger"), tick) if otype.startswith("STOP") else 0,
                                         "validity": b.get("validity") or "DAY", "legName": b.get("leg") or ""})
            audit("order_modified", order_id=oid, change=b, reply=r)
            return self.send(200, {"ok": True, "reply": r})
        if path == "/api/stop/move":
            code, r = move_stop(b)
            return self.send(code, r)
        if path == "/api/order/cancel":
            oid = str(b["order_id"])
            r = D.cancel_super(oid, b.get("leg") or "ENTRY_LEG") if b.get("super") else D.cancel_order(oid)
            audit("order_cancelled", order_id=oid, reply=r)
            return self.send(200, {"ok": True, "reply": r})
        if path == "/api/exit-all":
            if b.get("confirm") != "EXIT ALL":
                return self.send(400, {"error": "Type EXIT ALL to confirm."})
            r = D.exit_all()
            audit("exit_all", reply=r)
            return self.send(200, {"ok": True, "reply": r})
        if path == "/api/position/convert":
            r = D.convert_position(b)
            audit("convert", body=b, reply=r)
            return self.send(200, {"ok": True})
        if path == "/api/killswitch":
            on = bool(b.get("on"))
            if on and b.get("confirm") != "STOP":
                return self.send(400, {"error": "Type STOP to confirm."})
            r = D.kill_switch(on)
            audit("kill_switch", on=on, reply=r)
            return self.send(200, {"ok": True, "reply": r})
        if path == "/api/settings/dhan":
            cid, tok = str(b.get("client_id") or "").strip(), str(b.get("token") or "").strip()
            if not cid or len(tok) < 20:
                return self.send(400, {"error": "Enter your Client ID and the access token."})
            test = dhanlib.Dhan(cid, tok, API_BASE)
            try:
                test.funds()
            except dhanlib.DhanError as e:
                return self.send(400, {"error": f"Dhan didn't accept these: {e}"})
            save_settings(client_id=cid, access_token=tok, saved_at=now().strftime("%d %b %Y %H:%M"))
            pick_token()
            threading.Thread(target=sync, daemon=True).start()
            return self.send(200, {"ok": True})
        if path == "/api/settings/limits":
            lim = limits()
            for k in DEFAULT_LIMITS:
                if k in b:
                    lim[k] = max(0, float(b[k] or 0))
            save_settings(limits=lim)
            return self.send(200, {"ok": True, "limits": lim})
        if path == "/api/watchlists":
            wl = b.get("watchlists")
            if not isinstance(wl, list) or len(wl) > 20:
                return self.send(400, {"error": "Bad watchlists"})
            clean = [{"name": str(w.get("name") or "List")[:30], "keys": [k for k in (w.get("keys") or [])
                                                                          if isinstance(k, str) and k in INST.by_key][:100]}
                     for w in wl]
            save_settings(watchlists=clean)
            return self.send(200, {"ok": True})
        if path == "/api/pin/unlock":
            return self.pin_unlock(b)
        if path == "/api/pin/reset":                     # forgot the PIN: the sign-in password clears it
            given = str(b.get("password") or "").strip().lower().replace(" ", "")
            if not (PASSWORD and hmac.compare_digest(hashlib.sha256(given.encode()).digest(),
                                                     hashlib.sha256(PASSWORD.lower().replace(" ", "").encode()).digest())):
                time.sleep(1)
                return self.send(401, {"error": "Wrong password."})
            save_settings(pin_hash=None, pin_salt=None)
            audit("pin_reset")
            return self.send(200, {"ok": True})
        if path == "/api/pin/set":
            pin = str(b.get("pin") or "")
            if not (pin.isdigit() and 4 <= len(pin) <= 8):
                return self.send(400, {"error": "The PIN must be 4 to 8 digits."})
            salt = secrets.token_hex(16)
            save_settings(pin_hash=hash_pin(pin, salt), pin_salt=salt)
            audit("pin_set")
            exp = int(time.time()) + PIN_HOURS * 3600
            return self.send(200, {"ok": True, "token": pin_token(exp), "exp": exp})
        if path == "/api/pin/clear":
            save_settings(pin_hash=None, pin_salt=None)
            audit("pin_cleared")
            return self.send(200, {"ok": True})
        if path == "/api/push/subscribe":
            err = PUSH.subscribe(b.get("subscription"), b.get("device") or "")
            if err:
                return self.send(400, {"error": err})
            PUSH.send("Notifications are on", "This phone will get your trading alerts.", tag="welcome")
            return self.send(200, {"ok": True})
        if path == "/api/push/unsubscribe":
            PUSH.unsubscribe(str(b.get("endpoint") or ""))
            return self.send(200, {"ok": True})
        if path == "/api/push/test":
            if not PUSH.subs:
                return self.send(400, {"error": "No phone has turned notifications on yet."})
            ALERTS.notify("test", "Test from Handral Trading Desk", "If you can read this, alerts work.")
            return self.send(200, {"ok": True})
        if path == "/api/market/liquidity":
            from movers import LIQ_DEFAULTS
            cfg = dict(LIQ_DEFAULTS, **(SETTINGS.get("liquidity") or {}))
            for k, (lo, hi) in {"min_turnover_cr": (0, 100000), "min_avg_lakh": (0, 10000), "min_rvol": (0, 20)}.items():
                if b.get(k) not in (None, ""):
                    v = float(b[k])
                    if not lo <= v <= hi:
                        return self.send(400, {"error": f"{k.replace('_', ' ')} must be between {lo} and {hi}."})
                    cfg[k] = v
            save_settings(liquidity=cfg)
            if MOVERS:
                MOVERS.snap_at = 0
            return self.send(200, {"ok": True, "liq": cfg})
        if path == "/api/alerts/settings":
            err = ALERTS.update_settings(b)
            if err:
                return self.send(400, {"error": err})
            ALERTS.save()
            return self.send(200, {"ok": True, "settings": ALERTS.cfg()})
        if path.startswith("/api/rules/"):
            act = path[len("/api/rules/"):]
            if act == "settings":
                err = RULES.update_settings(b)
            elif act == "cancel":
                err = "" if RULES.cancel(str(b.get("id"))) else "That order is no longer waiting."
            elif act == "journal":
                err = RULES.save_journal(str(b.get("day") or RULES.today())[:10], b)
            else:
                return self.send(404, {"error": "No such action"})
            if err:
                return self.send(400, {"error": err})
            return self.send(200, {"ok": True, "cfg": RULES.cfg()})
        if path.startswith("/api/auto/"):
            act = path[len("/api/auto/"):]
            err = ""
            if act == "arm":
                err = BOT.arm(bool(b.get("on")))
            elif act == "mode":
                err = BOT.set_mode(str(b.get("mode")), str(b.get("confirm") or ""))
            elif act == "settings":
                err = BOT.update_settings(b)
            elif act == "backtest":
                if BOT.job["running"]:
                    err = "The backtest is already running."
                else:
                    threading.Thread(target=BOT.run_backtest, daemon=True).start()
            elif act == "close-all":
                if b.get("confirm") != "CLOSE":
                    err = "Type CLOSE to confirm."
                else:
                    err = BOT.close_all()
            else:
                return self.send(404, {"error": "No such action"})
            if err:
                return self.send(400, {"error": err})
            audit("auto_action", action=act, body={k: v for k, v in b.items() if k != "confirm"})
            return self.send(200, {"ok": True, "auto": BOT.view()})
        if path == "/api/insights/sync":
            threading.Thread(target=sync, kwargs={"full": bool(b.get("full"))}, daemon=True).start()
            return self.send(200, {"ok": True})
        return self.send(404, {"error": "No such action"})

    def do_PUT(self):
        self.mutate("PUT")

    def pin_unlock(self, b):
        ip, t = "pin:" + self.client_ip(), time.time()
        n, until = FAILED.get(ip, (0, 0))
        if until > t:
            return self.send(429, {"error": f"Too many wrong PINs. Try again in {int(until - t) // 60 + 1} min, "
                                            "or sign in again with your password to reset the PIN."})
        if not pin_set():
            return self.send(200, {"ok": True, "token": "", "exp": 0})
        given = str(b.get("pin") or "")
        if not hmac.compare_digest(hash_pin(given, SETTINGS["pin_salt"]), SETTINGS["pin_hash"]):
            n += 1
            FAILED[ip] = (n, t + 900 if n >= 5 else 0)
            time.sleep(1)
            return self.send(401, {"error": "Wrong PIN." + (f" {5 - n} tries left." if n < 5 else "")})
        FAILED.pop(ip, None)
        exp = int(t) + PIN_HOURS * 3600
        return self.send(200, {"ok": True, "token": pin_token(exp), "exp": exp})

    def login(self, b):
        ip, t = self.client_ip(), time.time()
        n, until = FAILED.get(ip, (0, 0))
        if until > t:
            return self.send(429, {"error": f"Too many wrong passwords. Try again in {int(until - t) // 60 + 1} min."})
        given = str(b.get("password") or "").strip().lower().replace(" ", "")
        ok = bool(PASSWORD) and hmac.compare_digest(hashlib.sha256(given.encode()).digest(),
                                                    hashlib.sha256(PASSWORD.lower().replace(" ", "").encode()).digest())
        if not ok:
            n += 1
            FAILED[ip] = (n, t + 900 if n >= 5 else 0)
            time.sleep(1)
            return self.send(401, {"error": "Wrong password."})
        FAILED.pop(ip, None)
        secure = "; Secure" if CLOUD else ""
        return self.send(200, {"ok": True}, extra={
            "Set-Cookie": f"mt={session_token()}; Max-Age=2592000; Path=/; HttpOnly; SameSite=Strict{secure}"})


def main():
    global API_BASE, MASTER
    if DEMO:
        import mockdhan
        mockdhan.serve(3099)
        API_BASE = "http://127.0.0.1:3099/v2"
        D.base = API_BASE
        INST.url = "http://127.0.0.1:3099/scrip-master.csv"
        if not SETTINGS.get("access_token"):
            save_settings(client_id=mockdhan.CLIENT, access_token=mockdhan.TOKEN)
    pick_token()
    INST.load(force=DEMO)
    global BOT
    BOT = AutoTrader(str(DATA), D, INST, QUOTES, now, audit, demo=DEMO)
    global RULES
    RULES = Rules(str(DATA), D, INST, QUOTES, now, audit, demo=DEMO)
    RULES.place_fn = place
    global PUSH, ALERTS
    PUSH = Push(str(DATA), lambda: SETTINGS, save_settings,
                os.environ.get("MT_PUBLIC_URL") or "https://localhost")
    ALERTS = Alerts(str(DATA), PUSH, now)
    global MOVERS
    MOVERS = Movers(D, INST, now, lambda: BOT.hist if BOT else {}, lambda: SETTINGS.get("liquidity"))
    threading.Thread(target=MOVERS.loop, args=(lambda: D.ready and bool(INST.by_key),), daemon=True).start()
    for fn in (keep_token, feed, sync_loop, instruments_loop, bot_loop, rules_loop, alerts_loop):
        threading.Thread(target=fn, daemon=True).start()
    srv = ThreadingHTTPServer((HOST, PORT), Handler)
    srv.daemon_threads = True
    print(f"Handral Trading Desk {VERSION} on http://{HOST}:{PORT}" + ("  (DEMO: pretend Dhan account)" if DEMO else ""))
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
