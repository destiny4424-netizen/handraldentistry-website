"""The Screener tab's link to the Meridian scanner: signing in, the rows it shows, and failures.
    cd _trade && python3 -m unittest discover tests
"""
import json
import os
import sys
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import screener                                          # noqa: E402

PW = "scanner-pw"
SEEN = {"logins": 0, "expire": False, "scans": 0}
HEALTH = {"checks": [{"name": "Server", "level": "ok", "text": "Running"},
                     {"name": "Dhan connection", "level": "bad", "text": "Token expired 25 Nov 10:00. Paste a new one in Settings."},
                     {"name": "Last scan", "level": "warn", "text": "should have scanned again by now"}]}
STATE = {"status": "live", "demo": False, "last_scan": "10:15", "next_scan": "10:30", "message": "",
         "trades": {"rows": [{"symbol": "SBIN", "side": "bull", "entry": 870.5, "sl": 864.0, "t1": 880.25, "t2": 889.0,
                              "status": "T1 hit", "result_r": None, "basis": "PDH breakout", "exit_at": None},
                             {"symbol": "TCS", "side": "bear", "entry": 3400, "sl": 3420, "t1": 3370, "t2": 3350,
                              "status": "SL hit", "result_r": -1.0}],
                    "summary": {"day": "2026-11-25", "count": 2, "triggered": 2, "wins": 0, "losses": 1, "total_r": -1.0}},
         "last_scan_ts": 1000.0,
         "btst": {"open": True, "from": "11:00", "caution": "", "picks": [
             {"symbol": "INFY", "side": "bull", "price": 1580, "plan": {"entry": 1585, "sl": 1550, "t1": 1655, "t2": 1700,
                                                                       "basis": "Swing · FVG", "action": "Buy above 1585"}}],
                  "book": {"rows": [{"symbol": "ITC", "side": "bull", "entry": 415, "sl": 405, "t1": 435, "t2": 450,
                                     "status": "Triggered", "result_r": None, "exit_at": None},
                                    {"symbol": "LT", "side": "bull", "entry": 3600, "sl": 3500, "t1": 3800, "t2": 3900,
                                     "status": "T2 hit", "result_r": 2.5, "exit_at": 1}]}}}
PERF = {"month": "2026-11", "intraday": {"summary": {"closed": 20, "win_rate": 55, "avg_r": 0.4, "total_r": 8.0}},
        "btst": {"summary": {"closed": 4, "win_rate": 50, "avg_r": 0.6, "total_r": 2.4}}}
BT = {"job": {"status": "idle"}, "report": {"ran_at": "24 Nov 2026 18:00", "sessions": 60, "from": "2026-08-20", "to": "2026-11-21",
                                           "intraday": {"summary": {"closed": 260, "win_rate": 52, "avg_r": 0.21, "max_drawdown": -6.5}},
                                           "btst": {"summary": {"closed": 30, "win_rate": 47, "avg_r": 0.3, "max_drawdown": -4.0}}}}


class H(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def reply(self, code, obj, cookie=None):
        data = json.dumps(obj).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        if cookie:
            self.send_header("Set-Cookie", f"obs={cookie}; Path=/; HttpOnly")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        if self.path == "/api/login":
            SEEN["logins"] += 1
            if body.get("pin") != PW:
                return self.reply(401, {"error": "Wrong password."})
            return self.reply(200, {"ok": True}, cookie=f"tok{SEEN['logins']}")
        if "obs=" not in (self.headers.get("Cookie") or ""):
            return self.reply(401, {})
        if self.path == "/api/scan":
            SEEN["scans"] += 1
        return self.reply(200, {"ok": True})

    def do_GET(self):
        ok = "obs=tok" in (self.headers.get("Cookie") or "")
        if SEEN["expire"]:                             # the scanner restarted: old cookies stop working
            SEEN["expire"] = False
            ok = False
        if not ok:
            return self.reply(401, {"error": "Sign in first."})
        self.reply(200, {"/api/state": STATE, "/api/performance": PERF, "/api/backtest": BT,
                         "/api/health": HEALTH}.get(self.path.split("?")[0], {}))


srv = ThreadingHTTPServer(("127.0.0.1", 0), H)
threading.Thread(target=srv.serve_forever, daemon=True).start()
URL = f"http://127.0.0.1:{srv.server_address[1]}"


class Screener(unittest.TestCase):
    def test_rows_perf_and_backtest(self):
        sc = screener.Scanner(URL, PW, key_of=lambda s: f"NSE_EQ:{s}" if s != "TCS" else None)
        v = sc.view()
        self.assertTrue(v["ok"], v)
        sbin, tcs = v["intraday"]["rows"]
        self.assertEqual((sbin["sym"], sbin["side"], sbin["entry"], sbin["sl"], sbin["t1"], sbin["t2"], sbin["key"]),
                         ("SBIN", "BUY", 870.5, 864.0, 880.25, 889.0, "NSE_EQ:SBIN"))
        self.assertEqual((tcs["side"], tcs["r"], tcs["key"]), ("SELL", -1.0, None))
        self.assertEqual([r["sym"] for r in v["swing"]["rows"]], ["INFY"])                 # today's setups from the plan
        self.assertEqual(v["swing"]["rows"][0]["entry"], 1585.0)
        self.assertEqual([r["sym"] for r in v["swing"]["running"]], ["ITC"])               # finished swing trades left out
        self.assertEqual(v["perf"]["intraday"]["win_rate"], 55)
        self.assertEqual(v["backtest"]["intraday"]["max_drawdown"], -6.5)
        self.assertEqual(v["backtest"]["sessions"], 60)

    def test_stale_scanner_is_flagged(self):
        sc = screener.Scanner(URL, PW)
        real = screener.market_open
        screener.market_open = lambda t=None: True
        try:
            v = sc.view()
        finally:
            screener.market_open = real
        self.assertTrue(v["overdue"])                                                    # last scan long ago, market open
        self.assertTrue(v["rows_old"])                                                   # the picks are another day's
        self.assertEqual(v["rows_day"], "2026-11-25")
        self.assertEqual([p["name"] for p in v["problems"]], ["Dhan connection", "Last scan"])   # ok checks left out
        sc._req("/api/scan", {})
        self.assertEqual(SEEN["scans"], 1)

    def test_signs_in_again_when_the_session_ends(self):
        sc = screener.Scanner(URL, PW)
        sc.view()
        n = SEEN["logins"]
        SEEN["expire"] = True
        sc.refresh(force=True)
        self.assertEqual(sc.error, "")
        self.assertEqual(SEEN["logins"], n + 1)

    def test_wrong_password_backs_off(self):
        sc = screener.Scanner(URL, "wrong")
        v = sc.view()
        self.assertFalse(v["ok"])
        n = SEEN["logins"]
        sc.refresh(force=True)
        self.assertEqual(SEEN["logins"], n)                                                 # no retry for 10 minutes
        self.assertIn("refused the password", sc.error)

    def test_no_password_or_no_scanner(self):
        self.assertIn("OBS_PASSWORD", screener.Scanner(URL, "").view()["error"])
        self.assertIn("Can't reach", screener.Scanner("http://127.0.0.1:9", PW).view()["error"])


if __name__ == "__main__":
    unittest.main()
