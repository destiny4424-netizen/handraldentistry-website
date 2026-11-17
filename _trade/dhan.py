"""Dhan v2 REST client for Meridian Trade (orders, portfolio, funds, quotes, option chain).

Request formats follow Dhan's official Python library (dhanhq 2.3): every request body
carries dhanClientId, headers are access-token and client-id. Errors come back as
{"errorType", "errorCode", "errorMessage"}; DhanError carries Dhan's own message so the
app can show it word for word.
"""
import base64
import json
import threading
import time
import urllib.error
import urllib.request

ERRORS = {
    "DH-901": "Dhan rejected the access token. It may have expired: paste a new one in Settings.",
    "DH-902": "Your Dhan account doesn't have access to this API (for prices and charts it needs "
              "Dhan's Data API plan).",
    "DH-903": "Dhan says there is a problem with the account: the segment may not be activated, "
              "or the static IP for API orders isn't set.",
    "DH-904": "Dhan is rate-limiting requests. Wait a moment and try again.",
    "DH-905": "Dhan didn't accept the details of the request.",
    "DH-906": "Dhan rejected the order.",
    "DH-907": "Dhan couldn't find data for this request.",
    "DH-908": "Dhan had an internal error. Try again.",
    "DH-909": "Dhan had a network problem. Try again.",
    "DH-910": "Dhan returned an error.",
}

SEGMENTS = ("NSE_EQ", "BSE_EQ", "NSE_FNO", "BSE_FNO", "NSE_CURRENCY", "BSE_CURRENCY", "MCX_COMM", "IDX_I")
PRODUCTS = ("CNC", "INTRADAY", "MARGIN", "MTF")
ORDER_TYPES = ("LIMIT", "MARKET", "STOP_LOSS", "STOP_LOSS_MARKET")
VALIDITY = ("DAY", "IOC")
SIDES = ("BUY", "SELL")
LEGS = ("ENTRY_LEG", "TARGET_LEG", "STOP_LOSS_LEG")


class DhanError(Exception):
    def __init__(self, message, code="", status=0, raw=""):
        super().__init__(message)
        self.code, self.status, self.raw = code, status, raw


def token_expiry(token):
    """Expiry (epoch seconds) from the access token's JWT payload, or None."""
    try:
        part = token.split(".")[1]
        return int(json.loads(base64.urlsafe_b64decode(part + "=" * (-len(part) % 4)))["exp"])
    except (IndexError, ValueError, KeyError, TypeError, AttributeError):
        return None


class Dhan:
    def __init__(self, client_id="", token="", base="https://api.dhan.co/v2"):
        self.client_id, self.token, self.base = client_id, token, base.rstrip("/")
        self._gap = {}                                 # endpoint group -> time of the last call
        self._lock = threading.Lock()

    @property
    def ready(self):
        return bool(self.client_id and self.token)

    # Dhan's limits: market quotes 1 request a second, option chain 1 every 3 seconds.
    def _pace(self, path):
        group, gap = None, 0
        if path.startswith("/marketfeed"):
            group, gap = "feed", 1.05
        elif path.startswith("/optionchain"):
            group, gap = "chain", 3.05
        elif path.startswith("/charts"):
            group, gap = "charts", 0.25
        if not group:
            return
        with self._lock:
            wait = gap - (time.time() - self._gap.get(group, 0))
            if wait > 0:
                time.sleep(wait)
            self._gap[group] = time.time()

    def call(self, method, path, body=None, timeout=20):
        if not self.ready:
            raise DhanError("Dhan isn't connected yet. Add your Client ID and access token in Settings.")
        self._pace(path)
        data = None
        if body is not None:
            body = dict(body, dhanClientId=self.client_id)
            data = json.dumps(body).encode()
        req = urllib.request.Request(self.base + path, data=data, method=method, headers={
            "access-token": self.token, "client-id": self.client_id, "dhanClientId": self.client_id,
            "Content-Type": "application/json", "Accept": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=timeout) as r:
                text = r.read().decode(errors="replace")
                return json.loads(text) if text.strip() else {}
        except urllib.error.HTTPError as e:
            text = e.read().decode(errors="replace")
            try:
                j = json.loads(text)
            except ValueError:
                j = {}
            code = str((j or {}).get("errorCode") or "") if isinstance(j, dict) else ""
            msg = str((j or {}).get("errorMessage") or (j or {}).get("remarks") or "") if isinstance(j, dict) else ""
            if not code:
                code = next((c for c in ERRORS if c in text), "")
            if e.code in (401,) and not code:
                code = "DH-901"
            base = ERRORS.get(code, f"Dhan error {e.code}.")
            raise DhanError(f"{base} {('Dhan says: ' + msg) if msg else text[:200]}".strip(),
                            code=code, status=e.code, raw=text[:1000]) from None
        except (urllib.error.URLError, TimeoutError, OSError) as e:
            raise DhanError(f"Can't reach Dhan: {getattr(e, 'reason', e)}") from None

    # ---- orders -------------------------------------------------------------------------
    def place_order(self, o):
        """o: securityId, exchangeSegment, transactionType, quantity, orderType, productType,
        price, triggerPrice, validity, disclosedQuantity, afterMarketOrder, amoTime, correlationId."""
        body = {
            "transactionType": o["transactionType"], "exchangeSegment": o["exchangeSegment"],
            "productType": o["productType"], "orderType": o["orderType"],
            "validity": o.get("validity") or "DAY", "securityId": str(o["securityId"]),
            "quantity": int(o["quantity"]), "disclosedQuantity": int(o.get("disclosedQuantity") or 0),
            "price": float(o.get("price") or 0), "triggerPrice": float(o.get("triggerPrice") or 0),
            "afterMarketOrder": bool(o.get("afterMarketOrder")),
        }
        if body["afterMarketOrder"]:
            body["amoTime"] = o.get("amoTime") or "OPEN"
        if o.get("correlationId"):
            body["correlationId"] = o["correlationId"]
        return self.call("POST", "/orders/slicing" if o.get("slice") else "/orders", body)

    def place_super_order(self, o):
        body = {
            "transactionType": o["transactionType"], "exchangeSegment": o["exchangeSegment"],
            "productType": o["productType"], "orderType": o["orderType"],
            "securityId": str(o["securityId"]), "quantity": int(o["quantity"]),
            "price": float(o["price"]), "targetPrice": float(o.get("targetPrice") or 0),
            "stopLossPrice": float(o.get("stopLossPrice") or 0),
            "trailingJump": float(o.get("trailingJump") or 0),
        }
        if o.get("correlationId"):
            body["correlationId"] = o["correlationId"]
        return self.call("POST", "/super/orders", body)

    def modify_order(self, order_id, m):
        body = {"orderId": str(order_id), "orderType": m["orderType"], "quantity": int(m["quantity"]),
                "price": float(m.get("price") or 0), "disclosedQuantity": int(m.get("disclosedQuantity") or 0),
                "triggerPrice": float(m.get("triggerPrice") or 0), "validity": m.get("validity") or "DAY"}
        if m.get("legName"):
            body["legName"] = m["legName"]
        return self.call("PUT", f"/orders/{order_id}", body)

    def modify_super_leg(self, order_id, leg, m):
        if leg == "TARGET_LEG":
            body = {"orderId": str(order_id), "legName": leg, "targetPrice": float(m["targetPrice"])}
        elif leg == "STOP_LOSS_LEG":
            body = {"orderId": str(order_id), "legName": leg, "stopLossPrice": float(m["stopLossPrice"]),
                    "trailingJump": float(m.get("trailingJump") or 0)}
        else:
            body = {"orderId": str(order_id), "orderType": m["orderType"], "legName": "ENTRY_LEG",
                    "quantity": int(m["quantity"]), "price": float(m.get("price") or 0),
                    "targetPrice": float(m.get("targetPrice") or 0), "stopLossPrice": float(m.get("stopLossPrice") or 0),
                    "trailingJump": float(m.get("trailingJump") or 0)}
        return self.call("PUT", f"/super/orders/{order_id}", body)

    def cancel_order(self, order_id):
        return self.call("DELETE", f"/orders/{order_id}")

    def cancel_super(self, order_id, leg="ENTRY_LEG"):
        return self.call("DELETE", f"/super/orders/{order_id}/{leg}")

    def orders(self):
        return _list(self.call("GET", "/orders"))

    def super_orders(self):
        return _list(self.call("GET", "/super/orders"))

    def trades_today(self):
        return _list(self.call("GET", "/trades"))

    def trade_history(self, from_date, to_date, page=0):
        return _list(self.call("GET", f"/trades/{from_date}/{to_date}/{page}"))

    # ---- portfolio & funds ----------------------------------------------------------------
    def positions(self):
        return _list(self.call("GET", "/positions"))

    def holdings(self):
        try:
            return _list(self.call("GET", "/holdings"))
        except DhanError as e:                        # Dhan answers an error when there are no holdings
            if e.code in ("DH-907", "DH-905") or "no holding" in str(e).lower():
                return []
            raise

    def exit_all(self):
        return self.call("DELETE", "/positions")

    def convert_position(self, c):
        return self.call("POST", "/positions/convert", {
            "fromProductType": c["fromProductType"], "exchangeSegment": c["exchangeSegment"],
            "positionType": c["positionType"], "securityId": str(c["securityId"]),
            "convertQty": int(c["convertQty"]), "toProductType": c["toProductType"]})

    def funds(self):
        return self.call("GET", "/fundlimit")

    def margin(self, o):
        return self.call("POST", "/margincalculator", {
            "securityId": str(o["securityId"]), "exchangeSegment": o["exchangeSegment"],
            "transactionType": o["transactionType"], "quantity": int(o["quantity"]),
            "productType": o["productType"], "price": float(o.get("price") or 0),
            "triggerPrice": float(o.get("triggerPrice") or 0)})

    def kill_switch(self, activate=True):
        return self.call("POST", f"/killswitch?killSwitchStatus={'ACTIVATE' if activate else 'DEACTIVATE'}", {})

    def kill_switch_status(self):
        return self.call("GET", "/killswitch")

    def renew(self):
        d = self.call("GET", "/RenewToken")
        return _find_token(d)

    # ---- market data ----------------------------------------------------------------------
    def quote(self, by_seg, kind="quote"):
        """by_seg: {"NSE_EQ": [ids], ...} -> {(seg, id): data}. kind: ltp, ohlc or quote."""
        body = {seg: [int(i) for i in ids] for seg, ids in by_seg.items() if ids}
        if not body:
            return {}
        d = self.call("POST", f"/marketfeed/{kind}", body)
        out = {}
        for seg, rows in ((d or {}).get("data") or {}).items():
            if isinstance(rows, dict):
                for sid, q in rows.items():
                    out[(seg, str(sid))] = q
        return out

    def expiries(self, under_id, under_seg):
        d = self.call("POST", "/optionchain/expirylist", {"UnderlyingScrip": int(under_id), "UnderlyingSeg": under_seg})
        return [str(x)[:10] for x in ((d or {}).get("data") or [])]

    def option_chain(self, under_id, under_seg, expiry):
        d = self.call("POST", "/optionchain", {"UnderlyingScrip": int(under_id), "UnderlyingSeg": under_seg,
                                               "Expiry": expiry})
        return (d or {}).get("data") or {}

    def intraday(self, sid, seg, inst, interval, from_dt, to_dt):
        return self.call("POST", "/charts/intraday", {
            "securityId": str(sid), "exchangeSegment": seg, "instrument": inst, "interval": str(interval),
            "oi": False, "fromDate": from_dt, "toDate": to_dt})

    def daily(self, sid, seg, inst, from_d, to_d):
        return self.call("POST", "/charts/historical", {
            "securityId": str(sid), "exchangeSegment": seg, "instrument": inst, "expiryCode": 0,
            "oi": False, "fromDate": from_d, "toDate": to_d})


def _list(d):
    if isinstance(d, list):
        return d
    if isinstance(d, dict):
        for k in ("data", "orders", "trades"):
            if isinstance(d.get(k), list):
                return d[k]
    return []


def _find_token(d):
    if isinstance(d, dict):
        for k in ("accessToken", "access_token", "token"):
            if isinstance(d.get(k), str) and len(d[k]) > 20:
                return d[k]
        for v in d.values():
            t = _find_token(v)
            if t:
                return t
    return None
