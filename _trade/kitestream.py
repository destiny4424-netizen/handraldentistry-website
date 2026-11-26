"""Zerodha's live market feed (Kite Connect WebSocket ticker), in place of Dhan's (stream.py).

    wss://ws.kite.trade?api_key=…&access_token=…

Subscribe with JSON {"a": "subscribe", "v": [tokens]} and {"a": "mode", "v": ["quote", [tokens]]}; unsubscribe with
{"a": "unsubscribe", "v": [tokens]}. Prices come back big-endian: 2 bytes = number of packets, then for each one 2
bytes of length and the packet. Every packet starts with the instrument token (4 bytes) and the last price, in paise
(currency: CDS ÷ 10,000,000, BCD ÷ 10,000). 8 bytes = LTP mode, 28/32 = an index, 44 = quote, 184 = full. A
1-byte message is Kite's heartbeat; text messages (order updates, errors) are skipped except errors.

Everything else (falling back to asking every second, the cross-check with REST quotes) is stream.Stream's.
"""
import json
import struct
import time
from urllib.parse import urlencode

import stream
import wsclient

SEG_DIV = {3: 10_000_000.0, 6: 10_000.0}                # token & 0xff: 3 CDS, 6 BCD


def parse(buf, key_of):
    """Kite's binary message -> list of (key, fields). key_of(token) gives the app's key, or None."""
    out = []
    if len(buf) < 2:
        return out                                     # heartbeat
    n = struct.unpack_from(">H", buf, 0)[0]
    i = 2
    for _ in range(n):
        if i + 2 > len(buf):
            break
        size = struct.unpack_from(">H", buf, i)[0]
        p = i + 2
        i = p + size
        if size < 8 or i > len(buf):
            break
        token, ltp = struct.unpack_from(">Ii", buf, p)
        key = key_of(str(token))
        if not key:
            continue
        div = SEG_DIV.get(token & 0xFF, 100.0)
        f = {"ltp": round(ltp / div, 4 if div > 100 else 2)}
        if size in (28, 32):                           # index: ltp, high, low, open, close, change
            hi, lo, op, cl = struct.unpack_from(">iiii", buf, p + 8)
            f.update(high=hi / div, low=lo / div, open=op / div, prev=cl / div)
        elif size >= 44:                               # quote / full
            _lq, avg, vol, _bq, _sq, op, hi, lo, cl = struct.unpack_from(">iiiiiiiii", buf, p + 8)
            f.update(avg=avg / div, vol=vol, open=op / div, high=hi / div, low=lo / div, prev=cl / div)
            if size >= 184:
                f["oi"] = struct.unpack_from(">i", buf, p + 48)[0]
        out.append((key, f))
    return out


class KiteStream(stream.Stream):
    """creds_fn() -> (api_key, access_token) or None. key_of(token) -> app key."""

    def __init__(self, url, creds_fn, on_quote, key_of, enabled=True):
        super().__init__(url, creds_fn, on_quote, enabled=enabled)
        self.key_of = key_of

    def _connect(self, creds):
        api_key, token = creds
        ws = wsclient.Client(f"{self.url}?{urlencode({'api_key': api_key, 'access_token': token})}")
        ws.settimeout(30)                              # Kite sends a heartbeat every second
        self.ws, self._creds, self.connected = ws, creds, True
        with self.lock:
            self.subbed = set()
        self.error = ""
        self._sync_subs()

    def _read(self):
        while self.connected:
            if self.creds_fn() != self._creds:
                return
            op, data = self.ws.recv()
            if op == wsclient.TEXT:
                try:
                    m = json.loads(data)
                except ValueError:
                    continue
                if m.get("type") == "error":
                    self.error = f"Zerodha's live stream: {m.get('data')}"
                continue
            if op != wsclient.BINARY:
                continue
            for key, fields in parse(data, self.key_of):
                if not (fields.get("ltp") and fields["ltp"] > 0):
                    fields.pop("ltp", None)
                self.last_packet = time.time()
                self.packets += 1
                self.on_quote(key, fields)

    def _sync_subs(self):
        with self.lock:
            add, rem = self.want - self.subbed, self.subbed - self.want
            self.subbed = set(self.want)
        try:
            tok = lambda keys: sorted(int(k.split(":")[1]) for k in keys if k.split(":")[1].isdigit())  # noqa: E731
            if rem:
                self.ws.send_text(json.dumps({"a": "unsubscribe", "v": tok(rem)}))
            if add:
                t = tok(add)
                self.ws.send_text(json.dumps({"a": "subscribe", "v": t}))
                self.ws.send_text(json.dumps({"a": "mode", "v": ["quote", t]}))
        except Exception as e:
            self.error = f"Live stream: {e}"
            self._drop()


