#!/usr/bin/env python3
"""Handral Clinic cloud server.

Keeps the clinic app's data (patients, appointments, bills, attendance and so on)
in one SQLite database on the droplet so every tablet, phone and laptop sees the
same records. Standard library only; runs behind Caddy, which adds HTTPS.

The app at https://handraldentistry.com/clinic/ talks to it with:
  POST /api/login            {username, password}            -> {token, user}
  POST /api/logout
  GET  /api/me
  GET  /api/changes?since=N  records changed after revision N
  POST /api/save             {changes: [{col, id, data, deleted}]}
  GET/POST /api/users        staff logins (admin only for changes)
  POST /api/files            upload a photo (raw body) -> {id}
  GET  /api/files/<id>       download it
  GET/POST /api/ai-key       set the Anthropic key (admin; never sent back)
  POST /api/ai/read          read a photo of a register page with Claude
  POST /api/public/request   appointment request from the public website (no login)
  GET  /healthz

Settings (environment):
  CLINIC_DB       database file   (default ./clinic.db)
  CLINIC_HOST     listen address  (default 127.0.0.1)
  CLINIC_PORT     listen port     (default 3030)
  CLINIC_ORIGINS  comma-separated web origins allowed to call the API
  CLINIC_ADMIN_PASSWORD  if set and there are no users yet, creates the login
                  "doctor" with this password (the installer does this)
"""
import base64
import hashlib
import hmac
import json
import os
import re
import secrets
import sqlite3
import sys
import threading
import time
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

DB_PATH = os.environ.get("CLINIC_DB", os.path.join(os.path.dirname(os.path.abspath(__file__)), "clinic.db"))
HOST = os.environ.get("CLINIC_HOST", "127.0.0.1")
PORT = int(os.environ.get("CLINIC_PORT", "3030"))
ORIGINS = {o.strip().rstrip("/") for o in os.environ.get(
    "CLINIC_ORIGINS", "https://handraldentistry.com,https://www.handraldentistry.com").split(",") if o.strip()}

COLLECTIONS = {
    "patients", "appointments", "invoices", "payments", "transactions", "attendance",
    "staff", "consultants", "consultcases", "photos", "settings", "requests",
}
MAX_BODY = 25 * 1024 * 1024
MAX_FILE = 15 * 1024 * 1024
SESSION_DAYS = 120
AI_MODEL = os.environ.get("CLINIC_AI_MODEL", "claude-sonnet-5-5")

_lock = threading.Lock()
_fails = {}  # ip -> [timestamps of failed logins]
_bookings = {}  # ip -> [timestamps of website booking requests]


# ── Database ────────────────────────────────────────────────────────────────
def db():
    con = sqlite3.connect(DB_PATH, timeout=30)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA journal_mode=WAL")
    con.execute("PRAGMA foreign_keys=ON")
    return con


def init_db():
    con = db()
    con.executescript("""
    CREATE TABLE IF NOT EXISTS users(
      id INTEGER PRIMARY KEY, username TEXT UNIQUE NOT NULL COLLATE NOCASE,
      name TEXT NOT NULL, role TEXT NOT NULL DEFAULT 'staff', pw TEXT NOT NULL,
      active INTEGER NOT NULL DEFAULT 1, created REAL NOT NULL);
    CREATE TABLE IF NOT EXISTS sessions(
      token TEXT PRIMARY KEY, user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
      created REAL NOT NULL, seen REAL NOT NULL, agent TEXT);
    CREATE TABLE IF NOT EXISTS docs(
      col TEXT NOT NULL, id TEXT NOT NULL, data TEXT, deleted INTEGER NOT NULL DEFAULT 0,
      rev INTEGER NOT NULL, updated REAL NOT NULL, updated_by TEXT,
      PRIMARY KEY(col, id));
    CREATE INDEX IF NOT EXISTS docs_rev ON docs(rev);
    CREATE TABLE IF NOT EXISTS history(
      rev INTEGER PRIMARY KEY, col TEXT, id TEXT, data TEXT, deleted INTEGER,
      at REAL, by TEXT);
    CREATE TABLE IF NOT EXISTS files(
      id TEXT PRIMARY KEY, mime TEXT NOT NULL, body BLOB NOT NULL, size INTEGER,
      created REAL NOT NULL, by TEXT);
    CREATE TABLE IF NOT EXISTS meta(k TEXT PRIMARY KEY, v TEXT);
    """)
    pw = os.environ.get("CLINIC_ADMIN_PASSWORD", "")
    if pw and not con.execute("SELECT 1 FROM users LIMIT 1").fetchone():
        con.execute("INSERT INTO users(username,name,role,pw,created) VALUES(?,?,?,?,?)",
                    ("doctor", "Dr. R.K. Handral", "admin", hash_pw(pw), time.time()))
    con.commit()
    con.close()


def meta_get(con, k, default=None):
    r = con.execute("SELECT v FROM meta WHERE k=?", (k,)).fetchone()
    return r["v"] if r else default


def meta_set(con, k, v):
    con.execute("INSERT INTO meta(k,v) VALUES(?,?) ON CONFLICT(k) DO UPDATE SET v=excluded.v", (k, v))


def hash_pw(pw):
    salt = secrets.token_bytes(16)
    h = hashlib.pbkdf2_hmac("sha256", pw.encode(), salt, 240000)
    return "pbkdf2$240000$" + base64.b64encode(salt).decode() + "$" + base64.b64encode(h).decode()


def check_pw(pw, stored):
    try:
        _, n, salt, h = stored.split("$")
        got = hashlib.pbkdf2_hmac("sha256", pw.encode(), base64.b64decode(salt), int(n))
        return hmac.compare_digest(got, base64.b64decode(h))
    except Exception:
        return False


def token_key(tok):
    return hashlib.sha256(tok.encode()).hexdigest()


def user_public(u):
    return {"id": u["id"], "username": u["username"], "name": u["name"], "role": u["role"],
            "active": bool(u["active"])}


# ── HTTP ────────────────────────────────────────────────────────────────────
class ApiError(Exception):
    def __init__(self, status, message):
        super().__init__(message)
        self.status = status
        self.message = message


class Handler(BaseHTTPRequestHandler):
    server_version = "HandralClinic"
    sys_version = ""
    protocol_version = "HTTP/1.1"

    def log_message(self, fmt, *args):
        # Paths and status only; never request bodies (patient data).
        sys.stderr.write("%s %s\n" % (self.address_string(), fmt % args))

    def client_ip(self):
        return (self.headers.get("X-Forwarded-For") or self.client_address[0]).split(",")[0].strip()

    # CORS: the app is served from handraldentistry.com; the API lives on its own address.
    def cors(self):
        origin = (self.headers.get("Origin") or "").rstrip("/")
        if origin and (origin in ORIGINS or "*" in ORIGINS):
            self.send_header("Access-Control-Allow-Origin", origin)
            self.send_header("Vary", "Origin")
            self.send_header("Access-Control-Allow-Headers", "Authorization, Content-Type")
            self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
            self.send_header("Access-Control-Max-Age", "86400")

    def send_json(self, status, obj):
        body = json.dumps(obj, separators=(",", ":")).encode()
        self.send_response(status)
        self.cors()
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def read_body(self, limit=MAX_BODY):
        n = int(self.headers.get("Content-Length") or 0)
        if n > limit:
            raise ApiError(413, "Too large")
        return self.rfile.read(n) if n else b""

    def read_json(self):
        raw = self.read_body()
        try:
            return json.loads(raw or b"{}")
        except ValueError:
            raise ApiError(400, "Bad JSON")

    def auth(self, con, admin=False):
        h = self.headers.get("Authorization", "")
        tok = h[7:] if h.startswith("Bearer ") else ""
        if not tok:
            raise ApiError(401, "Please sign in")
        r = con.execute(
            "SELECT u.*, s.seen FROM sessions s JOIN users u ON u.id=s.user_id WHERE s.token=?",
            (token_key(tok),)).fetchone()
        if not r or not r["active"] or r["seen"] < time.time() - SESSION_DAYS * 86400:
            raise ApiError(401, "Please sign in again")
        if r["seen"] < time.time() - 300:
            con.execute("UPDATE sessions SET seen=? WHERE token=?", (time.time(), token_key(tok)))
            con.commit()
        if admin and r["role"] != "admin":
            raise ApiError(403, "Only the doctor's login can do this")
        return r

    def do_OPTIONS(self):
        self.send_response(204)
        self.cors()
        self.send_header("Content-Length", "0")
        self.end_headers()

    def do_GET(self):
        self.route("GET")

    def do_POST(self):
        self.route("POST")

    def route(self, method):
        url = urlparse(self.path)
        path = url.path.rstrip("/") or "/"
        q = {k: v[0] for k, v in parse_qs(url.query).items()}
        con = None
        try:
            if path == "/healthz":
                return self.send_json(200, {"ok": True})
            if path == "/" and method == "GET":
                return self.send_json(200, {"app": "Handral Clinic server", "ok": True})
            con = db()
            fn = ROUTES.get((method, path))
            if fn:
                return fn(self, con, q)
            m = re.fullmatch(r"/api/files/([A-Za-z0-9_-]{8,64})", path)
            if m and method == "GET":
                return get_file(self, con, m.group(1))
            raise ApiError(404, "Not found")
        except ApiError as e:
            self.send_json(e.status, {"error": e.message})
        except Exception as e:  # pragma: no cover
            sys.stderr.write("error %s: %r\n" % (path, e))
            self.send_json(500, {"error": "Server error"})
        finally:
            if con is not None:
                con.close()


# ── Handlers ────────────────────────────────────────────────────────────────
def login(h, con, q):
    ip = h.client_ip()
    now = time.time()
    with _lock:
        recent = [t for t in _fails.get(ip, []) if t > now - 900]
        _fails[ip] = recent
    if len(recent) >= 10:
        raise ApiError(429, "Too many wrong tries. Wait 15 minutes and try again.")
    b = h.read_json()
    u = con.execute("SELECT * FROM users WHERE username=?", ((b.get("username") or "").strip(),)).fetchone()
    if not u or not u["active"] or not check_pw(b.get("password") or "", u["pw"]):
        with _lock:
            _fails.setdefault(ip, []).append(now)
        time.sleep(0.5)
        raise ApiError(401, "Wrong username or password")
    tok = secrets.token_urlsafe(32)
    con.execute("INSERT INTO sessions(token,user_id,created,seen,agent) VALUES(?,?,?,?,?)",
                (token_key(tok), u["id"], now, now, (h.headers.get("User-Agent") or "")[:200]))
    con.commit()
    h.send_json(200, {"token": tok, "user": user_public(u)})


def logout(h, con, q):
    auth = h.headers.get("Authorization", "")
    if auth.startswith("Bearer "):
        con.execute("DELETE FROM sessions WHERE token=?", (token_key(auth[7:]),))
        con.commit()
    h.send_json(200, {"ok": True})


def me(h, con, q):
    u = h.auth(con)
    h.send_json(200, {"user": user_public(u), "ai": bool(meta_get(con, "anthropic_key"))})


def changes(h, con, q):
    h.auth(con)
    try:
        since = int(q.get("since") or 0)
    except ValueError:
        since = 0
    limit = 3000
    rows = con.execute(
        "SELECT col,id,data,deleted,rev,updated,updated_by FROM docs WHERE rev>? ORDER BY rev LIMIT ?",
        (since, limit + 1)).fetchall()
    more = len(rows) > limit
    rows = rows[:limit]
    out = []
    for r in rows:
        out.append({"col": r["col"], "id": r["id"], "rev": r["rev"], "deleted": bool(r["deleted"]),
                    "data": None if r["deleted"] else json.loads(r["data"]),
                    "updated": r["updated"], "by": r["updated_by"]})
    # Only advance to revisions actually sent, so a write landing mid-read is never skipped.
    h.send_json(200, {"docs": out, "rev": rows[-1]["rev"] if rows else since, "more": more})


def save(h, con, q):
    u = h.auth(con)
    b = h.read_json()
    items = b.get("changes")
    if not isinstance(items, list) or len(items) > 2000:
        raise ApiError(400, "Bad changes")
    now = time.time()
    saved = []
    with _lock:
        cur = con.execute("SELECT COALESCE(MAX(rev),0) FROM docs").fetchone()[0]
        for c in items:
            col, did = c.get("col"), str(c.get("id") or "")
            if col not in COLLECTIONS or not did or len(did) > 100:
                raise ApiError(400, "Bad record")
            if col == "settings" and u["role"] != "admin" and did != "ui":
                raise ApiError(403, "Only the doctor's login can change settings")
            deleted = bool(c.get("deleted"))
            data = None if deleted else c.get("data")
            if not deleted and not isinstance(data, dict):
                raise ApiError(400, "Bad record")
            if data is not None:
                data["id"] = did
            cur += 1
            js = None if data is None else json.dumps(data, separators=(",", ":"))
            con.execute(
                "INSERT INTO docs(col,id,data,deleted,rev,updated,updated_by) VALUES(?,?,?,?,?,?,?) "
                "ON CONFLICT(col,id) DO UPDATE SET data=excluded.data, deleted=excluded.deleted, "
                "rev=excluded.rev, updated=excluded.updated, updated_by=excluded.updated_by",
                (col, did, js, int(deleted), cur, now, u["name"]))
            con.execute("INSERT INTO history(rev,col,id,data,deleted,at,by) VALUES(?,?,?,?,?,?,?)",
                        (cur, col, did, js, int(deleted), now, u["name"]))
            saved.append({"col": col, "id": did, "rev": cur})
        con.commit()
    h.send_json(200, {"saved": saved, "rev": cur})


def users_list(h, con, q):
    h.auth(con)
    rows = con.execute("SELECT * FROM users ORDER BY role, name").fetchall()
    h.send_json(200, {"users": [user_public(r) for r in rows]})


def users_save(h, con, q):
    u = h.auth(con)
    b = h.read_json()
    uid = b.get("id")
    # Anyone may change their own password; everything else is for the admin.
    if uid and int(uid) == u["id"] and set(b) <= {"id", "password", "old_password"}:
        if not check_pw(b.get("old_password") or "", u["pw"]):
            raise ApiError(400, "Current password is wrong")
        set_password(con, u["id"], b.get("password") or "")
        con.commit()
        return h.send_json(200, {"ok": True})
    if u["role"] != "admin":
        raise ApiError(403, "Only the doctor's login can manage logins")
    name = (b.get("name") or "").strip()
    username = re.sub(r"\s+", "", (b.get("username") or "").strip().lower())
    role = "admin" if b.get("role") == "admin" else "staff"
    if uid:
        r = con.execute("SELECT * FROM users WHERE id=?", (int(uid),)).fetchone()
        if not r:
            raise ApiError(404, "No such login")
        active = 1 if b.get("active", True) else 0
        if r["id"] == u["id"] and (not active or role != "admin"):
            raise ApiError(400, "You can't remove your own doctor access")
        con.execute("UPDATE users SET name=?, role=?, active=? WHERE id=?",
                    (name or r["name"], role, active, r["id"]))
        if not active:
            con.execute("DELETE FROM sessions WHERE user_id=?", (r["id"],))
        if b.get("password"):
            set_password(con, r["id"], b["password"])
    else:
        if not name or not re.fullmatch(r"[a-z0-9._-]{3,30}", username):
            raise ApiError(400, "Give a name and a username of 3-30 letters or numbers")
        if con.execute("SELECT 1 FROM users WHERE username=?", (username,)).fetchone():
            raise ApiError(400, "That username is taken")
        pw = b.get("password") or ""
        if len(pw) < 6:
            raise ApiError(400, "Password must be at least 6 characters")
        con.execute("INSERT INTO users(username,name,role,pw,created) VALUES(?,?,?,?,?)",
                    (username, name, role, hash_pw(pw), time.time()))
    con.commit()
    users_list(h, con, q)


def set_password(con, uid, pw):
    if len(pw) < 6:
        raise ApiError(400, "Password must be at least 6 characters")
    con.execute("UPDATE users SET pw=? WHERE id=?", (hash_pw(pw), uid))


def upload_file(h, con, q):
    u = h.auth(con)
    mime = (h.headers.get("Content-Type") or "").split(";")[0].strip().lower()
    if not re.fullmatch(r"(image/(jpeg|png|webp|heic|heif|gif)|application/pdf)", mime):
        raise ApiError(415, "Only photos and PDFs can be uploaded")
    body = h.read_body(MAX_FILE)
    if not body:
        raise ApiError(400, "Empty file")
    fid = secrets.token_urlsafe(18)
    con.execute("INSERT INTO files(id,mime,body,size,created,by) VALUES(?,?,?,?,?,?)",
                (fid, mime, body, len(body), time.time(), u["name"]))
    con.commit()
    h.send_json(200, {"id": fid})


def get_file(h, con, fid):
    h.auth(con)
    r = con.execute("SELECT mime, body FROM files WHERE id=?", (fid,)).fetchone()
    if not r:
        raise ApiError(404, "Not found")
    h.send_response(200)
    h.cors()
    h.send_header("Content-Type", r["mime"])
    h.send_header("Cache-Control", "private, max-age=31536000, immutable")
    h.send_header("Content-Length", str(len(r["body"])))
    h.end_headers()
    h.wfile.write(r["body"])


def ai_key(h, con, q):
    if h.command == "GET":
        h.auth(con)
        return h.send_json(200, {"set": bool(meta_get(con, "anthropic_key"))})
    h.auth(con, admin=True)
    key = (h.read_json().get("key") or "").strip()
    if key and not key.startswith("sk-ant-"):
        raise ApiError(400, "That doesn't look like an Anthropic key (it starts with sk-ant-)")
    meta_set(con, "anthropic_key", key)
    con.commit()
    h.send_json(200, {"set": bool(key)})


AI_PROMPT = """You are reading a photo of a page from an Indian dental clinic's handwritten register or appointment book.
Today is {today}. Known patients: {names}.
Extract EVERY entry you can see. Return ONLY a JSON array (no markdown), one object per entry with:
 "name" (string), "phone" (digits or null), "age" (number or null), "gender" ("M"/"F"/null),
 "treatment" (dental procedure or complaint, or null), "amount" (rupees paid, number or null),
 "payMode" ("Cash"/"UPI"/"Card"/null), "date" (YYYY-MM-DD or null), "time" (HH:MM 24h or null),
 "notes" (anything else, or null), "match" (exact name from Known patients if this is the same person, else null).
If a value is unreadable use null rather than guessing."""


def ai_read(h, con, q):
    h.auth(con)
    key = meta_get(con, "anthropic_key")
    if not key:
        raise ApiError(400, "Add the Claude AI key in Settings first")
    b = h.read_json()
    img = b.get("image") or ""
    mime = b.get("mime") or "image/jpeg"
    if mime not in ("image/jpeg", "image/png", "image/webp", "image/gif") or not img:
        raise ApiError(400, "Send a JPEG or PNG photo")
    names = ", ".join((b.get("names") or [])[:400]) or "none yet"
    prompt = AI_PROMPT.format(today=time.strftime("%Y-%m-%d"), names=names)
    req = urllib.request.Request(
        "https://api.anthropic.com/v1/messages",
        data=json.dumps({"model": AI_MODEL, "max_tokens": 4000, "messages": [{"role": "user", "content": [
            {"type": "image", "source": {"type": "base64", "media_type": mime, "data": img}},
            {"type": "text", "text": prompt}]}]}).encode(),
        headers={"content-type": "application/json", "x-api-key": key, "anthropic-version": "2023-06-01"})
    try:
        with urllib.request.urlopen(req, timeout=120) as r:
            res = json.loads(r.read())
    except urllib.error.HTTPError as e:
        try:
            msg = json.loads(e.read()).get("error", {}).get("message", "")
        except Exception:
            msg = ""
        raise ApiError(502, "Claude AI error: " + (msg or str(e.code)))
    except Exception:
        raise ApiError(502, "Could not reach Claude AI. Try again.")
    text = "".join(p.get("text", "") for p in res.get("content", []) if p.get("type") == "text")
    m = re.search(r"\[.*\]", text, re.S)
    try:
        entries = json.loads(m.group(0)) if m else []
    except ValueError:
        entries = []
    h.send_json(200, {"entries": [e for e in entries if isinstance(e, dict)]})


def public_request(h, con, q):
    """Appointment request from handraldentistry.com. No login; rate limited."""
    ip = h.client_ip()
    now = time.time()
    with _lock:
        recent = [t for t in _bookings.get(ip, []) if t > now - 3600]
        _bookings[ip] = recent
        if len(recent) >= 6:
            raise ApiError(429, "Too many requests. Please call or WhatsApp the clinic.")
        recent.append(now)
    b = h.read_json()
    if b.get("website"):  # hidden field only bots fill in
        return h.send_json(200, {"ok": True})
    clean = lambda k, n: re.sub(r"[\x00-\x1f]", " ", str(b.get(k) or "")).strip()[:n]
    name, phone = clean("name", 80), re.sub(r"\D", "", clean("phone", 20))
    if len(phone) == 12 and phone.startswith("91"):
        phone = phone[2:]
    if len(name) < 2 or len(phone) != 10:
        raise ApiError(400, "Please enter your name and a 10-digit mobile number.")
    if not b.get("consent"):
        raise ApiError(400, "Please tick the box so we may contact you.")
    clinic = clean("clinic", 20)
    date = clean("date", 10)
    data = {"name": name, "phone": phone, "clinic": clinic if clinic in ("bagalkot", "vidyagiri", "navanagar") else "",
            "date": date if re.fullmatch(r"\d{4}-\d{2}-\d{2}", date) else "", "time": clean("time", 20),
            "treatment": clean("treatment", 60), "message": clean("message", 500), "status": "new",
            "consent": True, "received": time.strftime("%Y-%m-%dT%H:%M:%S")}
    did = "w" + secrets.token_hex(8)
    data["id"] = did
    with _lock:
        cur = con.execute("SELECT COALESCE(MAX(rev),0) FROM docs").fetchone()[0] + 1
        js = json.dumps(data, separators=(",", ":"))
        con.execute("INSERT INTO docs(col,id,data,deleted,rev,updated,updated_by) VALUES('requests',?,?,0,?,?,?)",
                    (did, js, cur, now, "Website"))
        con.execute("INSERT INTO history(rev,col,id,data,deleted,at,by) VALUES(?,?,?,?,0,?,?)",
                    (cur, "requests", did, js, now, "Website"))
        con.commit()
    h.send_json(200, {"ok": True})


ROUTES = {
    ("POST", "/api/public/request"): public_request,
    ("POST", "/api/login"): login,
    ("POST", "/api/logout"): logout,
    ("GET", "/api/me"): me,
    ("GET", "/api/changes"): changes,
    ("POST", "/api/save"): save,
    ("GET", "/api/users"): users_list,
    ("POST", "/api/users"): users_save,
    ("POST", "/api/files"): upload_file,
    ("GET", "/api/ai-key"): ai_key,
    ("POST", "/api/ai-key"): ai_key,
    ("POST", "/api/ai/read"): ai_read,
}


def main():
    if len(sys.argv) >= 3 and sys.argv[1] == "set-password":
        # handral-clinic set-password <username>  (password read from stdin)
        init_db()
        pw = sys.stdin.readline().strip()
        con = db()
        r = con.execute("SELECT id FROM users WHERE username=?", (sys.argv[2],)).fetchone()
        if r:
            set_password(con, r["id"], pw)
            con.execute("UPDATE users SET active=1 WHERE id=?", (r["id"],))
        else:
            con.execute("INSERT INTO users(username,name,role,pw,created) VALUES(?,?,?,?,?)",
                        (sys.argv[2], "Dr. R.K. Handral", "admin", hash_pw(pw), time.time()))
        con.commit()
        print("Password set for", sys.argv[2])
        return
    init_db()
    srv = ThreadingHTTPServer((HOST, PORT), Handler)
    srv.daemon_threads = True
    print("Handral Clinic server on http://%s:%d (db %s)" % (HOST, PORT, DB_PATH), flush=True)
    srv.serve_forever()


if __name__ == "__main__":
    main()
