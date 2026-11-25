"""The Meridian scanner's picks in the trading desk: intraday and swing, each with entry, stop-loss and exit
(targets), this month's live performance and the latest backtest.

The scanner (_scanner/app.py, the orderblock-scanner service) runs on the same droplet at 127.0.0.1:3040 and
needs its password. The desk's systemd unit also loads the scanner's /etc/default/orderblock-scanner, so the
password arrives here as OBS_PASSWORD with nothing to copy. Nothing is computed twice: this only reads
/api/state, /api/performance and /api/backtest and keeps what the tab shows.
"""
import json
import threading
import time
import urllib.error
import urllib.request
from http.cookies import SimpleCookie


def _num(x):
    try:
        return round(float(x), 2)
    except (TypeError, ValueError):
        return None


def _summary(sm, backtest=False):
    sm = sm or {}
    keep = ("closed", "wins", "losses", "win_rate", "total_r", "avg_r", "profit_factor", "losing_streak")
    out = {k: sm.get(k) for k in keep}
    if backtest:
        out.update(max_drawdown=sm.get("max_drawdown"), days=sm.get("days"))
    return out


class Scanner:
    STATE_EVERY, SLOW_EVERY = 20, 300                  # seconds: picks; performance and backtest

    def __init__(self, url, password, key_of=lambda sym: None):
        self.url, self.password, self.key_of = url.rstrip("/"), password, key_of
        self.cookie = ""
        self.lock = threading.Lock()
        self.state = self.perf = self.bt = None
        self.state_at = self.slow_at = 0.0
        self.error = ""
        self.login_wait = 0.0                          # after a wrong password: don't try again for a while
        self.busy = False

    # -- talking to the scanner
    def _req(self, path, body=None):
        data = json.dumps(body).encode() if body is not None else None
        req = urllib.request.Request(self.url + path, data=data, method="POST" if data is not None else "GET")
        req.add_header("Content-Type", "application/json")
        if self.cookie:
            req.add_header("Cookie", f"obs={self.cookie}")
        with urllib.request.urlopen(req, timeout=10) as r:
            raw, hdr = r.read(), r.headers
        return json.loads(raw or b"{}"), hdr

    def _login(self):
        if not self.password:
            raise RuntimeError("The scanner's password isn't available to the desk (OBS_PASSWORD).")
        if time.time() < self.login_wait:
            raise RuntimeError("The scanner refused the password; trying again in a few minutes.")
        try:
            _, hdr = self._req("/api/login", {"pin": self.password})
        except urllib.error.HTTPError as e:
            if e.code in (401, 403, 429):
                self.login_wait = time.time() + 600
            raise
        for c in hdr.get_all("Set-Cookie") or []:
            m = SimpleCookie(c).get("obs")
            if m:
                self.cookie = m.value

    def _get(self, path):
        if not self.cookie:
            self._login()
        try:
            return self._req(path)[0]
        except urllib.error.HTTPError as e:
            if e.code != 401:
                raise
            self.cookie = ""
            self._login()
            return self._req(path)[0]

    def refresh(self, force=False):
        now = time.time()
        try:
            if force or now - self.state_at > self.STATE_EVERY:
                self.state, self.state_at = self._get("/api/state"), now
            running = ((self.bt or {}).get("job") or {}).get("status") == "running"
            if force or now - self.slow_at > (self.STATE_EVERY if running else self.SLOW_EVERY):
                self.perf = self._get("/api/performance")
                self.bt = self._get("/api/backtest")
                self.slow_at = now
            self.error = ""
        except urllib.error.HTTPError as e:
            self.error = f"The scanner answered {e.code}."
        except (urllib.error.URLError, OSError) as e:
            self.error = f"Can't reach the Meridian scanner ({getattr(e, 'reason', e)})."
        except (ValueError, RuntimeError) as e:
            self.error = str(e)

    def _refresh_bg(self):
        try:
            self.refresh()
        finally:
            self.busy = False

    # -- what the tab shows
    def view(self):
        stale = time.time() - self.state_at > self.STATE_EVERY
        if self.state is None:
            self.refresh()                             # first time: wait for it
        elif stale and not self.busy:
            self.busy = True
            threading.Thread(target=self._refresh_bg, daemon=True).start()
        s = self.state
        if s is None:
            return {"ok": False, "error": self.error or "The scanner hasn't answered yet."}
        trades = s.get("trades") or {}
        btst = s.get("btst") or {}
        intraday = [self.row(r, r) for r in trades.get("rows") or []]
        swing_new = [self.row(p, p.get("plan") or {}, new=True) for p in btst.get("picks") or []]
        running = [self.row(r, r) for r in (btst.get("book") or {}).get("rows") or [] if r.get("exit_at") is None]
        perf, rep = self.perf or {}, (self.bt or {}).get("report") or {}
        return {
            "ok": True, "error": self.error, "demo": bool(s.get("demo")), "status": s.get("status"),
            "message": s.get("message") or "", "last_scan": s.get("last_scan"), "next_scan": s.get("next_scan"),
            "schedule": s.get("schedule") or "",
            "intraday": {"rows": intraday, "today": trades.get("summary") or {}},
            "swing": {"rows": swing_new, "running": running, "open": bool(btst.get("open")), "from": btst.get("from"),
                      "caution": btst.get("caution") or ""},
            "perf": {"month": perf.get("month"), "intraday": _summary((perf.get("intraday") or {}).get("summary")),
                     "swing": _summary((perf.get("btst") or {}).get("summary"))},
            "backtest": {"ran_at": rep.get("ran_at"), "sessions": rep.get("sessions"), "from": rep.get("from"), "to": rep.get("to"),
                         "intraday": _summary((rep.get("intraday") or {}).get("summary"), True) if rep else None,
                         "swing": _summary((rep.get("btst") or {}).get("summary"), True) if rep else None,
                         "job": ((self.bt or {}).get("job") or {}).get("status")},
        }

    def row(self, r, plan, new=False):
        sym = r.get("symbol") or ""
        return {"sym": sym, "key": self.key_of(sym), "side": "BUY" if r.get("side") == "bull" else "SELL",
                "entry": _num(plan.get("entry")), "sl": _num(plan.get("sl")), "t1": _num(plan.get("t1")), "t2": _num(plan.get("t2")),
                "status": "Setup" if new else (r.get("status") or ""), "r": r.get("result_r"),
                "basis": plan.get("basis") or r.get("basis") or "", "action": plan.get("action") or ""}
