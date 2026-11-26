"""A pretend Zerodha (Kite Connect v3) for the Zerodha copy's demo mode and tests. It is a Kite-shaped front on the
same pretend market as mockdhan.py (prices, fills, positions), so the Zerodha adapter (kite.py) is tested end to end:
Kite's paths, form fields, statuses and shapes going in, the desk's Dhan-shaped data coming out.
Nothing here touches a real account.

    python mockkite.py 3095      serves it on 127.0.0.1:3095 (ticker on 3094)
"""
import csv
import hashlib
import io
import json
import re
import struct
import sys
import threading
import time
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

import mcx
import mockdhan
from mockdhan import IST

API_KEY = "demoapikey"
API_SECRET = "demoapisecret123"
TOKEN = "demo-kite-access-token"
USER = "ZD1234"
REQUEST = "demo-request"

SEG_EXCH = {"NSE_EQ": "NSE", "BSE_EQ": "BSE", "NSE_FNO": "NFO", "BSE_FNO": "BFO", "MCX_COMM": "MCX"}
SEG_CODE = {"NSE_EQ": 1, "NSE_FNO": 2, "BSE_EQ": 4, "BSE_FNO": 5, "MCX_COMM": 7, "IDX_I": 9}
INDEX_TS = {"NIFTY": "NIFTY 50", "BANKNIFTY": "NIFTY BANK", "FINNIFTY": "NIFTY FIN SERVICE", "SENSEX": "SENSEX",
            "INDIAVIX": "INDIA VIX"}
MON = ["JAN", "FEB", "MAR", "APR", "MAY", "JUN", "JUL", "AUG", "SEP", "OCT", "NOV", "DEC"]
PROD_IN = {"CNC": "CNC", "MIS": "INTRADAY", "NRML": "MARGIN", "MTF": "MTF"}
PROD_OUT = {v: k for k, v in PROD_IN.items()}
TYPE_IN = {"LIMIT": "LIMIT", "MARKET": "MARKET", "SL": "STOP_LOSS", "SL-M": "STOP_LOSS_MARKET"}
TYPE_OUT = {v: k for k, v in TYPE_IN.items()}

M = None
TOK = {}                                               # token -> (seg, id)
REV = {}                                               # (seg, id) -> token
NAME = {}                                              # "EXCH:TS" -> (seg, id)
TS = {}                                                # (seg, id) -> (exch, ts)
CALLS = []                                             # (method, path, params) for tests
OPTS = {"market_protection_unknown": False}            # tests: an older Kite that refuses market_protection


def _ts(i):
    if i["kind"] == "INDEX":
        return INDEX_TS.get(i["sym"], i["sym"])
    if i["kind"] == "EQUITY":
        return i["sym"]
    e = datetime.strptime(i["expiry"], "%Y-%m-%d")
    ul = i.get("ul") or i["sym"].split("-")[0]
    if i["opt"]:
        return f"{ul}{e:%y}{MON[e.month - 1]}{e.day:02d}{int(i['strike'])}{i['opt']}"
    return f"{ul}{e:%y}{MON[e.month - 1]}FUT"


def build():
    TOK.clear(), REV.clear(), NAME.clear(), TS.clear()
    n = 1000
    for (seg, sid), i in M.inst.items():
        n += 1
        tok = (n << 8) | SEG_CODE[seg]
        exch = i["exch"] if seg == "IDX_I" else SEG_EXCH[seg]
        ts = _ts(i)
        TOK[str(tok)], REV[(seg, sid)], NAME[f"{exch}:{ts}"], TS[(seg, sid)] = (seg, sid), str(tok), (seg, sid), (exch, ts)


def instruments_csv():
    out = io.StringIO()
    w = csv.writer(out)
    w.writerow(["instrument_token", "exchange_token", "tradingsymbol", "name", "last_price", "expiry", "strike",
                "tick_size", "lot_size", "instrument_type", "segment", "exchange"])
    for (seg, sid), i in M.inst.items():
        exch, ts = TS[(seg, sid)]
        itype = "EQ" if i["kind"] in ("EQUITY", "INDEX") else (i["opt"] or "FUT")
        segment = "INDICES" if seg == "IDX_I" else exch if i["kind"] == "EQUITY" else f"{exch}-{'OPT' if i['opt'] else 'FUT'}"
        name = (i["name"].upper() if i["kind"] == "EQUITY" else INDEX_TS.get(i["sym"], i["sym"]) if i["kind"] == "INDEX"
                else (i.get("ul") or i["sym"].split("-")[0]))
        lot = i["lot"] if i["kind"] not in ("EQUITY", "INDEX") else 1
        w.writerow([REV[(seg, sid)], sid, ts, name, 0, i["expiry"] or "", i["strike"] if i["opt"] else 0,
                    i["tick"], lot, itype, segment, exch])
    return out.getvalue()


def kerr(code, etype, msg):
    return code, {"status": "error", "message": msg, "error_type": etype, "data": None}


def ok(data):
    return 200, {"status": "success", "data": data}


def dh(method, path, body=None):
    """Ask the pretend market in Dhan's words."""
    mockdhan.LOCAL.m = M
    try:
        return mockdhan.Handler.dispatch(None, method, path, body or {}, {})
    finally:
        mockdhan.LOCAL.m = None


def from_dhan_err(code, r):
    msg = (r or {}).get("errorMessage") or "Error"
    return kerr(400, "OrderException" if (r or {}).get("errorCode") == "DH-906" else "InputException", msg)


def kstatus(o):
    s = o["orderStatus"]
    if s == "TRADED":
        return "COMPLETE"
    if s in ("REJECTED", "CANCELLED"):
        return s
    if s == "TRANSIT":
        return "OPEN PENDING"
    if o["orderType"] in ("STOP_LOSS", "STOP_LOSS_MARKET") and not o.get("_triggered"):
        return "TRIGGER PENDING"
    return "OPEN"


def korder(o):
    seg, sid = o["exchangeSegment"], str(o["securityId"])
    exch, ts = TS[(seg, sid)]
    return {"order_id": o["orderId"], "exchange_order_id": "1" + o["orderId"], "parent_order_id": None,
            "status": kstatus(o), "status_message": o.get("omsErrorDescription") or None, "variety": o.get("_variety", "regular"),
            "tradingsymbol": ts, "exchange": exch, "instrument_token": int(REV[(seg, sid)]),
            "transaction_type": o["transactionType"], "order_type": TYPE_OUT[o["orderType"]], "product": PROD_OUT[o["productType"]],
            "validity": o.get("validity", "DAY"), "price": o["price"], "quantity": o["quantity"], "trigger_price": o["triggerPrice"],
            "average_price": o["averageTradedPrice"], "pending_quantity": o["remainingQuantity"] if o["orderStatus"] not in
            ("TRADED", "CANCELLED", "REJECTED") else 0, "filled_quantity": o["filledQty"], "disclosed_quantity": 0,
            "order_timestamp": o["createTime"], "exchange_timestamp": o["exchangeTime"], "exchange_update_timestamp": o["updateTime"],
            "tag": o.get("correlationId") or None}


def to_dhan(p, require=True):
    """Kite order form -> the pretend market's Dhan order (or an error)."""
    k = NAME.get(f"{p.get('exchange')}:{p.get('tradingsymbol')}")
    if not k:
        return None, kerr(400, "InputException", "Invalid `tradingsymbol`.")
    if p.get("order_type") not in TYPE_IN:
        return None, kerr(400, "InputException", "Invalid `order_type`.")
    if p.get("product") not in PROD_IN:
        return None, kerr(400, "InputException", "Invalid `product`.")
    if p.get("transaction_type") not in ("BUY", "SELL"):
        return None, kerr(400, "InputException", "Invalid `transaction_type`.")
    if "market_protection" in p and OPTS["market_protection_unknown"]:
        return None, kerr(400, "InputException", "Invalid `market_protection`.")
    try:
        qty = int(p.get("quantity") or 0)
    except ValueError:
        qty = 0
    if qty <= 0:
        return None, kerr(400, "InputException", "Invalid `quantity`.")
    return {"securityId": k[1], "exchangeSegment": k[0], "transactionType": p["transaction_type"], "quantity": qty,
            "orderType": TYPE_IN[p["order_type"]], "productType": PROD_IN[p["product"]],
            "price": float(p.get("price") or 0), "triggerPrice": float(p.get("trigger_price") or 0),
            "validity": p.get("validity") or "DAY", "correlationId": p.get("tag") or "", "dhanClientId": mockdhan.CLIENT}, None


def kquote(rows_by, kind):
    out = {}
    for name, (seg, sid) in rows_by:
        code, d = dh("POST", f"/marketfeed/{'quote' if kind == 'quote' else kind}", {seg: [sid]})
        q = ((d.get("data") or {}).get(seg) or {}).get(str(sid))
        if not q:
            continue
        q = dict(q, instrument_token=int(REV[(seg, sid)]))
        if kind == "quote":
            q.pop("net_change", None)
            q["net_change"] = 0                       # Kite's full quote often says 0 here; the adapter works it out
        out[name] = q
    return out


def dispatch(method, path, p, raw_json):
    m = M
    if path == "/session/token" and method == "POST":
        good = hashlib.sha256((API_KEY + REQUEST + API_SECRET).encode()).hexdigest()
        if p.get("api_key") != API_KEY or p.get("request_token") != REQUEST or p.get("checksum") != good:
            return kerr(403, "TokenException", "Token is invalid or has expired.")
        return ok({"user_id": USER, "user_name": "Demo Zerodha", "user_shortname": "Demo", "access_token": TOKEN,
                   "api_key": API_KEY, "login_time": datetime.now(IST).strftime("%Y-%m-%d %H:%M:%S")})
    if path == "/user/profile":
        return ok({"user_id": USER, "user_name": "Demo Zerodha", "email": "demo@example.com"})
    if path == "/user/margins":
        code, f = dh("GET", "/fundlimit")
        eq = {"enabled": True, "net": f["availabelBalance"],
              "available": {"adhoc_margin": 0, "cash": f["sodLimit"], "opening_balance": f["sodLimit"],
                            "live_balance": f["availabelBalance"], "collateral": 0, "intraday_payin": 0},
              "utilised": {"debits": f["utilizedAmount"], "exposure": 0, "span": 0, "option_premium": 0, "payout": 0}}
        return ok({"equity": eq, "commodity": dict(eq, net=0)})
    mo = re.fullmatch(r"/orders/(regular|amo)", path)
    if mo and method == "POST":
        b, bad = to_dhan(p)
        if bad:
            return bad
        b["afterMarketOrder"] = mo.group(1) == "amo"
        bad = mockdhan.check_order(b)
        if bad:
            return kerr(400, "InputException", bad)
        code, r = m.new_order(b)
        if code != 200:
            return from_dhan_err(code, r)
        m.orders[r["orderId"]]["_variety"] = mo.group(1)
        return ok({"order_id": r["orderId"]})
    if path == "/orders" and method == "GET":
        return ok([korder(o) for o in m.orders.values() if o.get("_kind") in (None, "NORMAL")])
    mo = re.fullmatch(r"/orders/(\w+)", path)
    if mo and method == "GET":
        o = m.orders.get(mo.group(1))
        if not o:
            return kerr(400, "InputException", "Couldn't find that `order_id`.")
        return ok([korder(o)])
    mo = re.fullmatch(r"/orders/(regular|amo)/(\w+)", path)
    if mo and method in ("PUT", "DELETE"):
        o = m.orders.get(mo.group(2))
        if not o:
            return kerr(400, "InputException", "Couldn't find that `order_id`.")
        if o["orderStatus"] not in ("PENDING", "TRANSIT", "TRIGGERED"):
            return kerr(400, "OrderException", f"Order cannot be modified as it is being processed or is {kstatus(o)}.")
        if method == "DELETE":
            o["orderStatus"] = "CANCELLED"
            return ok({"order_id": o["orderId"]})
        if "order_type" in p:
            if p["order_type"] not in TYPE_IN:
                return kerr(400, "InputException", "Invalid `order_type`.")
            o["orderType"] = TYPE_IN[p["order_type"]]
        for kk, dk, f in (("quantity", "quantity", int), ("price", "price", float), ("trigger_price", "triggerPrice", float)):
            if kk in p:
                o[dk] = f(p[kk])
        if o["orderType"] in ("LIMIT", "MARKET"):
            o["triggerPrice"] = 0.0
        o["remainingQuantity"] = o["quantity"]
        m.match()
        return ok({"order_id": o["orderId"]})
    if path == "/trades":
        out = []
        for t in m.trades:
            exch, ts = TS[(t["exchangeSegment"], str(t["securityId"]))]
            out.append({"trade_id": t["exchangeTradeId"], "order_id": t["orderId"], "exchange_order_id": t["exchangeOrderId"],
                        "tradingsymbol": ts, "exchange": exch, "instrument_token": int(REV[(t["exchangeSegment"], str(t["securityId"]))]),
                        "transaction_type": t["transactionType"], "product": PROD_OUT[t["productType"]],
                        "average_price": t["tradedPrice"], "quantity": t["tradedQuantity"], "fill_timestamp": t["exchangeTime"],
                        "order_timestamp": t["createTime"], "exchange_timestamp": t["exchangeTime"]})
        return ok(out)
    if path == "/portfolio/positions" and method == "GET":
        net = []
        for x in m.positions():
            seg, sid = x["exchangeSegment"], str(x["securityId"])
            exch, ts = TS[(seg, sid)]
            i = m.inst[(seg, sid)]
            mult = (mcx.multiplier(i.get("ul") or i["sym"].split("-")[0]) or 1) if seg == "MCX_COMM" else 1
            unreal = x["unrealizedProfit"] * mult
            net.append({"tradingsymbol": ts, "exchange": exch, "instrument_token": int(REV[(seg, sid)]),
                        "product": PROD_OUT[x["productType"]], "quantity": x["netQty"], "overnight_quantity": 0,
                        "multiplier": mult, "average_price": x["costPrice"], "last_price": m.q(i), "close_price": i["prev"],
                        "pnl": round(x["realizedProfit"] + unreal, 2), "m2m": round(x["realizedProfit"] + unreal, 2),
                        "realised": x["realizedProfit"], "unrealised": round(unreal, 2),
                        "buy_quantity": x["buyQty"], "buy_price": x["buyAvg"], "buy_value": x["dayBuyValue"],
                        "sell_quantity": x["sellQty"], "sell_price": x["sellAvg"], "sell_value": x["daySellValue"],
                        "day_buy_quantity": x["dayBuyQty"], "day_sell_quantity": x["daySellQty"],
                        "day_buy_value": x["dayBuyValue"], "day_sell_value": x["daySellValue"]})
        return ok({"net": net, "day": net})
    if path == "/portfolio/positions" and method == "PUT":
        k = NAME.get(f"{p.get('exchange')}:{p.get('tradingsymbol')}")
        if not k or p.get("old_product") not in PROD_IN or p.get("new_product") not in PROD_IN:
            return kerr(400, "InputException", "Invalid position.")
        code, r = dh("POST", "/positions/convert", {"exchangeSegment": k[0], "securityId": k[1],
                                                     "fromProductType": PROD_IN[p["old_product"]],
                                                     "toProductType": PROD_IN[p["new_product"]]})
        return ok(True) if code < 300 else from_dhan_err(code, r)
    if path == "/portfolio/holdings":
        code, rows = dh("GET", "/holdings")
        out = []
        for h in rows:
            k = ("NSE_EQ", h["securityId"])
            out.append({"tradingsymbol": h["tradingSymbol"], "exchange": "NSE", "instrument_token": int(REV[k]),
                        "isin": h["isin"], "product": "CNC", "quantity": h["totalQty"], "t1_quantity": 0, "used_quantity": 0,
                        "collateral_quantity": 0, "average_price": h["avgCostPrice"], "last_price": m.q(m.inst[k])})
        return ok(out)
    if path == "/margins/orders" and method == "POST":
        out = []
        for row in raw_json or []:
            b, bad = to_dhan(dict(row, validity="DAY"))
            if bad:
                return bad
            code, r = dh("POST", "/margincalculator", b)
            if code != 200:
                return from_dhan_err(code, r)
            out.append({"type": "equity", "tradingsymbol": row["tradingsymbol"], "exchange": row["exchange"],
                        "span": r["spanMargin"], "exposure": r["exposureMargin"], "total": r["totalMargin"],
                        "leverage": 5, "charges": {"brokerage": 20.0, "total": 25.0}})
        return ok(out)
    mq = re.fullmatch(r"/quote(?:/(ltp|ohlc))?", path)
    if mq:
        names = p.get("i") or []
        names = names if isinstance(names, list) else [names]
        if len(names) > (500 if not mq.group(1) else 1000):
            return kerr(400, "InputException", "Too many instruments.")
        rows = [(n, NAME[n]) for n in names if n in NAME]
        return ok(kquote(rows, mq.group(1) or "quote"))
    mh = re.fullmatch(r"/instruments/historical/(\d+)/(\w+)", path)
    if mh:
        k = TOK.get(mh.group(1))
        if not k:
            return kerr(400, "InputException", "Invalid token.")
        i = m.inst[k]
        frm, to = str(p.get("from", ""))[:10], str(p.get("to", ""))[:10]
        if mh.group(2) == "day":
            d = mockdhan.daily_series(i, frm, to)
        else:
            iv = {"minute": 1, "3minute": 3, "5minute": 5, "10minute": 10, "15minute": 15, "30minute": 30, "60minute": 60}
            if mh.group(2) not in iv:
                return kerr(400, "InputException", "Invalid interval.")
            code, d = dh("POST", "/charts/intraday", {"securityId": k[1], "exchangeSegment": k[0], "interval": iv[mh.group(2)]})
        ois = d.get("open_interest") or [0] * len(d["timestamp"])
        candles = []
        for n, t in enumerate(d["timestamp"]):
            row = [datetime.fromtimestamp(t, IST).strftime("%Y-%m-%dT%H:%M:%S+0530"), d["open"][n], d["high"][n],
                   d["low"][n], d["close"][n], d["volume"][n]]
            if str(p.get("oi")) == "1":
                row.append(ois[n])
            candles.append(row)
        return ok({"candles": candles})
    if path == "/gtt/triggers" and method == "POST":
        try:
            cond, legs = json.loads(p.get("condition") or "{}"), json.loads(p.get("orders") or "[]")
        except ValueError:
            return kerr(400, "InputException", "Invalid condition or orders.")
        if p.get("type") != "single" or len(legs) != 1:
            return kerr(400, "InputException", "Only single GTT here.")
        b, bad = to_dhan(dict(legs[0], validity="DAY"))
        if bad:
            return bad
        b["triggerPrice"] = float((cond.get("trigger_values") or [0])[0])
        code, r = dh("POST", "/forever/orders", b)
        return ok({"trigger_id": r["orderId"][1:]}) if code == 200 else from_dhan_err(code, r)
    if path == "/gtt/triggers" and method == "GET":
        out = []
        for f in m.forever.values():
            exch, ts = TS[(f["exchangeSegment"], str(f["securityId"]))]
            out.append({"id": int(f["orderId"][1:]), "status": {"PENDING": "active", "TRADED": "triggered",
                                                                   "CANCELLED": "deleted"}.get(f["orderStatus"], "active"),
                        "condition": {"exchange": exch, "tradingsymbol": ts, "trigger_values": [f["triggerPrice"]]},
                        "orders": [{"transaction_type": f["transactionType"], "quantity": f["quantity"], "price": f["price"],
                                    "product": PROD_OUT[f["productType"]], "order_type": "LIMIT"}]})
        return ok(out)
    mg = re.fullmatch(r"/gtt/triggers/(\d+)", path)
    if mg and method == "DELETE":
        code, r = dh("DELETE", f"/forever/orders/F{mg.group(1)}")
        return ok({"trigger_id": int(mg.group(1))}) if code == 200 else from_dhan_err(code, r)
    return kerr(404, "GeneralException", f"Route not found {method} {path}")


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def reply(self, code, obj, ctype="application/json"):
        data = obj.encode() if isinstance(obj, str) else json.dumps(obj, default=str).encode()
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        self.route("GET")

    def do_POST(self):
        self.route("POST")

    def do_PUT(self):
        self.route("PUT")

    def do_DELETE(self):
        self.route("DELETE")

    def route(self, method):
        u = urlparse(self.path)
        path = u.path
        if path == "/instruments":
            return self.reply(200, instruments_csv(), "text/csv")
        n = int(self.headers.get("Content-Length") or 0)
        raw = self.rfile.read(n) if n else b""
        if path == "/mock/pin":                       # tests: hold one price still (Kite token)
            b = json.loads(raw or b"{}")
            k = TOK.get(str(b["token"]))
            with M.lock:
                i = M.inst.get(k)
                if not i:
                    return self.reply(404, {})
                i["px"], i["pinned"] = float(b["px"]), b.get("pinned", True)
                M.match()
            return self.reply(200, {"ok": True})
        if path == "/mock/opts":
            OPTS.update(json.loads(raw or b"{}"))
            return self.reply(200, {"ok": True})
        if self.headers.get("X-Kite-Version") != "3":
            return self.reply(400, {"status": "error", "error_type": "InputException", "message": "X-Kite-Version missing"})
        p = {k: (v if len(v) > 1 or k == "i" else v[0]) for k, v in parse_qs(u.query).items()}
        raw_json = None
        if raw:
            if (self.headers.get("Content-Type") or "").startswith("application/json"):
                raw_json = json.loads(raw)
            else:
                p.update({k: v[0] for k, v in parse_qs(raw.decode()).items()})
        if path != "/session/token" and self.headers.get("Authorization") != f"token {API_KEY}:{TOKEN}":
            code, out = kerr(403, "TokenException", "Incorrect `api_key` or `access_token`.")
            return self.reply(code, out)
        CALLS.append((method, path, p if raw_json is None else raw_json))
        with M.lock:
            code, out = dispatch(method, path, p, raw_json)
        self.reply(code, out)


# ---- the ticker (Kite's WebSocket), quote-mode packets -----------------------------------------------------------
FEED = {"conns": 0, "subs": []}


def packet(tok, i, ltp):
    seg = TOK[tok][0]
    t, px, prev = int(tok), int(round(ltp * 100)), int(round(i["prev"] * 100))
    if seg == "IDX_I":
        body = struct.pack(">Iiiiiii", t, px, max(px, prev), min(px, prev), prev, prev, px - prev)
    else:
        body = struct.pack(">Iiiiiiiiiii", t, px, 10, px, mockdhan.feed_volume(i), 12000, 9800, prev, max(px, prev),
                           min(px, prev), prev)
    return struct.pack(">H", len(body)) + body


def serve_feed(port):
    import socketserver
    import wsclient

    class H(socketserver.BaseRequestHandler):
        def handle(self):
            sock = self.request
            head = b""
            while b"\r\n\r\n" not in head:
                c = sock.recv(1024)
                if not c:
                    return
                head += c
            lines = head.decode("latin-1").split("\r\n")
            hdr = {k.strip().lower(): v.strip() for k, _, v in (ln.partition(":") for ln in lines[1:])}
            q = parse_qs(urlparse(lines[0].split(" ")[1]).query)
            if q.get("api_key", [""])[0] != API_KEY or q.get("access_token", [""])[0] != TOKEN:
                sock.sendall(b"HTTP/1.1 403 Forbidden\r\n\r\n")
                return
            sock.sendall(("HTTP/1.1 101 Switching Protocols\r\nUpgrade: websocket\r\nConnection: Upgrade\r\n"
                          f"Sec-WebSocket-Accept: {wsclient.accept_key(hdr.get('sec-websocket-key', ''))}\r\n\r\n").encode())
            lock = threading.Lock()

            def send(data, op=wsclient.BINARY):
                with lock:
                    sock.sendall(wsclient.frame(op, data, False))
            FEED["conns"] += 1
            subs, alive = set(), [True]

            def reader():
                try:
                    while alive[0]:
                        _fin, op, data = wsclient.read_frame(sock)
                        if op == wsclient.CLOSE:
                            break
                        if op != wsclient.TEXT:
                            continue
                        msg = json.loads(data)
                        FEED["subs"].append(msg)
                        if msg.get("a") == "subscribe":
                            subs.update(str(t) for t in msg.get("v") or [])
                        elif msg.get("a") == "unsubscribe":
                            subs.difference_update(str(t) for t in msg.get("v") or [])
                except Exception:
                    pass
                alive[0] = False
            threading.Thread(target=reader, daemon=True).start()
            try:
                send(json.dumps({"type": "message", "data": "connected"}).encode(), wsclient.TEXT)
                while alive[0]:
                    time.sleep(0.5)
                    pk = [packet(t, M.inst[TOK[t]], M.q(M.inst[TOK[t]])) for t in list(subs) if t in TOK]
                    send(struct.pack(">H", len(pk)) + b"".join(pk) if pk else b"\x00")
            except OSError:
                pass
            alive[0] = False
            FEED["conns"] -= 1

    class S(socketserver.ThreadingMixIn, socketserver.TCPServer):
        daemon_threads = True
        allow_reuse_address = True
    srv = S(("127.0.0.1", port), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv


def serve(port=3095, tick_every=1.0):
    global M
    M = mockdhan.Market()
    build()
    srv = ThreadingHTTPServer(("127.0.0.1", port), Handler)

    def ticker():
        while True:
            time.sleep(tick_every)
            M.tick()
    threading.Thread(target=ticker, daemon=True).start()
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv


if __name__ == "__main__":
    serve(int(sys.argv[1]) if len(sys.argv) > 1 else 3095)
    serve_feed(int(sys.argv[2]) if len(sys.argv) > 2 else 3094)
    print("Pretend Zerodha running; Ctrl+C to stop.")
    while True:
        time.sleep(3600)
