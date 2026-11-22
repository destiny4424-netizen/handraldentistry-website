"""A pretend Dhan (v2 REST) for demo mode and tests. Prices move at random; orders fill like on
an exchange (market at once, limit when the price gets there, stop-loss when triggered).
Nothing here touches a real account.

    python mockdhan.py 3099      serves it on 127.0.0.1:3099
"""
import csv
import io
import json
import math
import random
import re
import sys
import threading
import time
import zlib
from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

IST = timezone(timedelta(hours=5, minutes=30))
CLIENT = "1000000001"
TOKEN = "demo-token"

STOCKS = [("RELIANCE", "2885", 1290, 500), ("HDFCBANK", "1333", 1720, 550), ("ICICIBANK", "4963", 1310, 700),
          ("INFY", "1594", 1580, 400), ("TCS", "11536", 3450, 175), ("SBIN", "3045", 870, 750),
          ("TATAMOTORS", "3456", 720, 800), ("AXISBANK", "5900", 1150, 625), ("LT", "11483", 3620, 150),
          ("ITC", "1660", 415, 1600), ("BAJFINANCE", "317", 910, 750), ("MARUTI", "10999", 12600, 50),
          ("ADANIENT", "25", 2480, 300), ("HINDUNILVR", "1394", 2420, 300), ("SUNPHARMA", "3351", 1760, 350),
          ("TATASTEEL", "3499", 158, 5500), ("WIPRO", "3787", 248, 3000), ("ZOMATO", "5097", 285, 2250),
          ("BHARTIARTL", "10604", 1980, 475), ("KOTAKBANK", "1922", 2090, 400)]
INDICES = [("NIFTY", "13", 25900, 75, 50, "NSE"), ("BANKNIFTY", "25", 58300, 35, 100, "NSE"),
           ("FINNIFTY", "27", 27400, 65, 50, "NSE"), ("SENSEX", "51", 84800, 20, 100, "BSE"),
           ("INDIAVIX", "21", 12.4, 0, 0, "NSE")]
MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]


def _expiries(today):
    """Next two Tuesdays (NSE weekly) and the last Tuesday of this and next month."""
    d = today
    weekly = []
    while len(weekly) < 3:
        if d.weekday() == 1 and d >= today:
            weekly.append(d)
        d += timedelta(days=1)
    monthly = []
    for m in range(2):
        y, mo = today.year + (today.month - 1 + m) // 12, (today.month - 1 + m) % 12 + 1
        last = datetime(y + (mo // 12), mo % 12 + 1, 1).date() - timedelta(days=1)
        while last.weekday() != 1:
            last -= timedelta(days=1)
        if last < today:
            continue
        monthly.append(last)
    if len(monthly) < 2:
        y, mo = today.year + (today.month + 1) // 12, (today.month + 1) % 12 + 1
        last = datetime(y + (mo // 12), mo % 12 + 1, 1).date() - timedelta(days=1)
        while last.weekday() != 1:
            last -= timedelta(days=1)
        monthly.append(last)
    return weekly, monthly


class Market:
    def __init__(self, seed=7):
        self.rng = random.Random(seed)
        self.lock = threading.RLock()
        self.inst = {}                                 # (seg, id) -> dict
        self.next_id = 50000
        self.orders = {}
        self.trades = []
        self.history = []
        self.pos = {}                                  # (seg, id, product) -> dict
        self.funds = 500000.0
        self.used = 0.0
        self.kill = False
        self.calls = []                                # (method, path, body) for tests
        self.forever = {}                              # Forever (GTT) orders
        self.order_seq = 1000
        self.build()
        self.seed_history()

    # ---- instruments --------------------------------------------------------------------
    def add(self, **kw):
        kw.setdefault("tick", 0.05)
        self.inst[(kw["seg"], kw["id"])] = kw
        return kw

    def build(self):
        today = datetime.now(IST).date()
        weekly, monthly = _expiries(today)
        for name, sid, px, lot, step, exch in INDICES:
            self.add(seg="IDX_I", id=sid, kind="INDEX", sym=name, exch=exch, px=px, prev=px * 0.996, lot=1,
                     name=name, series="", expiry="", strike=0, opt="")
            if not lot:
                continue
            fseg = "BSE_FNO" if exch == "BSE" else "NSE_FNO"
            exps = weekly[:2] + [m for m in monthly if m not in weekly[:2]] if name in ("NIFTY", "SENSEX") else monthly
            for e in monthly[:2]:
                self._fut(name, fseg, exch, "FUTIDX", e, px, lot)
            for e in exps[:3]:
                self._opts(name, fseg, exch, "OPTIDX", e, px, lot, step, 14)
        for name, sid, px, lot in STOCKS:
            self.add(seg="NSE_EQ", id=sid, kind="EQUITY", sym=name, exch="NSE", px=px, prev=px * (1 + self.rng.uniform(-.02, .02)),
                     lot=1, name=name.title() + " Ltd", series="EQ", expiry="", strike=0, opt="")
            self.add(seg="BSE_EQ", id=str(500000 + int(sid)), kind="EQUITY", sym=name, exch="BSE", px=px, prev=px, lot=1,
                     name=name.title() + " Ltd", series="A", expiry="", strike=0, opt="")
            self._fut(name, "NSE_FNO", "NSE", "FUTSTK", monthly[0], px, lot)
            step = 10 if px < 500 else 20 if px < 1500 else 50 if px < 5000 else 100
            self._opts(name, "NSE_FNO", "NSE", "OPTSTK", monthly[0], px, lot, step, 8)
        self.add(seg="MCX_COMM", id="440000", kind="FUTCOM", sym=f"GOLD-{monthly[0].strftime('%b%Y')}-FUT", exch="MCX",
                 px=126500, prev=126000, lot=1, name="GOLD " + monthly[0].strftime("%d %b").upper() + " FUT", series="",
                 expiry=monthly[0].isoformat(), strike=0, opt="", tick=1)

    def _fut(self, ul, seg, exch, kind, e, px, lot):
        sid = str(self.next_id); self.next_id += 1
        self.add(seg=seg, id=sid, kind=kind, sym=f"{ul}-{e.strftime('%b%Y')}-FUT", exch=exch, px=px * 1.003, prev=px,
                 lot=lot, name=f"{ul} {e.strftime('%d %b').upper()} FUT", series="", expiry=e.isoformat(), strike=0, opt="",
                 ul=ul)

    def _opts(self, ul, seg, exch, kind, e, px, lot, step, n):
        atm = round(px / step) * step
        days = max((e - datetime.now(IST).date()).days, 0) + 0.5
        for k in range(-n, n + 1):
            strike = atm + k * step
            for opt in ("CE", "PE"):
                sid = str(self.next_id); self.next_id += 1
                p = _bs(px, strike, days / 365, 0.13 if kind == "OPTIDX" else 0.25, opt)
                self.add(seg=seg, id=sid, kind=kind, sym=f"{ul}-{e.strftime('%b%Y')}-{int(strike)}-{opt}", exch=exch,
                         px=max(p, 0.05), prev=max(p * 1.05, 0.05), lot=lot,
                         name=f"{ul} {e.strftime('%d %b').upper()} {int(strike)} {'CALL' if opt == 'CE' else 'PUT'}",
                         series="", expiry=e.isoformat(), strike=float(strike), opt=opt, ul=ul, under_px=px)

    def scrip_csv(self):
        out = io.StringIO()
        w = csv.writer(out)
        w.writerow(["SEM_EXM_EXCH_ID", "SEM_SEGMENT", "SEM_SMST_SECURITY_ID", "SEM_INSTRUMENT_NAME", "SEM_EXPIRY_CODE",
                    "SEM_TRADING_SYMBOL", "SEM_LOT_UNITS", "SEM_CUSTOM_SYMBOL", "SEM_EXPIRY_DATE", "SEM_STRIKE_PRICE",
                    "SEM_OPTION_TYPE", "SEM_TICK_SIZE", "SEM_EXPIRY_FLAG", "SEM_EXCH_INSTRUMENT_TYPE", "SEM_SERIES",
                    "SM_SYMBOL_NAME"])
        segc = {"NSE_EQ": "E", "BSE_EQ": "E", "NSE_FNO": "D", "BSE_FNO": "D", "IDX_I": "I", "MCX_COMM": "M"}
        for i in self.inst.values():
            w.writerow([i["exch"], segc[i["seg"]], i["id"], i["kind"], 0, i["sym"], f"{i['lot']}.0",
                        i["name"], (i["expiry"] + " 15:30:00") if i["expiry"] else "", f"{i['strike']:.5f}" if i["opt"] else "-0.01",
                        i["opt"] or "XX", f"{i['tick'] * 100:.4f}", "W" if i["opt"] else "NA", "", i["series"],
                        i["name"].upper()])
        return out.getvalue()

    # ---- prices -------------------------------------------------------------------------
    def tick(self):
        with self.lock:
            under = {}
            for i in self.inst.values():
                if i.get("pinned"):
                    under[i["sym"]] = i
                    continue
                if i["kind"] in ("INDEX", "EQUITY", "FUTCOM"):
                    vol = 0.0006 if i["kind"] == "INDEX" else 0.0012
                    i["px"] = max(0.05, i["px"] * (1 + self.rng.gauss(0, vol)))
                    under[i["sym"]] = i
            for i in self.inst.values():
                ul = under.get(i.get("ul", ""))
                if not ul:
                    continue
                if i["kind"].startswith("FUT"):
                    i["px"] = ul["px"] * 1.003
                elif i["opt"]:
                    e = datetime.fromisoformat(i["expiry"]).date()
                    days = max((e - datetime.now(IST).date()).days, 0) + 0.5
                    i["px"] = max(0.05, _bs(ul["px"], i["strike"], days / 365, 0.13 if i["kind"] == "OPTIDX" else 0.25, i["opt"]))
            self.match()
            self.check_forever()

    def check_forever(self):
        for f in self.forever.values():
            if f["orderStatus"] != "PENDING":
                continue
            i = self.inst[(f["exchangeSegment"], f["securityId"])]
            px = self.q(i)
            hit = px <= f["triggerPrice"] if f["transactionType"] == "SELL" and f["triggerPrice"] < f["_ref"] else \
                px >= f["triggerPrice"]
            if hit:
                f["orderStatus"] = "TRIGGERED"
                self.new_order({"transactionType": f["transactionType"], "exchangeSegment": f["exchangeSegment"],
                                "productType": f["productType"], "orderType": "LIMIT", "securityId": f["securityId"],
                                "quantity": f["quantity"], "price": f["price"], "validity": "DAY"})

    def q(self, i):
        px = round(i["px"] / i["tick"]) * i["tick"]
        return round(px, 2)

    # ---- orders -------------------------------------------------------------------------
    def new_order(self, b, kind="NORMAL"):
        key = (b["exchangeSegment"], str(b["securityId"]))
        i = self.inst.get(key)
        if not i:
            return 400, err("DH-905", "Input_Exception", "Invalid SecurityId")
        if self.kill:
            return 400, err("DH-906", "Order_Error", "Kill switch is active for the day")
        if i["lot"] > 1 and int(b["quantity"]) % i["lot"]:
            return 400, err("DH-906", "Order_Error", f"Quantity should be multiple of lot size {i['lot']}")
        self.order_seq += 1
        oid = f"5225{datetime.now(IST).strftime('%m%d')}{self.order_seq:06d}"
        now = datetime.now(IST).strftime("%Y-%m-%d %H:%M:%S")
        o = {"dhanClientId": CLIENT, "orderId": oid, "correlationId": b.get("correlationId", ""), "orderStatus": "PENDING",
             "transactionType": b["transactionType"], "exchangeSegment": b["exchangeSegment"],
             "productType": b["productType"], "orderType": b["orderType"], "validity": b.get("validity", "DAY"),
             "tradingSymbol": i["sym"], "securityId": i["id"], "quantity": int(b["quantity"]),
             "disclosedQuantity": int(b.get("disclosedQuantity") or 0), "price": float(b.get("price") or 0),
             "triggerPrice": float(b.get("triggerPrice") or 0), "afterMarketOrder": bool(b.get("afterMarketOrder")),
             "createTime": now, "updateTime": now, "exchangeTime": now, "drvExpiryDate": i["expiry"] or "0001-01-01 00:00:00",
             "drvOptionType": {"CE": "CALL", "PE": "PUT"}.get(i["opt"], "NA"), "drvStrikePrice": i["strike"],
             "omsErrorCode": "", "omsErrorDescription": "", "filledQty": 0, "remainingQuantity": int(b["quantity"]),
             "averageTradedPrice": 0, "algoId": "", "legName": "", "_kind": kind, "_triggered": False}
        if kind == "SUPER":
            o.update(legName="ENTRY_LEG", targetPrice=float(b.get("targetPrice") or 0),
                     stopLossPrice=float(b.get("stopLossPrice") or 0), trailingJump=float(b.get("trailingJump") or 0))
        value = int(b["quantity"]) * (o["price"] or self.q(i))
        margin = value * (0.2 if b["exchangeSegment"].endswith("FNO") and not i["opt"] else
                          0.2 if b["productType"] == "INTRADAY" else 1.0)
        if b["transactionType"] == "SELL" and i["opt"] and not self._closing(o):
            margin = value * 12
        if b["transactionType"] == "BUY" or not self._closing(o):
            if margin > self.funds - self.used:
                o.update(orderStatus="REJECTED", omsErrorDescription="RMS:Margin Exceeds,Required: %.2f, Available: %.2f"
                         % (margin, self.funds - self.used))
        if o["orderStatus"] == "PENDING":
            o["orderStatus"] = "TRANSIT"
        self.orders[oid] = o
        self.match()
        return 200, {"orderId": oid, "orderStatus": o["orderStatus"]}

    def _closing(self, o):
        p = self.pos.get((o["exchangeSegment"], o["securityId"], o["productType"]))
        if not p:
            return False
        return (p["net"] > 0 and o["transactionType"] == "SELL") or (p["net"] < 0 and o["transactionType"] == "BUY")

    def match(self):
        with self.lock:
            for o in list(self.orders.values()):
                if o["orderStatus"] == "TRANSIT":
                    o["orderStatus"] = "PENDING"
                if o["orderStatus"] not in ("PENDING", "TRIGGERED") or o.get("_parked"):
                    continue
                i = self.inst[(o["exchangeSegment"], o["securityId"])]
                px = self.q(i)
                buy = o["transactionType"] == "BUY"
                if o["orderType"] in ("STOP_LOSS", "STOP_LOSS_MARKET") and not o["_triggered"]:
                    hit = px >= o["triggerPrice"] if buy else px <= o["triggerPrice"]
                    if not hit:
                        continue
                    o["_triggered"] = True
                    o["orderStatus"] = "TRIGGERED"
                fill = None
                if o["orderType"] in ("MARKET", "STOP_LOSS_MARKET"):
                    fill = px
                elif (buy and px <= o["price"]) or (not buy and px >= o["price"]):
                    fill = o["price"] if o["orderType"] == "LIMIT" else px
                if fill is None:
                    continue
                self.fill(o, fill)

    def fill(self, o, price):
        now = datetime.now(IST).strftime("%Y-%m-%d %H:%M:%S")
        o.update(orderStatus="TRADED", filledQty=o["quantity"], remainingQuantity=0, averageTradedPrice=price,
                 updateTime=now, exchangeTime=now)
        i = self.inst[(o["exchangeSegment"], o["securityId"])]
        self.trades.append({"dhanClientId": CLIENT, "orderId": o["orderId"], "exchangeOrderId": "1" + o["orderId"],
                            "exchangeTradeId": str(len(self.trades) + 900001), "transactionType": o["transactionType"],
                            "exchangeSegment": o["exchangeSegment"], "productType": o["productType"],
                            "orderType": o["orderType"], "tradingSymbol": i["sym"], "customSymbol": i["name"],
                            "securityId": i["id"], "tradedQuantity": o["quantity"], "tradedPrice": price,
                            "createTime": now, "updateTime": now, "exchangeTime": now,
                            "drvExpiryDate": i["expiry"] or "NA", "drvOptionType": {"CE": "CALL", "PE": "PUT"}.get(i["opt"], "NA"),
                            "drvStrikePrice": i["strike"]})
        k = (o["exchangeSegment"], o["securityId"], o["productType"])
        p = self.pos.setdefault(k, {"net": 0, "buyQty": 0, "sellQty": 0, "buyVal": 0.0, "sellVal": 0.0, "dayBuy": 0, "daySell": 0})
        q = o["quantity"]
        if o["transactionType"] == "BUY":
            p["net"] += q; p["buyQty"] += q; p["buyVal"] += q * price
        else:
            p["net"] -= q; p["sellQty"] += q; p["sellVal"] += q * price
        if o.get("_kind") == "SUPER" and o.get("legName") == "ENTRY_LEG":
            side = "SELL" if o["transactionType"] == "BUY" else "BUY"
            for leg, typ, pr in (("TARGET_LEG", "LIMIT", o.get("targetPrice")), ("STOP_LOSS_LEG", "STOP_LOSS_MARKET", o.get("stopLossPrice"))):
                if pr:
                    self.order_seq += 1
                    lo = dict(o, orderId=o["orderId"] + leg[0], legName=leg, transactionType=side, orderType=typ,
                              price=pr if typ == "LIMIT" else 0, triggerPrice=pr if typ != "LIMIT" else 0,
                              orderStatus="PENDING", filledQty=0, remainingQuantity=o["quantity"], averageTradedPrice=0,
                              _triggered=False, _parent=o["orderId"], _kind="LEG")
                    self.orders[lo["orderId"]] = lo
        if o.get("_kind") == "LEG":                     # one leg done: cancel the other
            for x in self.orders.values():
                if x.get("_parent") == o["_parent"] and x is not o and x["orderStatus"] == "PENDING":
                    x["orderStatus"] = "CANCELLED"

    def positions(self):
        out = []
        for (seg, sid, prod), p in self.pos.items():
            i = self.inst[(seg, sid)]
            ltp = self.q(i)
            buy_avg = p["buyVal"] / p["buyQty"] if p["buyQty"] else 0
            sell_avg = p["sellVal"] / p["sellQty"] if p["sellQty"] else 0
            closed = min(p["buyQty"], p["sellQty"])
            realized = closed * (sell_avg - buy_avg)
            net = p["net"]
            unreal = net * (ltp - (buy_avg if net > 0 else sell_avg)) if net else 0
            out.append({"dhanClientId": CLIENT, "tradingSymbol": i["sym"], "securityId": sid,
                        "positionType": "LONG" if net > 0 else "SHORT" if net < 0 else "CLOSED",
                        "exchangeSegment": seg, "productType": prod, "buyAvg": round(buy_avg, 2), "buyQty": p["buyQty"],
                        "costPrice": round(buy_avg if net >= 0 else sell_avg, 2), "sellAvg": round(sell_avg, 2),
                        "sellQty": p["sellQty"], "netQty": net, "realizedProfit": round(realized, 2),
                        "unrealizedProfit": round(unreal, 2), "rbiReferenceRate": 1.0, "multiplier": 1,
                        "carryForwardBuyQty": 0, "carryForwardSellQty": 0, "carryForwardBuyValue": 0,
                        "carryForwardSellValue": 0, "dayBuyQty": p["buyQty"], "daySellQty": p["sellQty"],
                        "dayBuyValue": round(p["buyVal"], 2), "daySellValue": round(p["sellVal"], 2),
                        "drvExpiryDate": i["expiry"] or "0001-01-01", "drvOptionType": {"CE": "CALL", "PE": "PUT"}.get(i["opt"]),
                        "drvStrikePrice": i["strike"], "crossCurrency": False})
        return out

    def seed_history(self):
        """About 50 sessions of made-up past trades, with some bad habits in them."""
        rng = random.Random(11)
        today = datetime.now(IST).date()
        d = today - timedelta(days=75)
        n = 0
        opts = [i for i in self.inst.values() if i["kind"] == "OPTIDX" and i.get("ul") == "NIFTY"]
        stocks = [i for i in self.inst.values() if i["kind"] == "EQUITY" and i["exch"] == "NSE"]
        while d < today:
            if d.weekday() < 5:
                k = rng.choice([1, 2, 2, 3, 3, 4, 6, 8])
                t = datetime(d.year, d.month, d.day, 9, 16, tzinfo=IST) + timedelta(minutes=rng.randint(0, 40))
                last_loss = False
                for _ in range(k):
                    if rng.random() < 0.55:
                        i = rng.choice(opts); lot = i["lot"] * rng.choice([1, 1, 2]); prod = "INTRADAY"
                        entry = round(rng.uniform(60, 220), 1)
                        edge = -0.06 if t.hour == 9 and t.minute < 30 else 0.02
                        if last_loss:
                            lot *= 2; edge -= 0.08
                        move = rng.gauss(edge, 0.18)
                        hold = rng.randint(3, 25) if move > 0 else rng.randint(10, 80)
                        side = "BUY"
                    else:
                        i = rng.choice(stocks); prod = rng.choice(["INTRADAY", "INTRADAY", "CNC"])
                        entry = round(i["px"] * rng.uniform(0.95, 1.05), 1)
                        lot = max(1, int(100000 / entry))
                        move = rng.gauss(0.002, 0.012)
                        hold = rng.randint(15, 200)
                        side = rng.choice(["BUY", "BUY", "SELL"]) if prod == "INTRADAY" else "BUY"
                    exitp = round(max(0.05, entry * (1 + (move if side == "BUY" else -move))), 1)
                    t2 = t + timedelta(minutes=hold)
                    if t2.hour >= 15 and t2.minute > 25:
                        t2 = t2.replace(hour=15, minute=20)
                    for when, s, px in ((t, side, entry), (t2, "SELL" if side == "BUY" else "BUY", exitp)):
                        n += 1
                        val = lot * px
                        self.history.append({
                            "dhanClientId": CLIENT, "orderId": f"H{n:08d}", "exchangeOrderId": f"X{n}", "exchangeTradeId": f"T{n}",
                            "transactionType": s, "exchangeSegment": i["seg"], "productType": prod, "orderType": "MARKET",
                            "tradingSymbol": i["sym"], "customSymbol": i["name"], "securityId": i["id"],
                            "tradedQuantity": lot, "tradedPrice": px, "isin": "NA", "instrument": i["kind"],
                            "sebiTax": round(val * 1e-6, 4), "stt": round(val * (0.001 if s == "SELL" and i["opt"] else 0.00025 if s == "SELL" else 0), 4),
                            "brokerageCharges": 20.0, "serviceTax": round(0.18 * (20 + val * 0.0003), 4),
                            "exchangeTransactionCharges": round(val * (0.0003503 if i["opt"] else 0.0000297), 4),
                            "stampDuty": round(val * 0.00003 if s == "BUY" else 0, 4),
                            "createTime": when.strftime("%Y-%m-%d %H:%M:%S"), "updateTime": when.strftime("%Y-%m-%d %H:%M:%S"),
                            "exchangeTime": when.strftime("%Y-%m-%d %H:%M:%S"),
                            "drvExpiryDate": i["expiry"] or "NA", "drvOptionType": {"CE": "CALL", "PE": "PUT"}.get(i["opt"], "NA"),
                            "drvStrikePrice": i["strike"]})
                    last_loss = (exitp - entry) * (1 if side == "BUY" else -1) < 0
                    t = t2 + timedelta(minutes=rng.randint(1 if last_loss else 10, 40))
                    if t.hour >= 15:
                        break
            d += timedelta(days=1)


def _bs(s, k, t, v, kind):
    t = max(t, 1e-4)
    d1 = (math.log(s / k) + 0.5 * v * v * t) / (v * math.sqrt(t))
    d2 = d1 - v * math.sqrt(t)
    n = lambda x: 0.5 * (1 + math.erf(x / math.sqrt(2)))
    return s * n(d1) - k * n(d2) if kind == "CE" else k * n(-d2) - s * n(-d1)


def err(code, typ, msg):
    return {"errorType": typ, "errorCode": code, "errorMessage": msg}


M = None


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def reply(self, code, obj, ctype="application/json"):
        data = obj.encode() if isinstance(obj, str) else json.dumps(obj).encode()
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
        if path.endswith("scrip-master.csv"):
            return self.reply(200, M.scrip_csv(), "text/csv")
        if path == "/mock/pin":                       # tests: hold one price still
            n = int(self.headers.get("Content-Length") or 0)
            b = json.loads(self.rfile.read(n) or b"{}")
            with M.lock:
                i = M.inst.get((b["seg"], str(b["id"])))
                if not i:
                    return self.reply(404, {})
                i["px"], i["pinned"] = float(b["px"]), b.get("pinned", True)
                M.match()
            return self.reply(200, {"ok": True})
        if not path.startswith("/v2/"):
            return self.reply(404, {})
        path = path[3:]
        if self.headers.get("access-token") != TOKEN:
            return self.reply(401, err("DH-901", "Invalid_Authentication", "Client ID or user generated access token is invalid or expired."))
        body = {}
        if method in ("POST", "PUT"):
            n = int(self.headers.get("Content-Length") or 0)
            body = json.loads(self.rfile.read(n) or b"{}")
            if body.get("dhanClientId") != CLIENT:
                return self.reply(400, err("DH-905", "Input_Exception", "dhanClientId missing"))
        M.calls.append((method, path, body))
        with M.lock:
            code, out = self.dispatch(method, path, body, parse_qs(u.query))
        self.reply(code, out)

    def dispatch(self, method, path, b, qs):
        m = M
        if path == "/orders" and method == "POST" or path == "/orders/slicing" and method == "POST":
            bad = check_order(b)
            if bad:
                return 400, err("DH-905", "Input_Exception", bad)
            code, r = m.new_order(b)
            return (code, [r] if path.endswith("slicing") and code == 200 else r)
        if path == "/orders" and method == "GET":
            return 200, [pub(o) for o in m.orders.values() if o.get("_kind") != "SUPER" and o.get("_kind") != "LEG"]
        mo = re.fullmatch(r"/orders/(\w+)", path)
        if mo:
            o = m.orders.get(mo.group(1))
            if not o:
                return 400, err("DH-905", "Input_Exception", "Invalid order id")
            if method == "GET":
                return 200, pub(o)
            if o["orderStatus"] not in ("PENDING", "TRANSIT", "TRIGGERED"):
                return 400, err("DH-906", "Order_Error", f"Order is {o['orderStatus']}, can't change it")
            if method == "DELETE":
                o["orderStatus"] = "CANCELLED"
                return 200, {"orderId": o["orderId"], "orderStatus": "CANCELLED"}
            if method == "PUT":
                for k in ("orderType", "quantity", "price", "triggerPrice", "validity", "disclosedQuantity"):
                    if k in b:
                        o[k] = b[k]
                o["remainingQuantity"] = o["quantity"]
                m.match()
                return 200, {"orderId": o["orderId"], "orderStatus": o["orderStatus"]}
        if path == "/super/orders" and method == "POST":
            bad = check_order(dict(b, validity="DAY"), super_=True)
            if bad:
                return 400, err("DH-905", "Input_Exception", bad)
            return m.new_order(b, "SUPER")
        if path == "/super/orders" and method == "GET":
            out = []
            for o in m.orders.values():
                if o.get("_kind") == "SUPER":
                    legs = [{"orderId": x["orderId"], "legName": x["legName"], "transactionType": x["transactionType"],
                             "remainingQuantity": x["remainingQuantity"], "triggeredQuantity": 0,
                             "price": x["price"] or x["triggerPrice"], "orderStatus": x["orderStatus"], "trailingJump": 0}
                            for x in m.orders.values() if x.get("_parent") == o["orderId"]]
                    out.append(dict(pub(o), legDetails=legs))
            return 200, out
        ms = re.fullmatch(r"/super/orders/(\w+)(?:/(\w+))?", path)
        if ms:
            o = m.orders.get(ms.group(1))
            if not o:
                return 400, err("DH-905", "Input_Exception", "Invalid order id")
            if method == "DELETE":
                leg = ms.group(2) or "ENTRY_LEG"
                for x in m.orders.values():
                    if (x is o and leg == "ENTRY_LEG") or (x.get("_parent") == o["orderId"] and (leg == "ENTRY_LEG" or x["legName"] == leg)):
                        if x["orderStatus"] in ("PENDING", "TRANSIT", "TRIGGERED"):
                            x["orderStatus"] = "CANCELLED"
                return 200, {"orderId": o["orderId"], "orderStatus": "CANCELLED"}
            if method == "PUT":
                if b.get("legName") not in ("ENTRY_LEG", "TARGET_LEG", "STOP_LOSS_LEG"):
                    return 400, err("DH-905", "Input_Exception", "legName")
                for x in m.orders.values():
                    if x.get("_parent") == o["orderId"] and x["legName"] == b["legName"]:
                        if "targetPrice" in b:
                            x["price"] = b["targetPrice"]
                        if "stopLossPrice" in b:
                            x["triggerPrice"] = b["stopLossPrice"]
                return 200, {"orderId": o["orderId"], "orderStatus": o["orderStatus"]}
        if path == "/forever/orders" and method == "POST":
            i = m.inst.get((b["exchangeSegment"], str(b["securityId"])))
            if not i:
                return 400, err("DH-905", "Input_Exception", "Invalid SecurityId")
            if b["productType"] not in ("CNC", "MTF"):
                return 400, err("DH-906", "Order_Error", "Forever orders are for CNC and MTF")
            m.order_seq += 1
            oid = f"F{m.order_seq}"
            m.forever[oid] = dict(b, orderId=oid, orderStatus="PENDING", _ref=m.q(i), tradingSymbol=i["sym"])
            return 200, {"orderId": oid, "orderStatus": "PENDING"}
        if path == "/forever/orders" and method == "GET":
            return 200, [pub(f) for f in m.forever.values()]
        mfo = re.fullmatch(r"/forever/orders/(\w+)", path)
        if mfo and method == "DELETE":
            f = m.forever.get(mfo.group(1))
            if not f or f["orderStatus"] != "PENDING":
                return 400, err("DH-906", "Order_Error", "Order not pending")
            f["orderStatus"] = "CANCELLED"
            return 200, {"orderId": f["orderId"], "orderStatus": "CANCELLED"}
        if path == "/trades" and method == "GET":
            return 200, m.trades
        mt = re.fullmatch(r"/trades/(\d{4}-\d\d-\d\d)/(\d{4}-\d\d-\d\d)/(\d+)", path)
        if mt:
            rows = [h for h in m.history if mt.group(1) <= h["exchangeTime"][:10] <= mt.group(2)]
            p = int(mt.group(3))
            return 200, rows[p * 50:(p + 1) * 50]
        if path == "/positions" and method == "GET":
            return 200, m.positions()
        if path == "/positions" and method == "DELETE":
            for (seg, sid, prod), p in list(m.pos.items()):
                if p["net"]:
                    m.new_order({"transactionType": "SELL" if p["net"] > 0 else "BUY", "exchangeSegment": seg,
                                 "productType": prod, "orderType": "MARKET", "securityId": sid, "quantity": abs(p["net"])})
            for o in m.orders.values():
                if o["orderStatus"] == "PENDING":
                    o["orderStatus"] = "CANCELLED"
            return 200, {"status": "SUCCESS", "message": "All positions exited"}
        if path == "/positions/convert":
            k = (b["exchangeSegment"], b["securityId"], b["fromProductType"])
            if k not in m.pos:
                return 400, err("DH-905", "Input_Exception", "No position")
            m.pos[(b["exchangeSegment"], b["securityId"], b["toProductType"])] = m.pos.pop(k)
            return 202, {}
        if path == "/holdings":
            return 200, [{"exchange": "ALL", "tradingSymbol": "ITC", "securityId": "1660", "isin": "INE154A01025",
                          "totalQty": 200, "dpQty": 200, "t1Qty": 0, "availableQty": 200, "collateralQty": 0,
                          "avgCostPrice": 398.4},
                         {"exchange": "ALL", "tradingSymbol": "HDFCBANK", "securityId": "1333", "isin": "INE040A01034",
                          "totalQty": 40, "dpQty": 40, "t1Qty": 0, "availableQty": 40, "collateralQty": 0,
                          "avgCostPrice": 1602.0}]
        if path == "/fundlimit":
            return 200, {"dhanClientId": CLIENT, "availabelBalance": round(m.funds - m.used, 2), "sodLimit": m.funds,
                         "collateralAmount": 0.0, "receiveableAmount": 0.0, "utilizedAmount": round(m.used, 2),
                         "blockedPayoutAmount": 0.0, "withdrawableBalance": round(m.funds - m.used, 2)}
        if path == "/margincalculator":
            i = m.inst.get((b["exchangeSegment"], str(b["securityId"])))
            if not i:
                return 400, err("DH-905", "Input_Exception", "Invalid SecurityId")
            val = int(b["quantity"]) * (float(b.get("price") or 0) or m.q(i))
            req = val * (12 if i["opt"] and b["transactionType"] == "SELL" else 1 if i["opt"] else 0.2
                         if b["exchangeSegment"].endswith("FNO") or b["productType"] == "INTRADAY" else 1)
            avail = m.funds - m.used
            return 200, {"totalMargin": round(req, 2), "spanMargin": round(req * 0.7, 2), "exposureMargin": round(req * 0.3, 2),
                         "availableBalance": round(avail, 2), "variableMargin": 0.0,
                         "insufficientBalance": round(max(0, req - avail), 2), "brokerage": 20.0, "leverage": "5.00"}
        if path == "/killswitch":
            if method == "POST":
                m.kill = (qs.get("killSwitchStatus") or [""])[0] == "ACTIVATE"
                return 200, {"dhanClientId": CLIENT, "killSwitchStatus": "Kill Switch has been successfully " + ("activated" if m.kill else "deactivated")}
            return 200, {"dhanClientId": CLIENT, "killSwitchStatus": "ACTIVATE" if m.kill else "DEACTIVATE"}
        if path == "/RenewToken":
            return 200, {"accessToken": TOKEN}
        mf = re.fullmatch(r"/marketfeed/(ltp|ohlc|quote)", path)
        if mf:
            out = {}
            for seg, ids in b.items():
                if seg == "dhanClientId":
                    continue
                for sid in ids:
                    i = m.inst.get((seg, str(sid)))
                    if not i:
                        continue
                    px = m.q(i)
                    row = {"last_price": px}
                    if mf.group(1) in ("ohlc", "quote"):
                        row["ohlc"] = {"open": round(i["prev"] * 1.001, 2), "close": round(i["prev"], 2),
                                       "high": round(max(px, i["prev"]) * 1.004, 2), "low": round(min(px, i["prev"]) * 0.996, 2)}
                    if mf.group(1) == "quote":
                        t = i["tick"]
                        row.update(net_change=round(px - i["prev"], 2), volume=random.randint(10000, 900000),
                                   average_price=px, oi=random.randint(0, 500000) if i["kind"] not in ("EQUITY", "INDEX") else 0,
                                   buy_quantity=12000, sell_quantity=9800, last_trade_time=datetime.now(IST).strftime("%d/%m/%Y %H:%M:%S"),
                                   upper_circuit_limit=round(max(i["prev"], px) * (20 if i["opt"] else 1.2), 2),
                                   lower_circuit_limit=round(min(i["prev"], px) * (0.05 if i["opt"] else 0.8), 2),
                                   depth={"buy": [{"quantity": 100 * (k + 1), "orders": k + 1, "price": round(px - t * (k + 1), 2)} for k in range(5)],
                                          "sell": [{"quantity": 90 * (k + 1), "orders": k + 1, "price": round(px + t * (k + 1), 2)} for k in range(5)]})
                    out.setdefault(seg, {})[str(sid)] = row
            return 200, {"data": out, "status": "success"}
        if path == "/optionchain/expirylist":
            ul = next((i for i in m.inst.values() if i["id"] == str(b["UnderlyingScrip"]) and i["seg"] == b["UnderlyingSeg"]), None)
            if not ul:
                return 400, err("DH-905", "Input_Exception", "Invalid underlying")
            ex = sorted({i["expiry"] for i in m.inst.values() if i.get("ul") == ul["sym"] and i["opt"]})
            return 200, {"data": ex, "status": "success"}
        if path == "/optionchain":
            ul = next((i for i in m.inst.values() if i["id"] == str(b["UnderlyingScrip"]) and i["seg"] == b["UnderlyingSeg"]), None)
            if not ul:
                return 400, err("DH-905", "Input_Exception", "Invalid underlying")
            oc = {}
            for i in m.inst.values():
                if i.get("ul") == ul["sym"] and i["opt"] and i["expiry"] == b["Expiry"]:
                    px = m.q(i)
                    mny = (ul["px"] - i["strike"]) / ul["px"]
                    oi = int(2e6 * math.exp(-(mny * 30) ** 2)) + 1000
                    oc.setdefault(f"{i['strike']:.6f}", {})[i["opt"].lower()] = {
                        "greeks": {"delta": round(0.5 + mny * 10 if i["opt"] == "CE" else -0.5 + mny * 10, 4),
                                   "theta": -round(px * 0.05, 4), "gamma": 0.001, "vega": round(px * 0.1, 4)},
                        "implied_volatility": 13.2, "last_price": px, "oi": oi, "previous_close_price": round(i["prev"], 2),
                        "previous_oi": int(oi * 0.9), "previous_volume": 10000, "top_ask_price": round(px + 0.05, 2),
                        "top_ask_quantity": 750, "top_bid_price": round(max(px - 0.05, 0.05), 2), "top_bid_quantity": 600,
                        "volume": 50000}
            return 200, {"data": {"last_price": m.q(ul), "oc": oc}, "status": "success"}
        if path == "/charts/historical":
            i = m.inst.get((b["exchangeSegment"], str(b["securityId"])))
            if not i:
                return 400, err("DH-907", "Data_Error", "No data")
            return 200, daily_series(i, b["fromDate"][:10], b["toDate"][:10])
        if path == "/charts/intraday":
            i = m.inst.get((b["exchangeSegment"], str(b["securityId"])))
            if not i:
                return 400, err("DH-907", "Data_Error", "No data")
            n = 75
            step = int(b.get("interval") or 5) * 60
            end = int(time.time()) // step * step
            px = i["px"]
            rng = random.Random(int(i["id"]))
            rows = []
            for k in range(n):
                o = px
                c = px * (1 + rng.gauss(0, 0.002))
                rows.append((end - (n - k) * step, o, max(o, c) * 1.001, min(o, c) * 0.999, c, rng.randint(1000, 90000)))
                px = c
            scale = i["px"] / px
            return 200, {"timestamp": [r[0] for r in rows], "open": [round(r[1] * scale, 2) for r in rows],
                         "high": [round(r[2] * scale, 2) for r in rows], "low": [round(r[3] * scale, 2) for r in rows],
                         "close": [round(r[4] * scale, 2) for r in rows], "volume": [r[5] for r in rows]}
        return 404, err("DH-905", "Input_Exception", f"No such endpoint {method} {path}")


DAILY = {}


def daily_series(i, frm, to):
    """Made-up daily candles (weekdays) that end at the instrument's starting price. Trends come and go,
    so swing setups appear, but there is no real edge in them: a fair test for the bot's edge check."""
    key = (i["seg"], i["id"])
    if key not in DAILY:
        rng = random.Random(zlib.crc32(("%s:%s" % key).encode()))
        days = []
        d = datetime.now(IST).date() - timedelta(days=700)
        today = datetime.now(IST).date()
        while d < today:
            if d.weekday() < 5:
                days.append(d)
            d += timedelta(days=1)
        px, drift, out = 100.0, 0.0, []
        vol = 0.012 if i["kind"] == "INDEX" else 0.018
        for d in days:
            if rng.random() < 0.04:
                drift = rng.gauss(0, 0.004)
            o = px * (1 + rng.gauss(0, vol / 3))
            c = o * (1 + drift + rng.gauss(0, vol))
            h = max(o, c) * (1 + abs(rng.gauss(0, vol / 2)))
            l = min(o, c) * (1 - abs(rng.gauss(0, vol / 2)))
            out.append([d, o, h, l, c, rng.randint(200000, 2000000)])
            px = c
        scale = i["px"] / px
        DAILY[key] = [[r[0]] + [x * scale for x in r[1:5]] + [r[5]] for r in out]
    rows = [r for r in DAILY[key] if frm <= r[0].isoformat() < to]
    ts = lambda d: int(datetime(d.year, d.month, d.day, tzinfo=IST).timestamp())
    out = {"timestamp": [ts(r[0]) for r in rows], "open": [round(r[1], 2) for r in rows],
           "high": [round(r[2], 2) for r in rows], "low": [round(r[3], 2) for r in rows],
           "close": [round(r[4], 2) for r in rows], "volume": [r[5] for r in rows]}
    if i["kind"].startswith("FUT"):                      # futures: open interest that drifts day to day
        rng = random.Random(zlib.crc32(i["id"].encode()))
        oi, ois = 2_000_000, []
        for _ in rows:
            oi = max(100_000, oi * (1 + rng.gauss(0, 0.04)))
            ois.append(int(oi))
        out["open_interest"] = ois
    return out


def check_order(b, super_=False):
    need = ["transactionType", "exchangeSegment", "productType", "orderType", "securityId", "quantity"]
    for k in need:
        if b.get(k) in (None, ""):
            return f"{k} is required"
    if b["transactionType"] not in ("BUY", "SELL"):
        return "transactionType"
    if b["exchangeSegment"] not in ("NSE_EQ", "BSE_EQ", "NSE_FNO", "BSE_FNO", "MCX_COMM", "NSE_CURRENCY", "BSE_CURRENCY"):
        return "exchangeSegment"
    if b["productType"] not in ("CNC", "INTRADAY", "MARGIN", "MTF", "CO", "BO"):
        return "productType"
    if b["orderType"] not in ("LIMIT", "MARKET", "STOP_LOSS", "STOP_LOSS_MARKET"):
        return "orderType"
    if not super_ and b.get("validity") not in ("DAY", "IOC"):
        return "validity"
    if not isinstance(b["quantity"], int) or b["quantity"] <= 0:
        return "quantity"
    if b["orderType"] in ("LIMIT", "STOP_LOSS") and not float(b.get("price") or 0) > 0:
        return "price is required for a limit order"
    if b["orderType"] in ("STOP_LOSS", "STOP_LOSS_MARKET") and not float(b.get("triggerPrice") or 0) > 0:
        return "triggerPrice is required for a stop-loss order"
    return None


def pub(o):
    return {k: v for k, v in o.items() if not k.startswith("_")}


def serve(port=3099, tick_every=1.0):
    global M
    M = Market()
    srv = ThreadingHTTPServer(("127.0.0.1", port), Handler)

    def ticker():
        while True:
            time.sleep(tick_every)
            M.tick()
    threading.Thread(target=ticker, daemon=True).start()
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv


if __name__ == "__main__":
    serve(int(sys.argv[1]) if len(sys.argv) > 1 else 3099)
    print("Pretend Dhan running; Ctrl+C to stop.")
    while True:
        time.sleep(3600)
