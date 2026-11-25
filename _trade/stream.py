"""Dhan's live market feed (v2 WebSocket): Dhan pushes prices to the server about every second, for every
instrument on screen, instead of the app asking once a second.

    wss://api-feed.dhan.co?version=2&token=…&clientId=…&authType=2

Subscribe with JSON {"RequestCode": 15 ticker / 17 quote / 21 full, "InstrumentCount", "InstrumentList"} (100 a
message; 16 / 18 / 22 unsubscribe). Prices come back as little-endian binary packets with an 8-byte header:
response code (u8), message length (i16), exchange segment (u8), security id (i32).

The price loop in trade.py keeps asking Dhan's REST quotes every 10 seconds while the stream is up: that adds the
fields the stream doesn't carry (circuit limits, depth) and cross-checks the stream. If the two disagree, or the
stream goes quiet, the app falls back to asking every second, as before.
"""
import json
import math
import struct
import threading
import time
from urllib.parse import urlencode

import wsclient

SEG_CODE = {"IDX_I": 0, "NSE_EQ": 1, "NSE_FNO": 2, "NSE_CURRENCY": 3, "BSE_EQ": 4, "MCX_COMM": 5,
            "BSE_CURRENCY": 7, "BSE_FNO": 8}
CODE_SEG = {v: k for k, v in SEG_CODE.items()}
TICKER, QUOTE, OI, PREV_CLOSE, STATUS, FULL, DISCONNECT = 2, 4, 5, 6, 7, 8, 50
SIZES = {TICKER: 16, QUOTE: 50, OI: 12, PREV_CLOSE: 16, STATUS: 8, FULL: 162, DISCONNECT: 10}
SUB = {"IDX_I": (17, 18)}                              # indices: quote mode; everything else: full mode
SUB_DEFAULT = (21, 22)
DISCONNECT_REASONS = {805: "too many connections (Dhan allows 5)", 806: "the Data API subscription isn't active",
                      807: "the access token has expired", 808: "the client ID is wrong",
                      809: "the access token is wrong", 810: "the client ID is wrong"}


def _f(x):
    """A price from a 4-byte float, back to paise (1293.6500244 -> 1293.65); None if it isn't a number."""
    return round(x, 2) if isinstance(x, float) and math.isfinite(x) else None


def parse(buf):
    """Dhan's binary packets -> list of (key, fields). Unknown packets are skipped."""
    out, i = [], 0
    while i + 8 <= len(buf):
        code, _length, seg, sid = struct.unpack_from("<BhBi", buf, i)
        size = SIZES.get(code)
        if not size or i + size > len(buf):
            break
        key = f"{CODE_SEG.get(seg, seg)}:{sid}"
        p = i
        if code == TICKER:
            ltp, _ltt = struct.unpack_from("<fi", buf, p + 8)
            out.append((key, {"ltp": _f(ltp)}))
        elif code == QUOTE:
            ltp, _ltq, _ltt, atp, vol, tsq, tbq, o, _c, h, lo = struct.unpack_from("<fhifiiiffff", buf, p + 8)
            out.append((key, {"ltp": _f(ltp), "avg": _f(atp), "vol": vol, "open": _f(o), "high": _f(h), "low": _f(lo),
                              "tbq": tbq, "tsq": tsq}))
        elif code == FULL:
            ltp, _ltq, _ltt, atp, vol, tsq, tbq, oi, _oih, _oil, o, _c, h, lo = struct.unpack_from("<fhifiiiiiiffff", buf, p + 8)
            bids, asks = [], []
            for k in range(5):
                bq, aq, bo, ao, bp, ap = struct.unpack_from("<iihhff", buf, p + 62 + 20 * k)
                bids.append([round(bp, 2), bq, bo])
                asks.append([round(ap, 2), aq, ao])
            out.append((key, {"ltp": _f(ltp), "avg": _f(atp), "vol": vol, "oi": oi, "open": _f(o), "high": _f(h),
                              "low": _f(lo), "bids": bids, "asks": asks}))
        elif code == OI:
            out.append((key, {"oi": struct.unpack_from("<i", buf, p + 8)[0]}))
        elif code == PREV_CLOSE:
            prev, _poi = struct.unpack_from("<fi", buf, p + 8)
            out.append((key, {"prev": _f(prev)}))
        elif code == DISCONNECT:
            reason = struct.unpack_from("<h", buf, p + 8)[0]
            out.append(("_disconnect", {"code": reason}))
        i += size
    return out


class Stream:
    def __init__(self, url, creds_fn, on_quote, enabled=True):
        self.url, self.creds_fn, self.on_quote = url, creds_fn, on_quote
        self.enabled = enabled
        self.want = set()                              # keys the screens want
        self.subbed = set()                            # keys subscribed on the current connection
        self.ws = None
        self.connected = False
        self.last_packet = 0.0
        self.error = ""
        self.off_until = 0.0                           # after a mismatch: no stream until then
        self.off_reason = ""
        self.packets = 0
        self.lock = threading.Lock()
        self._creds = None

    # -- state the price loop reads
    def healthy(self):
        """Connected, and prices arriving (or nothing on screen to send)."""
        return (self.connected and time.time() >= self.off_until
                and (not self.subbed or time.time() - self.last_packet < 8))

    def mode(self):
        if not self.enabled:
            return "polling", "Live stream switched off"
        if time.time() < self.off_until:
            return "polling", self.off_reason
        if self.healthy():
            return "stream", ""
        if self.connected:
            return "polling", "Live stream connected, waiting for prices (quiet outside market hours)"
        return "polling", self.error or "Live stream not connected yet"

    def set_keys(self, keys):
        with self.lock:
            self.want = set(keys)
        if self.connected:
            self._sync_subs()

    def distrust(self, reason, minutes=60):
        """Prices from the stream didn't match Dhan's own quotes: stop using it for a while."""
        self.off_until = time.time() + minutes * 60
        self.off_reason = reason
        self._drop()

    # -- connection
    def run(self):
        backoff = 5
        while True:
            if not self.enabled or time.time() < self.off_until:
                time.sleep(5)
                continue
            creds = self.creds_fn()
            if not creds:
                time.sleep(5)
                continue
            try:
                self._connect(creds)
                backoff = 5
                self._read()
            except Exception as e:                     # network, handshake, Dhan closing: reconnect later
                self.error = f"Live stream: {e}"
            self._drop()
            time.sleep(backoff)
            backoff = min(backoff * 2, 120)

    def _connect(self, creds):
        client_id, token = creds
        q = urlencode({"version": 2, "token": token, "clientId": client_id, "authType": 2})
        ws = wsclient.Client(f"{self.url}?{q}")
        ws.settimeout(40)                              # Dhan pings every 10 s; nothing for 40 s = dead
        self.ws, self._creds, self.connected = ws, creds, True
        with self.lock:
            self.subbed = set()
        self.error = ""
        self._sync_subs()

    def _read(self):
        while self.connected:
            if self.creds_fn() != self._creds:         # token renewed or changed: connect again with the new one
                return
            op, data = self.ws.recv()
            if op != wsclient.BINARY:
                continue
            for key, fields in parse(data):
                if key == "_disconnect":
                    c = fields["code"]
                    self.error = f"Dhan closed the live stream: {DISCONNECT_REASONS.get(c, f'code {c}')}"
                    return
                if "ltp" in fields and not (fields["ltp"] and fields["ltp"] > 0):
                    fields.pop("ltp")
                self.last_packet = time.time()
                self.packets += 1
                self.on_quote(key, fields)

    def _drop(self):
        ws, self.ws, self.connected = self.ws, None, False
        if ws:
            ws.close()

    def _sync_subs(self):
        with self.lock:
            add, rem = self.want - self.subbed, self.subbed - self.want
            self.subbed = set(self.want)
        try:
            for keys, i in ((rem, 1), (add, 0)):
                by = {}
                for k in keys:
                    seg = k.split(":")[0]
                    by.setdefault(SUB.get(seg, SUB_DEFAULT)[i], []).append(k)
                for code, ks in by.items():
                    ks = sorted(ks)
                    for n in range(0, len(ks), 100):
                        part = ks[n:n + 100]
                        self.ws.send_text(json.dumps({"RequestCode": code, "InstrumentCount": len(part), "InstrumentList": [
                            {"ExchangeSegment": k.split(":")[0], "SecurityId": k.split(":")[1]} for k in part]}))
        except Exception as e:
            self.error = f"Live stream: {e}"
            self._drop()
