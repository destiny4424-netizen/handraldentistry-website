"""Expiry CAS: the closing-auction trade on NIFTY / SENSEX expiry days.

On an expiry day the cash market stops at 3:15 PM and a Closing Auction Session (CAS) sets each stock's
closing price; the index's official close, which settles the expiring options, comes out of it. The
expiring options keep trading meanwhile. Dhan's CAS Live Feed streams the auction live, including an
"indicative index packet" with the index's indicative close.

So, during the auction, every expiring option has a known settlement value at the indicative close:
    call: max(0, close - strike)      put: max(0, strike - close)
An option you can BUY below that value (after charges) is cheap; one you can SELL above it is dear
(for instance an out-of-the-money option still bid, which settles at zero). Until the auction ends the
indicative close can still move, so a safety band is applied; once it stops moving at the end of the
auction the band is dropped.

From 7 December 2026 (SEBI's revised CAS rules, per NSE's 6 Nov 2026 circulars as reported), expiring
contracts stop trading at 3:30 PM and settle on the last-30-minute average instead of the auction
close, so this trade no longer applies; the scanner then shows that and stops.

The feed (DhanHQ "CAS Live Feed", as documented):
  wss://api-feed.dhan.co?token=..&clientId=..&authType=2 ; subscribe {"RequestCode": 23, ...}
  CAS packet (128 bytes): header [code u8][len i16][segment u8][security id i32], IEP f32, buy qty i32,
    sell qty i32, 5 x depth (bid qty i32, ask qty i32, bid orders i16, ask orders i16, bid f32, ask f32),
    imbalance i32, reference price f32.
  Indicative index packet (33 bytes): [len i16][code u8][segment u8][index name 21 bytes][close f64].
  Disconnect packet: header with code 50, then reason i16.
Little-endian, like the rest of Dhan's feed.
"""
import base64
import json
import os
import socket
import ssl
import struct
import threading
import time
from urllib.parse import urlparse

FEED_URL = "wss://api-feed.dhan.co"
LAST_DAY = "2026-12-06"                                # the auction stops settling expiries from 7 Dec 2026
STT_EXERCISE = 0.00125                                 # STT on the settlement value of exercised options
STT_SELL = 0.001                                       # STT on the premium when you sell
CHARGES = 0.0006                                       # exchange + SEBI + stamp + GST, of the premium (approx.)
BROKERAGE = 20.0                                       # ₹ an order


# ── Minimal WebSocket client (standard library only) ─────────────────────────────────────────
class WS:
    def __init__(self, url, timeout=10):
        u = urlparse(url)
        port = u.port or (443 if u.scheme == "wss" else 80)
        raw = socket.create_connection((u.hostname, port), timeout=timeout)
        self.sock = ssl.create_default_context().wrap_socket(raw, server_hostname=u.hostname) if u.scheme == "wss" else raw
        key = base64.b64encode(os.urandom(16)).decode()
        path = (u.path or "/") + (("?" + u.query) if u.query else "")
        self.sock.sendall((f"GET {path} HTTP/1.1\r\nHost: {u.hostname}\r\nUpgrade: websocket\r\nConnection: Upgrade\r\n"
                           f"Sec-WebSocket-Key: {key}\r\nSec-WebSocket-Version: 13\r\nUser-Agent: meridian-scanner\r\n\r\n").encode())
        head = b""
        while b"\r\n\r\n" not in head:
            chunk = self.sock.recv(4096)
            if not chunk:
                raise ConnectionError("feed closed during the handshake")
            head += chunk
        head, self.buf = head.split(b"\r\n\r\n", 1)
        status = head.split(b"\r\n", 1)[0].decode(errors="replace")
        if " 101 " not in status + " ":
            raise ConnectionError(f"feed refused the connection: {status}")
        self.lock = threading.Lock()

    def _send(self, opcode, payload):
        mask = os.urandom(4)
        n = len(payload)
        head = bytes([0x80 | opcode])
        head += bytes([0x80 | n]) if n < 126 else bytes([0x80 | 126]) + struct.pack("!H", n) if n < 65536 \
            else bytes([0x80 | 127]) + struct.pack("!Q", n)
        body = bytes(b ^ mask[i % 4] for i, b in enumerate(payload))
        with self.lock:
            self.sock.sendall(head + mask + body)

    def send_json(self, obj):
        self._send(0x1, json.dumps(obj).encode())

    def _read(self, n):
        while len(self.buf) < n:
            chunk = self.sock.recv(65536)
            if not chunk:
                raise ConnectionError("feed closed")
            self.buf += chunk
        out, self.buf = self.buf[:n], self.buf[n:]
        return out

    def recv(self):
        """The next data message (bytes or str); answers pings; None on close."""
        parts, first_op = [], None
        while True:
            b0, b1 = self._read(2)
            op, fin = b0 & 0x0F, b0 & 0x80
            n = b1 & 0x7F
            if n == 126:
                n = struct.unpack("!H", self._read(2))[0]
            elif n == 127:
                n = struct.unpack("!Q", self._read(8))[0]
            mask = self._read(4) if b1 & 0x80 else None
            data = self._read(n)
            if mask:
                data = bytes(b ^ mask[i % 4] for i, b in enumerate(data))
            if op == 0x9:                              # ping -> pong (keeps the feed open)
                self._send(0xA, data)
                continue
            if op == 0xA:
                continue
            if op == 0x8:
                return None
            if op in (0x1, 0x2):
                first_op = op
            parts.append(data)
            if fin:
                msg = b"".join(parts)
                return msg.decode(errors="replace") if first_op == 0x1 else msg

    def close(self):
        try:
            self._send(0x8, b"")
        except OSError:
            pass
        try:
            self.sock.close()
        except OSError:
            pass


# ── Packets ───────────────────────────────────────────────────────────────────────────────
def parse(msg):
    """Binary feed message -> list of packets: {"type": "cas"|"index"|"disconnect", ...}."""
    out, i, n = [], 0, len(msg)
    while i < n:
        rest = n - i
        if rest >= 128 and struct.unpack_from("<h", msg, i + 1)[0] in (128, 120):
            code, _, seg, sid = struct.unpack_from("<BhBi", msg, i)
            iep, bq, sq = struct.unpack_from("<fii", msg, i + 8)
            depth = [dict(zip(("bid_qty", "ask_qty", "bid_orders", "ask_orders", "bid", "ask"),
                              struct.unpack_from("<iihhff", msg, i + 20 + 20 * k))) for k in range(5)]
            imb, ref = struct.unpack_from("<if", msg, i + 120)
            out.append({"type": "cas", "code": code, "segment": seg, "security_id": sid, "iep": round(iep, 2),
                        "buy_qty": bq, "sell_qty": sq, "depth": depth, "imbalance": imb, "ref": round(ref, 2)})
            i += 128
        elif rest >= 33 and struct.unpack_from("<h", msg, i)[0] in (33, 29):
            _, code, seg = struct.unpack_from("<hBB", msg, i)
            name = msg[i + 4:i + 25].split(b"\x00")[0].decode(errors="replace").strip()
            close = struct.unpack_from("<d", msg, i + 25)[0]
            out.append({"type": "index", "code": code, "segment": seg, "name": name, "close": round(close, 2)})
            i += 33
        elif rest >= 10 and msg[i] == 50:
            reason = struct.unpack_from("<h", msg, i + 8)[0]
            out.append({"type": "disconnect", "reason": reason})
            i += 10
        else:
            ln = struct.unpack_from("<h", msg, i + 1)[0] if rest >= 3 else 0
            if 8 <= ln <= rest:                        # another feed packet: skip it whole
                i += ln
            else:
                break
    return out


def norm(name):
    return "".join(ch for ch in (name or "").upper() if ch.isalnum())


def index_key(name):
    k = norm(name)
    if k in ("NIFTY", "NIFTY50"):
        return "NIFTY"
    if k in ("SENSEX", "BSESENSEX", "SPBSESENSEX"):
        return "SENSEX"
    return None


# ── The trade ─────────────────────────────────────────────────────────────────────────────
def settle_value(kind, strike, close):
    return max(0.0, close - strike) if kind == "CE" else max(0.0, strike - close)


def opportunities(chain, close, band, lot, risk_amt, final=False, min_edge=1.0):
    """Every strike of the expiring chain against the settlement value at the indicative close.
    band: how far the close may still move (0 once the auction has ended): the worst case is used.
    Returns rows sorted by edge; edge = ₹ a unit after charges, per lot too."""
    rows = []
    for k, row in ((chain or {}).get("oc") or {}).items():
        try:
            strike = float(k)
        except ValueError:
            continue
        for kind in ("CE", "PE"):
            o = (row or {}).get(kind.lower()) or {}
            bid, ask, ltp = o.get("top_bid_price") or 0, o.get("top_ask_price") or 0, o.get("last_price") or 0
            if not (bid or ask):
                continue
            fair = settle_value(kind, strike, close)
            worst_low = settle_value(kind, strike, close - band if kind == "CE" else close + band)
            worst_high = settle_value(kind, strike, close + band if kind == "CE" else close - band)
            out = {"strike": strike, "kind": kind, "bid": round(bid, 2), "ask": round(ask, 2), "ltp": round(ltp, 2),
                   "settle": round(fair, 2), "oi": o.get("oi"), "volume": o.get("volume")}
            # Buy now, hold to settlement: you get the settlement value, pay the ask, charges and STT on exercise.
            if ask:
                cost = ask * CHARGES + worst_low * STT_EXERCISE + BROKERAGE / max(lot, 1)
                e = worst_low - ask - cost
                if e >= min_edge:
                    # lots your risk rule allows: the loss if the close still moves 3 bands against you
                    # (after the auction: only execution slippage, taken as 5% of the premium)
                    far = settle_value(kind, strike, close - 3 * band if kind == "CE" else close + 3 * band)
                    loss = max(ask - far, ask * 0.05, 0.05)
                    rows.append(dict(out, side="BUY", edge=round(e, 2), edge_lot=round(e * lot),
                                     max_lots=int(risk_amt // (loss * lot)) if lot else 0))
            # Sell now (your long, or a short to settlement): you get the bid, pay STT and charges, owe the settlement value.
            if bid:
                cost = bid * (STT_SELL + CHARGES) + BROKERAGE / max(lot, 1)
                e = bid - worst_high - cost
                if e >= min_edge:
                    rows.append(dict(out, side="SELL", edge=round(e, 2), edge_lot=round(e * lot),
                                     note="settles at 0: sell what you hold" if worst_high == 0 else "priced above settlement"))
    rows.sort(key=lambda r: -r["edge_lot"])
    return rows


def ladder(chain, close, width=8):
    """The strikes around the close, both sides, for the table."""
    oc = (chain or {}).get("oc") or {}
    ks = sorted(float(k) for k in oc if _num(k))
    if not ks:
        return []
    near = sorted(ks, key=lambda k: abs(k - close))[:width * 2]
    out = []
    for k in sorted(near):
        row = oc.get(next(x for x in oc if _num(x) and abs(float(x) - k) < 1e-6)) or {}
        r = {"strike": k}
        for kind in ("CE", "PE"):
            o = row.get(kind.lower()) or {}
            r[kind] = {"bid": round(o.get("top_bid_price") or 0, 2), "ask": round(o.get("top_ask_price") or 0, 2),
                       "settle": round(settle_value(kind, k, close), 2)}
        out.append(r)
    return out


def _num(x):
    try:
        float(x)
        return True
    except ValueError:
        return False


def band_for(close, history, final):
    """How far the indicative close may still move: 0 after the auction; else the larger of 0.04% and the
    range of the last minute's updates."""
    if final or not close:
        return 0.0
    recent = [c for t, c in history if t >= time.time() - 60]
    rng = (max(recent) - min(recent)) if len(recent) > 1 else 0
    return round(max(close * 0.0004, rng), 2)


# ── Demo ──────────────────────────────────────────────────────────────────────────────────
def demo_packets(close, t):
    """A made-up index packet and one stock packet, in the documented binary layout."""
    name = b"Nifty 50".ljust(21, b"\x00")
    idx = struct.pack("<hBB", 33, 51, 0) + name + struct.pack("<d", close)
    depth = b"".join(struct.pack("<iihhff", 1000 + k, 900 + k, 5, 4, 1500.0 - k, 1500.5 + k) for k in range(5))
    stock = struct.pack("<BhBi", 52, 128, 1, 1333) + struct.pack("<fii", 1500.25, 80000, 76000) + depth + struct.pack("<if", 4000, 1499.8)
    return idx + stock
