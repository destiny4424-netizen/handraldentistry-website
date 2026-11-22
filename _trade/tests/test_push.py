"""Phone notifications and the PIN lock.
    cd _trade && python3 -m unittest discover tests
"""
import base64
import json
import os
import sys
import tempfile
import threading
import time
import unittest
from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
os.environ.setdefault("MT_DATA_DIR", tempfile.mkdtemp())

import webpush                                           # noqa: E402
import alerts                                            # noqa: E402
from cryptography.hazmat.primitives.asymmetric import ec  # noqa: E402
from cryptography.hazmat.primitives import hashes, serialization  # noqa: E402
from cryptography.hazmat.primitives.kdf.hkdf import HKDF  # noqa: E402
from cryptography.hazmat.primitives.ciphers.aead import AESGCM  # noqa: E402
from cryptography.hazmat.primitives.asymmetric.utils import encode_dss_signature  # noqa: E402

IST = timezone(timedelta(hours=5, minutes=30))


class Phone:
    """What a phone's browser does with a push message (RFC 8291, receiving side)."""

    def __init__(self):
        self.key = ec.generate_private_key(ec.SECP256R1())
        self.pub = self.key.public_key().public_bytes(serialization.Encoding.X962, serialization.PublicFormat.UncompressedPoint)
        self.auth = os.urandom(16)
        self.keys = {"p256dh": webpush.b64u(self.pub), "auth": webpush.b64u(self.auth)}

    def decrypt(self, body):
        salt, idlen = body[:16], body[20]
        as_pub = body[21:21 + idlen]
        shared = self.key.exchange(ec.ECDH(), ec.EllipticCurvePublicKey.from_encoded_point(ec.SECP256R1(), as_pub))
        h = lambda s, k, info, n: HKDF(algorithm=hashes.SHA256(), length=n, salt=s, info=info).derive(k)
        ikm = h(self.auth, shared, b"WebPush: info\x00" + self.pub + as_pub, 32)
        cek = h(salt, ikm, b"Content-Encoding: aes128gcm\x00", 16)
        nonce = h(salt, ikm, b"Content-Encoding: nonce\x00", 12)
        plain = AESGCM(cek).decrypt(nonce, body[21 + idlen:], None)
        return plain.rstrip(b"\x00")[:-1]                 # drop the \x02 delimiter


class Crypto(unittest.TestCase):
    def test_round_trip(self):
        ph = Phone()
        msg = json.dumps({"title": "Stop-loss hit", "body": "SBIN 100 @ ₹850.00"}).encode()
        self.assertEqual(ph.decrypt(webpush.encrypt(msg, ph.keys["p256dh"], ph.keys["auth"])), msg)

    def test_vapid_signature(self):
        pem, pub = webpush.new_vapid()
        h = webpush.vapid_header(pem, pub, "https://web.push.apple.com/QGuQyavXutnMH", "https://trade.example.com")
        t = h.split("t=")[1].split(",")[0]
        k = h.split("k=")[1]
        head, claims, sig = t.split(".")
        c = json.loads(webpush.unb64u(claims))
        self.assertEqual(c["aud"], "https://web.push.apple.com")
        self.assertEqual(c["sub"], "https://trade.example.com")
        self.assertGreater(c["exp"], time.time())
        raw = webpush.unb64u(sig)
        der = encode_dss_signature(int.from_bytes(raw[:32], "big"), int.from_bytes(raw[32:], "big"))
        key = ec.EllipticCurvePublicKey.from_encoded_point(ec.SECP256R1(), webpush.unb64u(k))
        key.verify(der, f"{head}.{claims}".encode(), ec.ECDSA(hashes.SHA256()))   # raises if wrong


GOT = []


class FakePushService(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def do_POST(self):
        body = self.rfile.read(int(self.headers.get("Content-Length") or 0))
        GOT.append((self.path, dict(self.headers), body))
        self.send_response(410 if self.path.endswith("/gone") else 201)
        self.end_headers()


class Delivery(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.srv = ThreadingHTTPServer(("127.0.0.1", 0), FakePushService)
        threading.Thread(target=cls.srv.serve_forever, daemon=True).start()
        cls.base = f"http://127.0.0.1:{cls.srv.server_address[1]}"

    def setUp(self):
        GOT.clear()
        self.settings = {}
        self.push = webpush.Push(tempfile.mkdtemp(), lambda: self.settings, lambda **kw: self.settings.update(kw),
                                 "https://trade.example.com")
        self.push.public_key()

    def wait(self, n=1):
        for _ in range(50):
            if len(GOT) >= n:
                return
            time.sleep(0.1)

    def test_delivers_encrypted(self):
        ph = Phone()
        self.assertEqual(self.push.subscribe({"endpoint": self.base + "/sub/1", "keys": ph.keys}, "iPhone"), "")
        self.push.send("Filled: BUY SBIN", "100 @ ₹850.00", url="/#orders")
        self.wait()
        path, headers, body = GOT[0]
        self.assertEqual(headers["Content-Encoding"], "aes128gcm")
        self.assertTrue(headers["Authorization"].startswith("vapid t="))
        m = json.loads(ph.decrypt(body))
        self.assertEqual((m["title"], m["body"], m["url"]), ("Filled: BUY SBIN", "100 @ ₹850.00", "/#orders"))

    def test_gone_phone_is_removed(self):
        self.push.subscribe({"endpoint": self.base + "/sub/gone", "keys": Phone().keys})
        self.push.send("x", "y")
        self.wait()
        time.sleep(0.3)
        self.assertEqual(self.push.subs, [])

    def test_rejects_junk(self):
        self.assertTrue(self.push.subscribe({"endpoint": "javascript:alert(1)", "keys": Phone().keys}))
        self.assertTrue(self.push.subscribe({"endpoint": "https://x.example/1", "keys": {}}))


class FakePush:
    def __init__(self):
        self.sent = []

    def send(self, title, body, tag="", url="/"):
        self.sent.append((title, body))


class Rules(unittest.TestCase):
    def setUp(self):
        self.t = datetime(2026, 11, 23, 10, 0, tzinfo=IST)           # a Monday
        self.p = FakePush()
        self.a = alerts.Alerts(tempfile.mkdtemp(), self.p, lambda: self.t)

    def test_orders_after_start_only(self):
        old = [{"orderId": "1", "orderStatus": "TRADED", "transactionType": "BUY", "quantity": 5, "averageTradedPrice": 100}]
        self.a.tick(orders=old)
        self.assertEqual(self.p.sent, [])                                # already filled before: no flood
        new = old + [{"orderId": "2", "orderStatus": "TRADED", "transactionType": "SELL", "filledQty": 10,
                      "averageTradedPrice": 1517, "legName": "STOP_LOSS_LEG"},
                     {"orderId": "3", "orderStatus": "REJECTED", "transactionType": "BUY", "omsErrorDescription": "RMS: margin"}]
        self.a.tick(orders=new, names={"2": "SILVER100 JAN FUT", "3": "SBIN"})
        self.assertEqual(self.p.sent[0], ("Stop-loss hit: SELL SILVER100 JAN FUT", "10 @ ₹1,517.00"))
        self.assertEqual(self.p.sent[1], ("Rejected: BUY SBIN", "RMS: margin"))
        self.a.tick(orders=new)
        self.assertEqual(len(self.p.sent), 2)                            # each order once

    def test_loss_steps_and_limit(self):
        self.a.tick(pnl=0)
        self.a.tick(pnl=-4000, loss_limit=15000)
        self.assertEqual(self.p.sent, [])
        self.a.tick(pnl=-12000, loss_limit=15000)
        self.assertIn("−₹12,000.00", self.p.sent[0][0])
        self.a.tick(pnl=-32374.99, loss_limit=15000)
        titles = [x[0] for x in self.p.sent]
        self.assertIn("Daily loss limit reached", titles)
        n = len(self.p.sent)
        self.a.tick(pnl=-31000, loss_limit=15000)
        self.assertEqual(len(self.p.sent), n)                            # no repeat for a smaller loss

    def test_routine_and_kinds(self):
        self.a.tick()
        self.t = datetime(2026, 11, 23, 9, 5, tzinfo=IST)
        self.a.tick()
        self.assertEqual(self.p.sent[-1][0], "Plan your day")
        self.a.update_settings({"kinds": ["orders"]})
        self.t = datetime(2026, 11, 23, 15, 50, tzinfo=IST)
        self.a.tick(journal_done=False)
        self.assertEqual(self.p.sent[-1][0], "Plan your day")            # routine switched off: nothing new

    def test_bot_log(self):
        self.a.tick(bot_log=[{"at": "23 Nov 09:10", "msg": "Setup SBIN"}])
        self.a.tick(bot_log=[{"at": "23 Nov 09:20", "msg": "LIVE BOUGHT SBIN 75 @ 872"},
                             {"at": "23 Nov 09:10", "msg": "Setup SBIN"}])
        self.assertEqual(self.p.sent, [("Auto-trader", "LIVE BOUGHT SBIN 75 @ 872")])


class Pin(unittest.TestCase):
    def test_token(self):
        import trade
        trade.SETTINGS.pop("pin_hash", None)
        self.assertTrue(trade.pin_ok(""))                                # no PIN: open
        salt = "00" * 16
        trade.SETTINGS.update(pin_hash=trade.hash_pin("2468", salt), pin_salt=salt)
        try:
            self.assertFalse(trade.pin_ok(""))
            good = trade.pin_token(int(time.time()) + 60)
            self.assertTrue(trade.pin_ok(good))
            self.assertFalse(trade.pin_ok(trade.pin_token(int(time.time()) - 1)))       # expired
            self.assertFalse(trade.pin_ok(good[:-1] + ("0" if good[-1] != "0" else "1")))  # forged
            trade.SETTINGS["pin_hash"] = trade.hash_pin("1357", salt)                    # PIN changed
            self.assertFalse(trade.pin_ok(good))
        finally:
            trade.SETTINGS.pop("pin_hash", None)
            trade.SETTINGS.pop("pin_salt", None)


if __name__ == "__main__":
    unittest.main()
