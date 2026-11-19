"""Auto-trader tests: the edge check, sizing, paper and live trade handling against the pretend Dhan,
hands-off and the market-hours lock.     cd _trade && python3 -m unittest discover tests
"""
import os
import random
import sys
import tempfile
import time
import unittest
from datetime import datetime, timedelta, timezone

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
os.environ.setdefault("MT_DATA_DIR", tempfile.mkdtemp())

import mockdhan                                          # noqa: E402

PORT = int(os.environ.setdefault("MT_TEST_PORT", "3097"))
if mockdhan.M is None:                                   # one pretend Dhan for all test files
    mockdhan.serve(PORT, tick_every=3600)
os.environ.setdefault("MT_DHAN_API", f"http://127.0.0.1:{PORT}/v2")
os.environ.setdefault("MT_MASTER_URL", f"http://127.0.0.1:{PORT}/scrip-master.csv")
import autotrader                                        # noqa: E402
import dhan                                              # noqa: E402
from instruments import Instruments                      # noqa: E402

IST = timezone(timedelta(hours=5, minutes=30))
D = dhan.Dhan(mockdhan.CLIENT, mockdhan.TOKEN, f"http://127.0.0.1:{PORT}/v2")
INST = Instruments(tempfile.mkdtemp(), f"http://127.0.0.1:{PORT}/scrip-master.csv")
INST.load(force=True)


class Clock:
    def __init__(self):
        self.t = datetime(2026, 11, 16, 9, 20, tzinfo=IST)            # a Monday

    def __call__(self):
        return self.t


def walk(seed, n=480):
    rng = random.Random(seed)
    px, out, d = 100.0, [], datetime(2024, 12, 2, tzinfo=IST)
    while len(out) < n:
        if d.weekday() < 5:
            o = px * (1 + rng.gauss(0, .006))
            c = o * (1 + rng.gauss(0, .018))
            out.append({"t": int(d.timestamp()), "o": o, "h": max(o, c) * (1 + abs(rng.gauss(0, .009))),
                        "l": min(o, c) * (1 - abs(rng.gauss(0, .009))), "c": c, "v": rng.randint(200000, 2000000)})
            px = c
        d += timedelta(days=1)
    return out


class EdgeCheck(unittest.TestCase):
    def test_random_prices_have_no_edge(self):
        hist = {f"S{i}": walk(7000 + i) for i in range(30)}
        r = autotrader.choose(hist, walk(7999), dict(autotrader.DEFAULTS))
        self.assertFalse(r["passed"], r["why"])
        self.assertIn("No edge", r["why"])

    def test_sizing(self):
        c = dict(autotrader.DEFAULTS)
        self.assertEqual(autotrader.size(1000, 960, c), 37)            # ₹1,500 risk ÷ ₹40
        self.assertEqual(autotrader.size(1000, 995, c), 150)           # capped at 1× capital (₹1.5 L)
        self.assertEqual(autotrader.size(1000, 995, c, exposure=250000), 50)   # all positions ≤ 2× capital
        self.assertEqual(autotrader.size(1000, 1001, c), 0)


class Trading(unittest.TestCase):
    def setUp(self):
        self.clock = Clock()
        self.quotes = {}
        self.bot = autotrader.AutoTrader(tempfile.mkdtemp(), D, INST, self.quotes, self.clock)
        self.bot.s["armed"] = True
        self.key = INST.under["SBIN"]
        self.inst = mockdhan.M.inst[tuple(self.key.split(":"))]
        mockdhan.M.calls.clear()

    def px(self, v):
        self.inst["px"] = v
        self.quotes[self.key] = {"ltp": v, "t": time.time()}
        mockdhan.M.match()
        mockdhan.M.check_forever()

    def setup(self, entry=870, sl=850, t1=900, t2=930):
        self.bot.s["setups"] = [{"sym": "SBIN", "key": self.key, "tick": 0.05, "score": 70, "entry": entry, "sl": sl,
                                 "t1": t1, "t2": t2, "made": "2026-11-13", "days": 0, "why": [], "order": None}]

    def test_paper_trade_t1_then_cost_stop(self):
        self.setup()
        self.px(865)
        self.bot.morning()
        st = self.bot.s["setups"][0]
        self.assertEqual(st["qty"], 75)                                 # 1500 / 20
        self.px(871)
        self.bot.monitor()
        self.assertEqual(len(self.bot.s["positions"]), 1)
        self.px(901)
        self.bot.monitor()
        p = self.bot.s["positions"][0]
        self.assertEqual((p["left"], p["sl"], p["half"]), (38, 871, True))
        self.px(870)
        self.bot.monitor()
        self.assertEqual(self.bot.s["positions"], [])
        t = self.bot.s["closed"][0]
        self.assertEqual(t["why"], "stop at cost")
        self.assertAlmostEqual(t["gross"], 37 * 30 + 38 * -1, places=2)
        self.assertFalse([c for c in mockdhan.M.calls if c[1].startswith("/orders") or c[1].startswith("/forever")])   # paper: no orders

    def test_no_chasing_a_gap(self):
        self.setup()
        self.px(885)                                                    # 15 above an entry with 20 risk
        self.bot.morning()
        self.assertFalse(self.bot.s["setups"][0].get("qty"))
        self.assertIn("not chased", self.bot.s["log"][0]["msg"])

    def test_setup_expires_after_3_sessions(self):
        self.setup()
        self.px(860)
        for _ in range(4):
            self.bot.morning()
            self.bot.eod()
        self.assertEqual(self.bot.s["setups"], [])

    def test_live_entry_gtt_stop_and_stop_fill(self):
        self.bot.s["mode"] = "live"
        self.setup()
        self.px(865)
        self.bot.morning()
        m, path, b = [c for c in mockdhan.M.calls if c[1] == "/orders"][-1]
        self.assertEqual((b["transactionType"], b["productType"], b["orderType"], b["quantity"], b["triggerPrice"], b["price"]),
                         ("BUY", "MTF", "STOP_LOSS", 75, 870.0, 875.0))
        self.px(872)                                                    # triggers and fills in the pretend market
        self.bot.monitor()
        p = self.bot.s["positions"][0]
        self.assertEqual(p["stop_kind"], "gtt")
        g = [c for c in mockdhan.M.calls if c[1] == "/forever/orders" and c[0] == "POST"][-1][2]
        self.assertEqual((g["transactionType"], g["productType"], g["quantity"], g["triggerPrice"], g["orderFlag"]),
                         ("SELL", "MTF", 75, 850.0, "SINGLE"))
        self.px(849)                                                    # the GTT triggers at Dhan and sells
        self.bot.monitor()
        self.assertEqual(self.bot.s["positions"], [])
        self.assertEqual(self.bot.s["closed"][0]["why"], "stop")
        pos = mockdhan.M.pos[("NSE_EQ", self.inst["id"], "MTF")]
        self.assertEqual(pos["net"], 0)                                 # flat: never sold twice

    def test_live_t1_cancels_stop_before_selling(self):
        self.bot.s["mode"] = "live"
        self.setup()
        self.px(865)
        self.bot.morning()
        self.px(872)
        self.bot.monitor()
        self.assertTrue(self.bot.s["positions"], self.bot.s["log"][:4])
        first_gtt = self.bot.s["positions"][0]["stop_id"]
        self.px(902)
        self.bot.monitor()
        p = self.bot.s["positions"][0]
        self.assertEqual(mockdhan.M.forever[first_gtt]["orderStatus"], "CANCELLED")
        self.assertEqual(p["left"], 38)
        self.assertEqual(mockdhan.M.forever[p["stop_id"]]["quantity"], 38)
        self.assertEqual(mockdhan.M.forever[p["stop_id"]]["triggerPrice"], p["entry"])
        self.px(931)
        self.bot.monitor()
        self.assertEqual(self.bot.s["positions"], [])
        self.assertEqual(mockdhan.M.pos[("NSE_EQ", self.inst["id"], "MTF")]["net"], 0)

    def test_drawdown_stops_the_bot(self):
        self.bot.s["closed"] = [{"mode": "paper", "net": -13000.0}]
        self.bot._check_drawdown(self.bot.cfg())
        self.assertFalse(self.bot.s["armed"])
        self.assertIn("Drawdown", self.bot.s["halted"])

    def test_locks_and_live_gate(self):
        self.clock.t = datetime(2026, 11, 16, 11, 0, tzinfo=IST)
        self.assertIn("locked", self.bot.update_settings({"risk_pct": 2}))
        self.bot.arm(False)                                              # pause mid-day
        self.assertTrue(self.bot.hands_off())                            # hands-off holds until the close
        self.clock.t = datetime(2026, 11, 16, 16, 0, tzinfo=IST)
        self.assertFalse(self.bot.hands_off())
        self.assertEqual(self.bot.update_settings({"risk_pct": 0.5}), "")
        self.assertIn("between", self.bot.update_settings({"risk_pct": 5}))
        self.assertIn("Not yet", self.bot.set_mode("live", "LIVE"))     # no backtest, no paper sessions
        self.bot.s["backtest"] = {"passed": True, "score": 60}
        self.bot.s["paper_days"] = [f"d{i}" for i in range(10)]
        self.assertIn("Type LIVE", self.bot.set_mode("live", ""))
        self.assertEqual(self.bot.set_mode("live", "LIVE"), "")


if __name__ == "__main__":
    unittest.main()
