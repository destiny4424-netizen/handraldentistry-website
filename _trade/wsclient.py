"""A small WebSocket (RFC 6455) client, and the few server-side pieces the pretend Dhan needs, with only the
standard library. Enough for Dhan's live market feed: text frames out, binary frames in, ping/pong, close."""
import base64
import hashlib
import os
import socket
import ssl
import struct
import threading
from urllib.parse import urlparse

GUID = "258EAFA5-E914-47DA-95CA-C5AB0DC85B11"
TEXT, BINARY, CLOSE, PING, PONG = 0x1, 0x2, 0x8, 0x9, 0xA


class WSError(Exception):
    pass


def accept_key(key):
    return base64.b64encode(hashlib.sha1((key + GUID).encode()).digest()).decode()


def frame(opcode, payload, mask):
    """One final frame. A client must mask what it sends; a server must not."""
    head = bytes([0x80 | opcode])
    n = len(payload)
    m = 0x80 if mask else 0
    if n < 126:
        head += bytes([m | n])
    elif n < 65536:
        head += bytes([m | 126]) + struct.pack(">H", n)
    else:
        head += bytes([m | 127]) + struct.pack(">Q", n)
    if not mask:
        return head + payload
    key = os.urandom(4)
    return head + key + bytes(b ^ key[i % 4] for i, b in enumerate(payload))


def _read_exact(sock, n):
    buf = b""
    while len(buf) < n:
        chunk = sock.recv(n - len(buf))
        if not chunk:
            raise WSError("Connection closed")
        buf += chunk
    return buf


def read_frame(sock):
    """(fin, opcode, payload) of the next frame, unmasked."""
    b0, b1 = _read_exact(sock, 2)
    fin, opcode = bool(b0 & 0x80), b0 & 0x0F
    n = b1 & 0x7F
    if n == 126:
        n = struct.unpack(">H", _read_exact(sock, 2))[0]
    elif n == 127:
        n = struct.unpack(">Q", _read_exact(sock, 8))[0]
    key = _read_exact(sock, 4) if b1 & 0x80 else None
    data = _read_exact(sock, n) if n else b""
    if key:
        data = bytes(b ^ key[i % 4] for i, b in enumerate(data))
    return fin, opcode, data


class Client:
    def __init__(self, url, timeout=15):
        u = urlparse(url)
        if u.scheme not in ("ws", "wss"):
            raise WSError(f"Not a WebSocket address: {u.scheme}")
        host, port = u.hostname, u.port or (443 if u.scheme == "wss" else 80)
        path = (u.path or "/") + (("?" + u.query) if u.query else "")
        raw = socket.create_connection((host, port), timeout=timeout)
        self.sock = ssl.create_default_context().wrap_socket(raw, server_hostname=host) if u.scheme == "wss" else raw
        self._send_lock = threading.Lock()
        key = base64.b64encode(os.urandom(16)).decode()
        req = (f"GET {path} HTTP/1.1\r\nHost: {host}\r\nUpgrade: websocket\r\nConnection: Upgrade\r\n"
               f"Sec-WebSocket-Key: {key}\r\nSec-WebSocket-Version: 13\r\nUser-Agent: handral-desk\r\n\r\n")
        self.sock.sendall(req.encode())
        head = b""
        while b"\r\n\r\n" not in head:
            chunk = self.sock.recv(1024)
            if not chunk:
                raise WSError("Closed during the handshake")
            head += chunk
            if len(head) > 16384:
                raise WSError("Handshake answer too long")
        head, _, self._extra = head.partition(b"\r\n\r\n")
        lines = head.decode("latin-1").split("\r\n")
        if " 101 " not in lines[0] + " ":
            raise WSError(f"Handshake refused: {lines[0]}")
        hdr = {k.strip().lower(): v.strip() for k, _, v in (ln.partition(":") for ln in lines[1:])}
        if hdr.get("sec-websocket-accept") != accept_key(key):
            raise WSError("Bad handshake answer")
        if self._extra:                                # bytes that came with the handshake belong to the first frame
            self.sock = _Prefixed(self.sock, self._extra)

    def settimeout(self, t):
        self.sock.settimeout(t)

    def send_text(self, s):
        with self._send_lock:
            self.sock.sendall(frame(TEXT, s.encode(), True))

    def recv(self):
        """The next data message (opcode, payload). Pings are answered here; a close raises WSError."""
        parts, op = [], None
        while True:
            fin, opcode, data = read_frame(self.sock)
            if opcode == PING:
                with self._send_lock:
                    self.sock.sendall(frame(PONG, data, True))
                continue
            if opcode == PONG:
                continue
            if opcode == CLOSE:
                code = struct.unpack(">H", data[:2])[0] if len(data) >= 2 else 1005
                raise WSError(f"Closed by the server ({code}{': ' + data[2:].decode('utf-8', 'replace') if len(data) > 2 else ''})")
            if opcode != 0:
                op = opcode
            parts.append(data)
            if fin:
                return op, b"".join(parts)

    def close(self):
        try:
            with self._send_lock:
                self.sock.sendall(frame(CLOSE, struct.pack(">H", 1000), True))
        except OSError:
            pass
        try:
            self.sock.close()
        except OSError:
            pass


class _Prefixed:
    """A socket with some bytes already read (they came in with the handshake)."""
    def __init__(self, sock, pre):
        self._s, self._pre = sock, pre

    def recv(self, n):
        if self._pre:
            out, self._pre = self._pre[:n], self._pre[n:]
            return out
        return self._s.recv(n)

    def __getattr__(self, name):
        return getattr(self._s, name)
