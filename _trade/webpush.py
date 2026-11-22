"""Web Push: notifications on the phone from the home-screen app, even when it is closed.

Standards: VAPID (RFC 8292) to identify this server, and aes128gcm message encryption (RFC 8291), which
is what Apple (iOS 16.4+, home-screen apps), Google and Mozilla push services accept. Uses the
`cryptography` package (python3-cryptography on Ubuntu; the droplet updater installs it). Without it the
app works as before and the Notifications panel says they aren't available yet.
"""
import base64
import json
import os
import queue
import struct
import threading
import time
import urllib.error
import urllib.parse
import urllib.request


def b64u(b):
    return base64.urlsafe_b64encode(b).rstrip(b"=").decode()


def unb64u(s):
    s = str(s)
    return base64.urlsafe_b64decode(s + "=" * (-len(s) % 4))


def crypto():
    """The cryptography modules, or None if the package isn't installed (checked each time, so
    installing it later works without a restart)."""
    try:
        from cryptography.hazmat.primitives.asymmetric import ec
        from cryptography.hazmat.primitives import hashes, serialization
        from cryptography.hazmat.primitives.kdf.hkdf import HKDF
        from cryptography.hazmat.primitives.ciphers.aead import AESGCM
        from cryptography.hazmat.primitives.asymmetric.utils import decode_dss_signature
        return {"ec": ec, "hashes": hashes, "ser": serialization, "HKDF": HKDF, "AESGCM": AESGCM,
                "dss": decode_dss_signature}
    except ImportError:
        return None


def new_vapid():
    c = crypto()
    key = c["ec"].generate_private_key(c["ec"].SECP256R1())
    pem = key.private_bytes(c["ser"].Encoding.PEM, c["ser"].PrivateFormat.PKCS8, c["ser"].NoEncryption()).decode()
    pub = key.public_key().public_bytes(c["ser"].Encoding.X962, c["ser"].PublicFormat.UncompressedPoint)
    return pem, b64u(pub)


def _hkdf(c, salt, ikm, info, n):
    return c["HKDF"](algorithm=c["hashes"].SHA256(), length=n, salt=salt, info=info).derive(ikm)


def encrypt(payload, p256dh, auth, _test_keys=None):
    """aes128gcm body for one subscription (RFC 8291)."""
    c = crypto()
    ua_pub = unb64u(p256dh)
    auth = unb64u(auth)
    as_key = _test_keys[0] if _test_keys else c["ec"].generate_private_key(c["ec"].SECP256R1())
    salt = _test_keys[1] if _test_keys else os.urandom(16)
    as_pub = as_key.public_key().public_bytes(c["ser"].Encoding.X962, c["ser"].PublicFormat.UncompressedPoint)
    ua_key = c["ec"].EllipticCurvePublicKey.from_encoded_point(c["ec"].SECP256R1(), ua_pub)
    shared = as_key.exchange(c["ec"].ECDH(), ua_key)
    ikm = _hkdf(c, auth, shared, b"WebPush: info\x00" + ua_pub + as_pub, 32)
    cek = _hkdf(c, salt, ikm, b"Content-Encoding: aes128gcm\x00", 16)
    nonce = _hkdf(c, salt, ikm, b"Content-Encoding: nonce\x00", 12)
    body = c["AESGCM"](cek).encrypt(nonce, payload + b"\x02", None)      # \x02 = last (and only) record
    header = salt + struct.pack(">I", 4096) + bytes([len(as_pub)]) + as_pub
    return header + body


def vapid_header(pem, pub_b64, endpoint, subject):
    c = crypto()
    key = c["ser"].load_pem_private_key(pem.encode(), password=None)
    u = urllib.parse.urlsplit(endpoint)
    claims = {"aud": f"{u.scheme}://{u.netloc}", "exp": int(time.time()) + 12 * 3600, "sub": subject}
    signing = b64u(json.dumps({"typ": "JWT", "alg": "ES256"}).encode()) + "." + b64u(json.dumps(claims).encode())
    der = key.sign(signing.encode(), c["ec"].ECDSA(c["hashes"].SHA256()))
    r, s = c["dss"](der)
    jwt = signing + "." + b64u(r.to_bytes(32, "big") + s.to_bytes(32, "big"))
    return f"vapid t={jwt}, k={pub_b64}"


class Push:
    """Keeps the phone subscriptions and sends notifications from a background thread."""

    def __init__(self, data_dir, get_settings, save_settings, subject):
        self.path = os.path.join(data_dir, "push.json")
        self.get, self.put, self.subject = get_settings, save_settings, subject
        self.q = queue.Queue()
        self.log = []
        self.lock = threading.Lock()
        try:
            self.subs = json.load(open(self.path))
        except (OSError, ValueError):
            self.subs = []
        threading.Thread(target=self._worker, daemon=True).start()

    def available(self):
        return crypto() is not None

    def public_key(self):
        if not self.available():
            return None
        s = self.get()
        if not (s.get("vapid_pem") and s.get("vapid_pub")):
            pem, pub = new_vapid()
            self.put(vapid_pem=pem, vapid_pub=pub)
            return pub
        return s["vapid_pub"]

    def _save(self):
        tmp = self.path + ".tmp"
        with open(tmp, "w") as f:
            json.dump(self.subs, f)
        os.chmod(tmp, 0o600)
        os.replace(tmp, self.path)

    def subscribe(self, sub, device=""):
        ep = str((sub or {}).get("endpoint") or "")
        keys = (sub or {}).get("keys") or {}
        if not ep.startswith("https://") and not ep.startswith("http://127.0.0.1"):
            return "That isn't a push subscription."
        if not (keys.get("p256dh") and keys.get("auth")):
            return "The subscription has no keys."
        with self.lock:
            self.subs = [x for x in self.subs if x["endpoint"] != ep]
            self.subs.append({"endpoint": ep, "p256dh": keys["p256dh"], "auth": keys["auth"],
                              "device": str(device)[:60], "since": time.strftime("%d %b %Y")})
            self.subs = self.subs[-10:]
            self._save()
        return ""

    def unsubscribe(self, endpoint):
        with self.lock:
            self.subs = [x for x in self.subs if x["endpoint"] != endpoint]
            self._save()

    def send(self, title, body, tag="", url="/"):
        """Queue one notification to every subscribed phone."""
        if self.subs and self.available():
            self.q.put({"title": title, "body": body, "tag": tag or title, "url": url, "ts": int(time.time())})

    def _worker(self):
        while True:
            msg = self.q.get()
            for sub in list(self.subs):
                ok, why = self._send_one(sub, msg)
                self.log.append((time.strftime("%d %b %H:%M"), ok, msg["title"] if ok else why))
                del self.log[:-20]
            time.sleep(0.3)

    def _send_one(self, sub, msg):
        s = self.get()
        try:
            body = encrypt(json.dumps(msg).encode(), sub["p256dh"], sub["auth"])
            req = urllib.request.Request(sub["endpoint"], data=body, method="POST", headers={
                "TTL": "3600", "Urgency": "high", "Content-Encoding": "aes128gcm",
                "Content-Type": "application/octet-stream",
                "Authorization": vapid_header(s["vapid_pem"], s["vapid_pub"], sub["endpoint"], self.subject)})
            with urllib.request.urlopen(req, timeout=15) as r:
                return 200 <= r.status < 300, f"HTTP {r.status}"
        except urllib.error.HTTPError as e:
            if e.code in (404, 410):                      # the phone unsubscribed or the app was removed
                self.unsubscribe(sub["endpoint"])
                return False, "That phone is no longer subscribed; removed."
            return False, f"Push service said {e.code}: {e.read()[:120].decode(errors='replace')}"
        except Exception as e:
            return False, f"Couldn't send: {e}"
