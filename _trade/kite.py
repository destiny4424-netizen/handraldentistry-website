"""Zerodha Kite Connect (v3) client for Handral Trading Desk, speaking Dhan's dialect.

The desk was written for Dhan (dhan.py). To run the same desk on a Zerodha account, this class has the same methods
as dhan.Dhan and gives back the same shapes (orderId, orderStatus, exchangeSegment, securityId, netQty, ...), so
everything else in the app (orders, positions, alerts, insights, the auto-trader, the screener check) works
unchanged. The instrument list (instruments.KiteInstruments) uses Kite's instrument_token as the "security id", so a
key is still "NSE_EQ:<token>".

What Zerodha doesn't have, and what happens instead:
- Super orders (entry + target + stop-loss): done here. The entry is a normal order; once it fills, the stop-loss
  goes in as a real SL order (options: stop-limit, as on Dhan) and the target is watched every 2 seconds: when the
  price reaches it, the stop order is changed to a LIMIT at the target (so only one exit order ever sits at
  Zerodha). Trailing jump moves the stop by the jump each time price moves that much in your favour. Kept in
  kite-super.json; a super order stops being watched when the position is closed some other way, or at the close.
- Option chain: Zerodha has no chain API, so the desk's own fallback builds it from the options' quotes.
- Kill switch and "exit all": no API for the kill switch (use Kite → Account → Kill switch); exit all is done
  here (cancel open orders, then close each position at market).
- Trade history before today: not in the API. Insights fills up from the day the desk is first used.
- Login: a Kite access token lasts until about 6 AM the next day and can't be renewed, so the desk shows
  "Log in with Zerodha" each morning (Kite's own login page; the desk never sees the password).
"""
import hashlib
import json
import math
import os
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone

from dhan import DhanError, SIDES, ORDER_TYPES, VALIDITY  # noqa: F401  (same errors and choices as Dhan)

IST = timezone(timedelta(hours=5, minutes=30))
API = "https://api.kite.trade"
LOGIN = "https://kite.zerodha.com/connect/login"

EXCH_SEG = {"NSE": "NSE_EQ", "BSE": "BSE_EQ", "NFO": "NSE_FNO", "BFO": "BSE_FNO", "CDS": "NSE_CURRENCY",
            "BCD": "BSE_CURRENCY", "MCX": "MCX_COMM"}
PRODUCT_IN = {"CNC": "CNC", "INTRADAY": "MIS", "MARGIN": "NRML", "MTF": "MTF"}
PRODUCT_OUT = {v: k for k, v in PRODUCT_IN.items()}
TYPE_IN = {"LIMIT": "LIMIT", "MARKET": "MARKET", "STOP_LOSS": "SL", "STOP_LOSS_MARKET": "SL-M"}
TYPE_OUT = {v: k for k, v in TYPE_IN.items()}
WORKING = ("PENDING", "TRANSIT", "TRIGGERED", "PART_TRADED")
ERRORS = {"TokenException": "DH-901", "PermissionException": "DH-902", "OrderException": "DH-906",
          "InputException": "DH-905", "DataException": "DH-907", "NetworkException": "DH-909",
          "GeneralException": "DH-910", "MarginException": "DH-906", "HoldingException": "DH-906"}
INTERVAL = {"1": "minute", "3": "3minute", "5": "5minute", "10": "10minute", "15": "15minute", "25": "15minute",
            "30": "30minute", "60": "60minute"}


def checksum(api_key, request_token, api_secret):
    return hashlib.sha256((api_key + request_token + api_secret).encode()).hexdigest()


def status_out(o):
    """Kite's order status -> Dhan's (PENDING, TRANSIT, PART_TRADED, TRADED, REJECTED, CANCELLED)."""
    s = str(o.get("status") or "").upper()
    filled = int(o.get("filled_quantity") or 0)
    if s == "COMPLETE":
        return "TRADED"
    if s in ("REJECTED", "CANCELLED"):
        return s
    if s in ("OPEN", "TRIGGER PENDING", "AMO REQ RECEIVED", "MODIFIED"):
        return "PART_TRADED" if filled else "PENDING"
    return "TRANSIT"


def token_expiry(saved_at_epoch):
    """A Kite access token stops working at about 6 AM IST after the day it was made."""
    if not saved_at_epoch:
        return None
    t = datetime.fromtimestamp(saved_at_epoch, IST)
    nxt = t.replace(hour=6, minute=0, second=0, microsecond=0)
    if t >= nxt:
        nxt += timedelta(days=1)
    return int(nxt.timestamp())


def _round_tick(p, tick):
    tick = tick or 0.05
    return round(round(float(p) / tick) * tick, 2) if p else 0.0


def _stop_limit(trigger, tick, sell):
    """Same as trade.option_stop_limit: options get a stop-limit 1% (at least 2 ticks) past the trigger."""
    tick = tick or 0.05
    gap = max(2 * tick, trigger * 0.01)
    return max(tick, _round_tick(trigger - gap, tick)) if sell else _round_tick(trigger + gap, tick)


def _epoch(ts):
    """Kite's "2026-11-26T09:15:00+0530" (or "2026-11-26 09:15:00") -> epoch seconds."""
    s = str(ts)
    for fmt in ("%Y-%m-%dT%H:%M:%S%z", "%Y-%m-%d %H:%M:%S%z", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%d %H:%M:%S"):
        try:
            d = datetime.strptime(s, fmt)
            if not d.tzinfo:
                d = d.replace(tzinfo=IST)
            return int(d.timestamp())
        except ValueError:
            continue
    return 0


class Kite:
    broker = "zerodha"

    def __init__(self, api_key="", token="", base=API, inst=None, data_dir=None):
        self.api_key, self.token, self.base = api_key, token, base.rstrip("/")
        self.api_secret = ""
        self.client_id = ""                            # Kite user id once logged in
        self.inst = inst                               # instruments.KiteInstruments
        self._gap = {}
        self._lock = threading.Lock()
        self._mp = True                                # send market_protection with market orders (see _place)
        self.sup_file = os.path.join(data_dir, "kite-super.json") if data_dir else None
        self.sup = self._load_sup()
        self.sup_lock = threading.RLock()
        self.sup_note = ""

    @property
    def ready(self):
        return bool(self.api_key and self.token and self.client_id)

    # ---- plumbing ---------------------------------------------------------------------------------------------
    def _pace(self, path):
        group, gap = None, 0
        if path.startswith("/quote"):
            group, gap = "quote", 1.05                 # Kite: quotes 1 request a second
        elif path.startswith("/instruments/historical"):
            group, gap = "hist", 0.35                  # 3 a second
        elif path.startswith("/orders") or path.startswith("/gtt"):
            group, gap = "orders", 0.11                # 10 a second
        if not group:
            return
        with self._lock:
            wait = gap - (time.time() - self._gap.get(group, 0))
            if wait > 0:
                time.sleep(wait)
            self._gap[group] = time.time()

    def _http(self, method, path, params=None, json_body=None, auth=True, timeout=20):
        if auth and not (self.api_key and self.token):
            raise DhanError("Zerodha isn't connected yet. Open Account → Zerodha login and tap Log in with Zerodha.")
        self._pace(path)
        url = self.base + path
        data = None
        headers = {"X-Kite-Version": "3", "Accept": "application/json"}
        if auth:
            headers["Authorization"] = f"token {self.api_key}:{self.token}"
        if json_body is not None:
            data = json.dumps(json_body).encode()
            headers["Content-Type"] = "application/json"
        elif params is not None and method in ("POST", "PUT"):
            data = urllib.parse.urlencode(params, doseq=True).encode()
            headers["Content-Type"] = "application/x-www-form-urlencoded"
        elif params:
            url += "?" + urllib.parse.urlencode(params, doseq=True)
        req = urllib.request.Request(url, data=data, method=method, headers=headers)
        try:
            with urllib.request.urlopen(req, timeout=timeout) as r:
                text = r.read().decode(errors="replace")
        except urllib.error.HTTPError as e:
            text = e.read().decode(errors="replace")
            try:
                j = json.loads(text)
            except ValueError:
                j = {}
            et = str(j.get("error_type") or "") if isinstance(j, dict) else ""
            msg = str(j.get("message") or "") if isinstance(j, dict) else ""
            code = ERRORS.get(et, "DH-901" if e.code in (401, 403) else "DH-904" if e.code == 429 else "DH-910")
            base = {"DH-901": "Zerodha rejected the login (it ends at 6 AM each day): log in with Zerodha again.",
                    "DH-904": "Zerodha is rate-limiting requests. Wait a moment and try again."}.get(code, f"Zerodha error {e.code}.")
            if code == "DH-901" and auth:               # our access token was refused (not a failed login attempt)
                self.token = ""                        # the app shows "not connected" until the next login
            raise DhanError(f"{base} {('Zerodha says: ' + msg) if msg else text[:200]}".strip(),
                            code=code, status=e.code, raw=text[:1000]) from None
        except (urllib.error.URLError, TimeoutError, OSError) as e:
            raise DhanError(f"Can't reach Zerodha: {getattr(e, 'reason', e)}") from None
        try:
            j = json.loads(text) if text.strip() else {}
        except ValueError:
            raise DhanError(f"Zerodha sent something unexpected: {text[:200]}") from None
        return j.get("data") if isinstance(j, dict) and "data" in j else j

    def _it(self, seg, sid):
        it = self.inst.get(f"{seg}:{sid}") if self.inst else None
        if not it or not it.get("ts"):
            raise DhanError(f"Unknown instrument {seg}:{sid} (Zerodha's list may not have loaded yet).", code="DH-905")
        return it

    def _key_of(self, row):
        """(seg, token) of a Kite order / trade / position row."""
        tok = str(row.get("instrument_token") or "")
        exch = str(row.get("exchange") or "")
        seg = EXCH_SEG.get(exch, exch)
        if self.inst and tok and f"{seg}:{tok}" not in self.inst.by_key:
            k = self.inst.tok.get(tok) if hasattr(self.inst, "tok") else None
            if k:
                seg = k.split(":")[0]
        if not tok and self.inst:                      # holdings and some rows: find it by symbol
            k = self.inst.by_ts.get((exch, str(row.get("tradingsymbol") or ""))) if hasattr(self.inst, "by_ts") else None
            if k:
                seg, tok = k.split(":")
        return seg, tok

    # ---- login ---------------------------------------------------------------------------------------------------
    def login_url(self, state=""):
        q = {"v": 3, "api_key": self.api_key}
        if state:
            q["redirect_params"] = urllib.parse.urlencode({"state": state})
        return f"{LOGIN}?{urllib.parse.urlencode(q)}"

    def exchange(self, request_token, api_secret=None):
        """request_token from Kite's redirect -> access token. Returns the session (user_id, user_name, ...)."""
        secret = api_secret or self.api_secret
        d = self._http("POST", "/session/token", {"api_key": self.api_key, "request_token": request_token,
                                                  "checksum": checksum(self.api_key, request_token, secret)}, auth=False)
        if not isinstance(d, dict) or not d.get("access_token"):
            raise DhanError("Zerodha didn't give an access token.")
        return d

    def profile(self):
        return self._http("GET", "/user/profile")

    # ---- orders --------------------------------------------------------------------------------------------------
    def _params(self, o):
        it = self._it(o["exchangeSegment"], o["securityId"])
        otype = TYPE_IN.get(o["orderType"], o["orderType"])
        p = {"tradingsymbol": it["ts"], "exchange": it["kx"], "transaction_type": o["transactionType"],
             "order_type": otype, "quantity": int(o["quantity"]), "product": PRODUCT_IN.get(o["productType"], o["productType"]),
             "validity": o.get("validity") or "DAY"}
        if otype in ("LIMIT", "SL"):
            p["price"] = float(o.get("price") or 0)
        if otype in ("SL", "SL-M"):
            p["trigger_price"] = float(o.get("triggerPrice") or 0)
        if int(o.get("disclosedQuantity") or 0):
            p["disclosed_quantity"] = int(o["disclosedQuantity"])
        if o.get("correlationId"):
            p["tag"] = "".join(c for c in str(o["correlationId"]) if c.isalnum())[:20]
        return p, it

    def _place(self, variety, p):
        """Market and SL-M orders carry market_protection (Zerodha's guard against a fill far from the price;
        -1 = Zerodha's own default). If Zerodha doesn't know the field, it is dropped from then on."""
        if self._mp and p.get("order_type") in ("MARKET", "SL-M"):
            try:
                return self._http("POST", f"/orders/{variety}", dict(p, market_protection=-1))
            except DhanError as e:
                if "market_protection" not in str(e).lower():
                    raise
                self._mp = False
        return self._http("POST", f"/orders/{variety}", p)

    def place_order(self, o):
        p, _ = self._params(o)
        variety = "amo" if o.get("afterMarketOrder") else "regular"
        if o.get("slice"):
            p["autoslice"] = "true"
        d = self._place(variety, p)
        oid = str((d or {}).get("order_id") or "")
        return {"orderId": oid, "orderStatus": "TRANSIT"}

    def modify_order(self, order_id, m):
        cur = self._raw_order(order_id)
        otype = TYPE_IN.get(m["orderType"], m["orderType"])
        p = {"order_type": otype, "quantity": int(m["quantity"]), "validity": m.get("validity") or "DAY"}
        if otype in ("LIMIT", "SL"):
            p["price"] = float(m.get("price") or 0)
        if otype in ("SL", "SL-M"):
            p["trigger_price"] = float(m.get("triggerPrice") or 0)
        if int(m.get("disclosedQuantity") or 0):
            p["disclosed_quantity"] = int(m["disclosedQuantity"])
        d = self._http("PUT", f"/orders/{cur.get('variety') or 'regular'}/{order_id}", p)
        return {"orderId": str((d or {}).get("order_id") or order_id), "orderStatus": "TRANSIT"}

    def cancel_order(self, order_id):
        cur = self._raw_order(order_id)
        d = self._http("DELETE", f"/orders/{cur.get('variety') or 'regular'}/{order_id}")
        return {"orderId": str((d or {}).get("order_id") or order_id), "orderStatus": "CANCELLED"}

    def _raw_orders(self):
        d = self._http("GET", "/orders")
        return d if isinstance(d, list) else []

    def _raw_order(self, order_id):
        d = self._http("GET", f"/orders/{order_id}")
        if isinstance(d, list):
            return d[-1] if d else {}
        return d or {}

    def _order_out(self, o):
        seg, tok = self._key_of(o)
        it = (self.inst.get(f"{seg}:{tok}") if self.inst else None) or {}
        t = str(o.get("order_timestamp") or "")
        u = str(o.get("exchange_update_timestamp") or o.get("exchange_timestamp") or t)
        return {"orderId": str(o.get("order_id") or ""), "orderStatus": status_out(o),
                "transactionType": o.get("transaction_type"), "exchangeSegment": seg, "securityId": tok,
                "productType": PRODUCT_OUT.get(o.get("product"), o.get("product")),
                "orderType": TYPE_OUT.get(o.get("order_type"), o.get("order_type")), "validity": o.get("validity") or "DAY",
                "tradingSymbol": o.get("tradingsymbol"), "quantity": int(o.get("quantity") or 0),
                "price": float(o.get("price") or 0), "triggerPrice": float(o.get("trigger_price") or 0),
                "disclosedQuantity": int(o.get("disclosed_quantity") or 0),
                "afterMarketOrder": o.get("variety") == "amo", "createTime": t, "updateTime": u, "exchangeTime": u,
                "filledQty": int(o.get("filled_quantity") or 0), "remainingQuantity": int(o.get("pending_quantity") or 0),
                "averageTradedPrice": float(o.get("average_price") or 0), "correlationId": o.get("tag") or "",
                "omsErrorDescription": o.get("status_message") or "", "omsErrorCode": "",
                "drvExpiryDate": it.get("expiry") or "NA", "drvStrikePrice": it.get("strike") or 0,
                "drvOptionType": {"CE": "CALL", "PE": "PUT"}.get(it.get("opt"), "NA"),
                "legName": "", "_variety": o.get("variety"), "brokerStatus": o.get("status")}

    def orders(self):
        mine = self._super_order_ids()
        return [self._order_out(o) for o in self._raw_orders() if str(o.get("order_id")) not in mine]

    def order(self, order_id):
        s = self.sup.get(str(order_id))
        if s:                                          # a super order: its entry
            order_id = s["entry"]
        o = self._raw_order(order_id)
        return self._order_out(o) if o else {}

    def trades_today(self):
        d = self._http("GET", "/trades")
        out = []
        for t in d if isinstance(d, list) else []:
            seg, tok = self._key_of(t)
            it = (self.inst.get(f"{seg}:{tok}") if self.inst else None) or {}
            when = str(t.get("exchange_timestamp") or t.get("fill_timestamp") or t.get("order_timestamp") or "")
            out.append({"orderId": str(t.get("order_id") or ""), "exchangeOrderId": str(t.get("exchange_order_id") or ""),
                        "exchangeTradeId": str(t.get("trade_id") or ""), "transactionType": t.get("transaction_type"),
                        "exchangeSegment": seg, "securityId": tok, "productType": PRODUCT_OUT.get(t.get("product"), t.get("product")),
                        "tradingSymbol": t.get("tradingsymbol"), "customSymbol": it.get("name") or t.get("tradingsymbol"),
                        "instrument": it.get("kind"), "tradedQuantity": int(t.get("quantity") or 0),
                        "tradedPrice": float(t.get("average_price") or 0), "createTime": when, "updateTime": when,
                        "exchangeTime": when, "drvExpiryDate": it.get("expiry") or "NA", "drvStrikePrice": it.get("strike") or 0,
                        "drvOptionType": {"CE": "CALL", "PE": "PUT"}.get(it.get("opt"), "NA")})
        return out

    def trade_history(self, from_date, to_date, page=0):
        return []                                      # Kite Connect has no API for past days' trades

    # ---- Forever orders = Kite GTT ------------------------------------------------------------------------------
    def place_forever(self, o):
        it = self._it(o["exchangeSegment"], o["securityId"])
        q = self.quote({o["exchangeSegment"]: [o["securityId"]]}, "ltp")
        last = float((q.get((o["exchangeSegment"], str(o["securityId"]))) or {}).get("last_price") or 0) or float(o["triggerPrice"])
        cond = {"exchange": it["kx"], "tradingsymbol": it["ts"], "trigger_values": [float(o["triggerPrice"])], "last_price": last}
        leg = {"exchange": it["kx"], "tradingsymbol": it["ts"], "transaction_type": o["transactionType"],
               "quantity": int(o["quantity"]), "order_type": "LIMIT", "product": PRODUCT_IN.get(o["productType"], o["productType"]),
               "price": float(o["price"])}
        d = self._http("POST", "/gtt/triggers", {"type": "single", "condition": json.dumps(cond), "orders": json.dumps([leg])})
        return {"orderId": str((d or {}).get("trigger_id") or ""), "orderStatus": "PENDING"}

    def cancel_forever(self, order_id):
        self._http("DELETE", f"/gtt/triggers/{order_id}")
        return {"orderId": str(order_id), "orderStatus": "CANCELLED"}

    def forever_orders(self):
        d = self._http("GET", "/gtt/triggers")
        st = {"active": "PENDING", "triggered": "TRADED", "cancelled": "CANCELLED", "deleted": "CANCELLED",
              "rejected": "REJECTED", "expired": "EXPIRED", "disabled": "CANCELLED"}
        out = []
        for g in d if isinstance(d, list) else []:
            c, legs = g.get("condition") or {}, g.get("orders") or [{}]
            seg, tok = self._key_of({"exchange": c.get("exchange"), "tradingsymbol": c.get("tradingsymbol")})
            out.append({"orderId": str(g.get("id")), "orderStatus": st.get(g.get("status"), str(g.get("status")).upper()),
                        "exchangeSegment": seg, "securityId": tok, "tradingSymbol": c.get("tradingsymbol"),
                        "transactionType": legs[0].get("transaction_type"), "quantity": legs[0].get("quantity"),
                        "price": legs[0].get("price"), "triggerPrice": (c.get("trigger_values") or [0])[0],
                        "productType": PRODUCT_OUT.get(legs[0].get("product"), legs[0].get("product"))})
        return out

    # ---- portfolio & funds ---------------------------------------------------------------------------------------
    def positions(self):
        d = self._http("GET", "/portfolio/positions") or {}
        out = []
        for p in (d.get("net") or []) if isinstance(d, dict) else []:
            seg, tok = self._key_of(p)
            net = int(p.get("quantity") or 0)
            mult = float(p.get("multiplier") or 1) or 1
            pnl = p.get("pnl")
            if pnl is None:
                pnl = float(p.get("sell_value") or 0) - float(p.get("buy_value") or 0) + net * float(p.get("last_price") or 0) * mult
            pnl = float(pnl)
            realized = float(p.get("realised") or 0)
            if not net:                                # all booked
                realized, unreal = pnl, 0.0
            else:
                unreal = pnl - realized
            it = (self.inst.get(f"{seg}:{tok}") if self.inst else None) or {}
            out.append({"tradingSymbol": p.get("tradingsymbol"), "securityId": tok, "exchangeSegment": seg,
                        "positionType": "LONG" if net > 0 else "SHORT" if net < 0 else "CLOSED",
                        "productType": PRODUCT_OUT.get(p.get("product"), p.get("product")),
                        "buyAvg": float(p.get("buy_price") or 0), "sellAvg": float(p.get("sell_price") or 0),
                        "buyQty": int(p.get("buy_quantity") or 0), "sellQty": int(p.get("sell_quantity") or 0),
                        "netQty": net, "costPrice": float(p.get("average_price") or 0),
                        "realizedProfit": round(realized, 2), "unrealizedProfit": round(unreal, 2),
                        "multiplier": mult, "carryForwardBuyQty": int(p.get("overnight_quantity") or 0) if int(p.get("overnight_quantity") or 0) > 0 else 0,
                        "carryForwardSellQty": -int(p.get("overnight_quantity") or 0) if int(p.get("overnight_quantity") or 0) < 0 else 0,
                        "dayBuyQty": int(p.get("day_buy_quantity") or 0), "daySellQty": int(p.get("day_sell_quantity") or 0),
                        "dayBuyValue": float(p.get("day_buy_value") or 0), "daySellValue": float(p.get("day_sell_value") or 0),
                        "drvExpiryDate": it.get("expiry") or "0001-01-01", "drvStrikePrice": it.get("strike") or 0,
                        "drvOptionType": {"CE": "CALL", "PE": "PUT"}.get(it.get("opt")), "_mult": mult if seg == "MCX_COMM" else None,
                        "lastPrice": float(p.get("last_price") or 0)})
        return out

    def holdings(self):
        d = self._http("GET", "/portfolio/holdings")
        out = []
        for h in d if isinstance(d, list) else []:
            seg, tok = self._key_of(h)
            qty, t1 = int(h.get("quantity") or 0), int(h.get("t1_quantity") or 0)
            out.append({"exchange": h.get("exchange"), "tradingSymbol": h.get("tradingsymbol"), "securityId": tok,
                        "isin": h.get("isin"), "totalQty": qty + t1, "dpQty": qty, "t1Qty": t1,
                        "availableQty": max(0, qty - int(h.get("used_quantity") or 0)),
                        "collateralQty": int(h.get("collateral_quantity") or 0), "avgCostPrice": float(h.get("average_price") or 0),
                        "lastTradedPrice": float(h.get("last_price") or 0)})
        return out

    def exit_all(self):
        """Zerodha has no "exit all": cancel open orders, then close every position at market."""
        cancelled, closed, problems = 0, 0, []
        for o in self._raw_orders():
            if status_out(o) in WORKING:
                try:
                    self._http("DELETE", f"/orders/{o.get('variety') or 'regular'}/{o['order_id']}")
                    cancelled += 1
                except DhanError as e:
                    problems.append(f"{o.get('tradingsymbol')}: {e}")
        with self.sup_lock:
            for s in self.sup.values():
                if s["status"] in ("PENDING", "TRADED"):
                    s["status"], s["note"] = "CLOSED", "Exit all"
            self._save_sup()
        for p in self.positions():
            net = int(p["netQty"])
            if not net:
                continue
            try:
                self.place_order({"securityId": p["securityId"], "exchangeSegment": p["exchangeSegment"],
                                  "transactionType": "SELL" if net > 0 else "BUY", "quantity": abs(net),
                                  "orderType": "MARKET", "productType": p["productType"], "validity": "DAY"})
                closed += 1
            except DhanError as e:
                problems.append(f"{p['tradingSymbol']}: {e}")
        msg = f"{closed} position(s) closed at market, {cancelled} open order(s) cancelled."
        if problems:
            msg += " Problems: " + "; ".join(problems)
        return {"status": "SUCCESS" if not problems else "PARTIAL", "message": msg}

    def convert_position(self, c):
        it = self._it(c["exchangeSegment"], c["securityId"])
        frm = PRODUCT_IN.get(c["fromProductType"], c["fromProductType"])
        to = PRODUCT_IN.get(c["toProductType"], c["toProductType"])
        side = "BUY" if str(c.get("positionType")).upper() == "LONG" else "SELL"
        return self._http("PUT", "/portfolio/positions", {
            "tradingsymbol": it["ts"], "exchange": it["kx"], "transaction_type": side,
            "position_type": "overnight" if frm in ("NRML", "CNC") and c.get("carry") else "day",
            "quantity": int(c["convertQty"]), "old_product": frm, "new_product": to})

    def funds(self):
        d = self._http("GET", "/user/margins") or {}
        e = d.get("equity") or {}
        a, u = e.get("available") or {}, e.get("utilised") or {}
        net = float(e.get("net") or 0)
        return {"dhanClientId": self.client_id, "availabelBalance": round(net, 2), "availableBalance": round(net, 2),
                "sodLimit": float(a.get("opening_balance") or 0), "collateralAmount": float(a.get("collateral") or 0),
                "receiveableAmount": float(a.get("intraday_payin") or 0), "utilizedAmount": float(u.get("debits") or 0),
                "blockedPayoutAmount": float(u.get("payout") or 0),
                "withdrawableBalance": round(max(0.0, min(net, float(a.get("live_balance") or a.get("cash") or 0))), 2),
                "commodity": (d.get("commodity") or {}).get("net")}

    def margin(self, o):
        p, _ = self._params(dict(o, orderType=o["orderType"] if o["orderType"] in TYPE_IN else "LIMIT"))
        row = {"exchange": p["exchange"], "tradingsymbol": p["tradingsymbol"], "transaction_type": p["transaction_type"],
               "variety": "regular", "product": p["product"], "order_type": p["order_type"], "quantity": p["quantity"],
               "price": float(o.get("price") or 0), "trigger_price": float(o.get("triggerPrice") or 0)}
        d = self._http("POST", "/margins/orders", json_body=[row])
        m = (d or [{}])[0] if isinstance(d, list) else (d or {})
        total = float(m.get("total") or 0)
        avail = None
        try:
            avail = self.funds()["availabelBalance"]
        except DhanError:
            pass
        ch = m.get("charges") or {}
        out = {"totalMargin": round(total, 2), "spanMargin": float(m.get("span") or 0),
               "exposureMargin": float(m.get("exposure") or 0), "brokerage": float(ch.get("brokerage") or 0),
               "leverage": m.get("leverage")}
        if avail is not None:
            out.update(availableBalance=avail, insufficientBalance=round(max(0.0, total - avail), 2))
        return out

    def kill_switch(self, activate=True):
        raise DhanError("Zerodha's kill switch can't be worked from an app: open Kite → Account → Kill switch "
                        "(on the website: console.zerodha.com → Account → Segment / Kill switch).", code="DH-902")

    def kill_switch_status(self):
        return {"killSwitchStatus": "NOT_AVAILABLE"}

    def renew(self):
        return None                                    # Kite tokens can't be renewed: log in again each morning

    # ---- market data -----------------------------------------------------------------------------------------------
    def quote(self, by_seg, kind="quote"):
        """{"NSE_EQ": [tokens], ...} -> {(seg, token): quote in Kite's (= Dhan's) shape}."""
        names, back = [], {}
        for seg, ids in by_seg.items():
            for sid in ids:
                it = self.inst.get(f"{seg}:{sid}") if self.inst else None
                if not it or not it.get("ts"):
                    continue
                n = f"{it['kx']}:{it['ts']}"
                names.append(n)
                back[n] = (seg, str(sid))
        if not names:
            return {}
        path = {"ltp": "/quote/ltp", "ohlc": "/quote/ohlc"}.get(kind, "/quote")
        out = {}
        size = 500 if path == "/quote" else 1000
        for n in range(0, len(names), size):
            d = self._http("GET", path, {"i": names[n:n + size]}) or {}
            for name, q in d.items() if isinstance(d, dict) else []:
                k = back.get(name)
                if not k or not isinstance(q, dict):
                    continue
                q = dict(q)
                prev = float((q.get("ohlc") or {}).get("close") or 0)
                if prev and q.get("last_price"):
                    q["net_change"] = round(float(q["last_price"]) - prev, 2)
                out[k] = q
        return out

    def expiries(self, under_id, under_seg):
        raise DhanError("Zerodha has no option chain API.", code="DH-907")

    def option_chain(self, under_id, under_seg, expiry):
        raise DhanError("Zerodha has no option chain API.", code="DH-907")  # trade.py builds it from quotes

    def _candles(self, sid, interval, frm, to, oi=False):
        d = self._http("GET", f"/instruments/historical/{sid}/{interval}",
                       {"from": frm, "to": to, "continuous": 0, "oi": 1 if oi else 0}) or {}
        rows = d.get("candles") or [] if isinstance(d, dict) else []
        out = {"timestamp": [], "open": [], "high": [], "low": [], "close": [], "volume": []}
        ois = []
        for r in rows:
            if len(r) < 6:
                continue
            out["timestamp"].append(_epoch(r[0]))
            for k, v in zip(("open", "high", "low", "close", "volume"), r[1:6]):
                out[k].append(v)
            if oi:
                ois.append(r[6] if len(r) > 6 else 0)
        if oi:
            out["open_interest"] = ois
        return out

    def intraday(self, sid, seg, inst, interval, from_dt, to_dt):
        iv = INTERVAL.get(str(interval), "5minute")
        frm = datetime.strptime(from_dt[:19], "%Y-%m-%d %H:%M:%S") if len(from_dt) > 10 else datetime.strptime(from_dt, "%Y-%m-%d")
        to = datetime.strptime(to_dt[:19], "%Y-%m-%d %H:%M:%S") if len(to_dt) > 10 else datetime.strptime(to_dt, "%Y-%m-%d")
        if iv == "minute" and (to - frm).days > 59:     # Kite: 60 days a request for 1-minute candles
            frm = to - timedelta(days=59)
        return self._candles(sid, iv, frm.strftime("%Y-%m-%d %H:%M:%S"), to.strftime("%Y-%m-%d %H:%M:%S"))

    def daily(self, sid, seg, inst, from_d, to_d, oi=False):
        to = min(datetime.strptime(to_d[:10], "%Y-%m-%d"), datetime.now(IST).replace(tzinfo=None) + timedelta(days=1))
        return self._candles(sid, "day", f"{from_d[:10]} 00:00:00", to.strftime("%Y-%m-%d 23:59:59"), oi=oi)

    def call(self, method, path, body=None, timeout=20):
        """The few places that call Dhan's endpoints directly (movers: daily candles with OI)."""
        if path == "/charts/historical":
            return self.daily(body["securityId"], body.get("exchangeSegment"), body.get("instrument"),
                              body["fromDate"], body["toDate"], oi=bool(body.get("oi")))
        if path == "/charts/intraday":
            return self.intraday(body["securityId"], body.get("exchangeSegment"), body.get("instrument"),
                                 body.get("interval"), body["fromDate"], body["toDate"])
        raise DhanError(f"Zerodha has no equivalent of {path}.", code="DH-905")

    # ---- super orders (entry + target + stop-loss), run by this app -----------------------------------------------
    def _load_sup(self):
        try:
            with open(self.sup_file) as f:
                d = json.load(f)
            return d if isinstance(d, dict) else {}
        except (OSError, ValueError, TypeError):
            return {}

    def _save_sup(self):
        if not self.sup_file:
            return
        today = datetime.now(IST).strftime("%Y-%m-%d")
        keep = {k: v for k, v in self.sup.items() if v.get("day", today) >= (datetime.now(IST) - timedelta(days=7)).strftime("%Y-%m-%d")}
        tmp = self.sup_file + ".tmp"
        with open(tmp, "w") as f:
            json.dump(keep, f, indent=1)
        os.replace(tmp, self.sup_file)
        self.sup = keep

    def _super_order_ids(self):
        ids = set()
        for s in self.sup.values():
            ids.add(str(s["entry"]))
            ids.update(str(x) for x in s.get("exits", []))
        return ids

    def place_super_order(self, o):
        """Entry now; the stop-loss and the target are looked after by tick() once it fills."""
        r = self.place_order(dict(o, validity="DAY"))
        oid = r["orderId"]
        it = self._it(o["exchangeSegment"], o["securityId"])
        with self.sup_lock:
            self.sup[oid] = {"id": oid, "entry": oid, "seg": o["exchangeSegment"], "sid": str(o["securityId"]),
                             "sym": it["ts"], "side": o["transactionType"], "qty": int(o["quantity"]),
                             "product": o["productType"], "otype": o["orderType"], "price": float(o.get("price") or 0),
                             "target": float(o.get("targetPrice") or 0), "sl": float(o.get("stopLossPrice") or 0),
                             "trail": float(o.get("trailingJump") or 0), "tick": it.get("tick") or 0.05,
                             "opt": bool(it.get("opt")), "status": "PENDING", "entry_status": "TRANSIT",
                             "filled": 0, "avg": 0.0, "sl_order": None, "sl_trigger": 0.0, "target_order": None,
                             "target_status": "PENDING" if float(o.get("targetPrice") or 0) else "",
                             "sl_status": "PENDING" if float(o.get("stopLossPrice") or 0) else "", "best": 0.0,
                             "exits": [], "note": "", "day": datetime.now(IST).strftime("%Y-%m-%d"),
                             "created": datetime.now(IST).strftime("%Y-%m-%d %H:%M:%S")}
            self._save_sup()
        return {"orderId": oid, "orderStatus": "TRANSIT"}

    def super_orders(self):
        out = []
        with self.sup_lock:
            for s in sorted(self.sup.values(), key=lambda x: x["created"], reverse=True):
                exit_side = "SELL" if s["side"] == "BUY" else "BUY"
                left = s["filled"] or s["qty"]
                legs = []
                if s["target"] or s["target_status"]:
                    legs.append({"orderId": s.get("target_order") or s["id"], "legName": "TARGET_LEG", "transactionType": exit_side,
                                 "remainingQuantity": 0 if s["target_status"] in ("TRADED", "CANCELLED") else left,
                                 "price": s["target"], "orderStatus": s["target_status"] or "CANCELLED", "trailingJump": 0})
                if s["sl"] or s["sl_status"]:
                    legs.append({"orderId": s.get("sl_order") or s["id"], "legName": "STOP_LOSS_LEG", "transactionType": exit_side,
                                 "remainingQuantity": 0 if s["sl_status"] in ("TRADED", "CANCELLED") else left,
                                 "price": s["sl_trigger"] or s["sl"], "orderStatus": s["sl_status"] or "CANCELLED",
                                 "trailingJump": s["trail"]})
                st = s["entry_status"]
                out.append({"orderId": s["id"], "orderStatus": "CLOSED" if s["status"] == "CLOSED" else st,
                            "transactionType": s["side"], "exchangeSegment": s["seg"], "securityId": s["sid"],
                            "tradingSymbol": s["sym"], "productType": s["product"], "orderType": s["otype"],
                            "quantity": s["qty"], "price": s["price"], "filledQty": s["filled"],
                            "averageTradedPrice": s["avg"], "createTime": s["created"], "updateTime": s["created"],
                            "legName": "ENTRY_LEG", "omsErrorDescription": s["note"], "legDetails": legs,
                            "targetPrice": s["target"], "stopLossPrice": s["sl"], "trailingJump": s["trail"]})
        return out

    def modify_super_leg(self, order_id, leg, m):
        with self.sup_lock:
            s = self.sup.get(str(order_id))
            if not s:
                raise DhanError("That super order isn't known any more.", code="DH-905")
            if leg == "TARGET_LEG":
                s["target"] = float(m["targetPrice"])
                if s["target_status"] in ("", "CANCELLED") and s["status"] != "CLOSED":
                    s["target_status"] = "PENDING"
            elif leg == "STOP_LOSS_LEG":
                new = float(m["stopLossPrice"])
                s["trail"] = float(m.get("trailingJump") or 0)
                if s.get("sl_order") and s["sl_status"] in WORKING:
                    self._move_sl(s, new)
                s["sl"] = new
                if s["sl_status"] in ("", "CANCELLED") and s["status"] != "CLOSED":
                    s["sl_status"] = "PENDING"
                s["best"] = 0.0                        # trailing starts again from the new stop
                s["sl_base"] = new
            else:
                if s["entry_status"] not in WORKING:
                    raise DhanError("The entry has already filled or closed.", code="DH-906")
                self.modify_order(s["entry"], {"orderType": m["orderType"], "quantity": int(m["quantity"]),
                                               "price": float(m.get("price") or 0)})
                s.update(qty=int(m["quantity"]), price=float(m.get("price") or 0), otype=m["orderType"])
                if m.get("targetPrice"):
                    s["target"] = float(m["targetPrice"])
                if m.get("stopLossPrice"):
                    s["sl"] = float(m["stopLossPrice"])
            self._save_sup()
        return {"orderId": str(order_id), "orderStatus": "PENDING"}

    def cancel_super(self, order_id, leg="ENTRY_LEG"):
        with self.sup_lock:
            s = self.sup.get(str(order_id))
            if not s:
                raise DhanError("That super order isn't known any more.", code="DH-905")
            if leg in ("ENTRY_LEG", "STOP_LOSS_LEG") and s.get("sl_order") and s["sl_status"] in WORKING:
                self._cancel_quiet(s["sl_order"])
                s["sl_status"] = "CANCELLED"
            if leg == "ENTRY_LEG":
                if s["entry_status"] in WORKING:
                    self.cancel_order(s["entry"])
                    s["entry_status"] = "CANCELLED"
                s["target_status"] = "CANCELLED" if s["target_status"] not in ("TRADED",) else s["target_status"]
                s["status"] = "CLOSED"
                s["note"] = "Cancelled"
            elif leg == "TARGET_LEG":
                s["target_status"] = "CANCELLED"
            elif leg == "STOP_LOSS_LEG":
                s["sl_status"] = "CANCELLED"
            self._save_sup()
        return {"orderId": str(order_id), "orderStatus": "CANCELLED"}

    def _cancel_quiet(self, oid):
        try:
            self.cancel_order(oid)
        except DhanError:
            pass

    def _sl_params(self, s, trigger):
        sell = s["side"] == "BUY"
        trigger = _round_tick(trigger, s["tick"])
        if s["opt"]:
            return {"orderType": "STOP_LOSS", "triggerPrice": trigger, "price": _stop_limit(trigger, s["tick"], sell)}
        return {"orderType": "STOP_LOSS_MARKET", "triggerPrice": trigger, "price": 0}

    def _move_sl(self, s, trigger):
        p = self._sl_params(s, trigger)
        self.modify_order(s["sl_order"], dict(p, quantity=s["filled"] or s["qty"], validity="DAY"))
        s["sl_trigger"] = p["triggerPrice"]

    def _exit(self, s, **kw):
        o = {"securityId": s["sid"], "exchangeSegment": s["seg"], "transactionType": "SELL" if s["side"] == "BUY" else "BUY",
             "quantity": s["filled"] or s["qty"], "productType": s["product"], "validity": "DAY"}
        o.update(kw)
        r = self.place_order(o)
        s["exits"].append(r["orderId"])
        return r["orderId"]

    def active_supers(self):
        return [s for s in self.sup.values() if s["status"] != "CLOSED"]

    def tick(self, quotes=None):
        """Look after the super orders: stop-loss once the entry fills, target, trailing, and the other leg."""
        with self.sup_lock:
            act = self.active_supers()
            if not act or not self.ready:
                return
            try:
                book = {str(o.get("order_id")): o for o in self._raw_orders()}
            except DhanError as e:
                self.sup_note = str(e)
                return
            need = {}
            for s in act:
                need.setdefault(s["seg"], []).append(s["sid"])
            ltp = {}
            for s in act:
                q = (quotes or {}).get(f"{s['seg']}:{s['sid']}") or {}
                if q.get("ltp") and time.time() - q.get("t", 0) < 5:
                    ltp[(s["seg"], s["sid"])] = float(q["ltp"])
            missing = {seg: [i for i in ids if (seg, i) not in ltp] for seg, ids in need.items()}
            if any(missing.values()):
                try:
                    for k, q in self.quote({k: v for k, v in missing.items() if v}, "ltp").items():
                        ltp[k] = float(q.get("last_price") or 0)
                except DhanError:
                    pass
            changed = False
            for s in act:
                try:
                    changed |= self._tick_one(s, book, ltp.get((s["seg"], s["sid"]), 0.0))
                    if s["status"] == "TRADED" and s.get("sl_order") is None and not s["target"] and s["sl_status"] != "PENDING":
                        s["status"] = "CLOSED"
                except DhanError as e:
                    s["note"] = str(e)[:200]
                    changed = True
            # a super whose position was closed some other way stops being watched (its stop would open a new one)
            if any(s["status"] == "TRADED" for s in act):
                try:
                    pos = {(p["exchangeSegment"], str(p["securityId"]), p["productType"]): int(p["netQty"]) for p in self.positions()}
                except DhanError:
                    pos = None
                if pos is not None:
                    for s in act:
                        if s["status"] != "TRADED":
                            continue
                        n = pos.get((s["seg"], s["sid"], s["product"]), 0)
                        flat = n == 0 or (n > 0) != (s["side"] == "BUY")
                        if flat and time.time() - s.get("filled_at", 0) > 10:
                            if s.get("sl_order") and s["sl_status"] in WORKING:
                                self._cancel_quiet(s["sl_order"])
                                s["sl_status"] = "CANCELLED"
                            if s["target_status"] == "PENDING":
                                s["target_status"] = "CANCELLED"
                            s["status"], s["note"] = "CLOSED", s["note"] or "Position closed"
                            changed = True
            if changed:
                self._save_sup()

    def _tick_one(self, s, book, ltp):
        changed = False
        e = book.get(str(s["entry"]))
        if e:
            st = status_out(e)
            if st != s["entry_status"]:
                s["entry_status"], changed = st, True
            s["filled"] = int(e.get("filled_quantity") or 0)
            s["avg"] = float(e.get("average_price") or 0)
            if st in ("REJECTED", "CANCELLED") and not s["filled"]:
                s["status"] = "CLOSED"
                s["note"] = e.get("status_message") or st.title()
                s["target_status"] = s["sl_status"] = "CANCELLED" if s["target"] or s["sl"] else ""
                return True
        if s["entry_status"] != "TRADED" and not (s["entry_status"] in ("CANCELLED",) and s["filled"]):
            return changed
        if s["status"] == "PENDING":
            s["status"], s["filled_at"], changed = "TRADED", time.time(), True
        sell = s["side"] == "BUY"
        # the stop-loss order, once
        if s["sl"] and not s.get("sl_order") and s["sl_status"] == "PENDING":
            p = self._sl_params(s, s["sl"])
            s["sl_order"] = self._exit(s, orderType=p["orderType"], triggerPrice=p["triggerPrice"], price=p["price"])
            s["sl_trigger"], s["sl_base"] = p["triggerPrice"], s["sl"]
            changed = True
        # the exit orders' state
        for which in ("sl_order", "target_order"):
            oid = s.get(which)
            o = book.get(str(oid)) if oid else None
            if not o:
                continue
            st = status_out(o)
            leg = "sl_status" if which == "sl_order" else "target_status"
            if which == "sl_order" and s.get("target_hit"):
                leg = "target_status"                  # the stop order became the target limit
            if st == "TRADED":
                s[leg] = "TRADED"
                other = "target_status" if leg == "sl_status" else "sl_status"
                if s[other] in ("PENDING",) + WORKING:
                    s[other] = "CANCELLED"
                s["status"] = "CLOSED"
                s["note"] = "Target hit" if leg == "target_status" else "Stop-loss hit"
                return True
            if st in ("REJECTED", "CANCELLED") and s[leg] in ("PENDING",) + WORKING:
                s[leg] = st
                if st == "REJECTED":
                    s["note"] = ("Stop-loss" if leg == "sl_status" else "Target") + " rejected: " + str(o.get("status_message") or "")
                changed = True
            elif st in WORKING and s[leg] != st and s[leg] not in ("TRADED",):
                s[leg] = "PENDING" if st == "TRANSIT" else st
        if not ltp:
            return changed
        # target reached: the stop order becomes a limit at the target (or a new limit if there's no stop order)
        if s["target"] and s["target_status"] == "PENDING" and not s.get("target_hit"):
            hit = ltp >= s["target"] if sell else ltp <= s["target"]
            if hit:
                tgt = _round_tick(s["target"], s["tick"])
                if s.get("sl_order") and s["sl_status"] in WORKING + ("PENDING",):
                    try:
                        self.modify_order(s["sl_order"], {"orderType": "LIMIT", "quantity": s["filled"] or s["qty"],
                                                          "price": tgt, "triggerPrice": 0, "validity": "DAY"})
                    except DhanError:                  # Zerodha wouldn't change it: cancel it, then a fresh limit
                        self.cancel_order(s["sl_order"])
                        s["sl_status"] = "CANCELLED"
                        s["sl_order"] = None
                        s["target_order"] = self._exit(s, orderType="LIMIT", price=tgt)
                    s["sl_status"] = "CANCELLED"
                else:
                    s["target_order"] = self._exit(s, orderType="LIMIT", price=tgt)
                s["target_hit"] = True
                return True
        # trailing: the stop moves by the jump each time price moves that much past the entry
        if s["trail"] and s.get("sl_order") and s["sl_status"] in WORKING + ("PENDING",) and not s.get("target_hit"):
            ref = s["avg"] or s["price"]
            s["best"] = max(s["best"] or ltp, ltp) if sell else min(s["best"] or ltp, ltp)
            steps = math.floor(((s["best"] - ref) if sell else (ref - s["best"])) / s["trail"])
            if steps > 0:
                base = s.get("sl_base") or s["sl"]
                new = _round_tick(base + steps * s["trail"] if sell else base - steps * s["trail"], s["tick"])
                if (sell and new > s["sl_trigger"] and new < ltp) or (not sell and new < s["sl_trigger"] and new > ltp):
                    self._move_sl(s, new)
                    changed = True
        return changed

    def super_loop(self, quotes=None):
        while True:
            try:
                if self.active_supers():
                    self.tick(quotes)
                    time.sleep(2)
                else:
                    time.sleep(5)
            except Exception as e:                     # never let this thread die
                self.sup_note = f"Super orders: {e}"
                time.sleep(5)
