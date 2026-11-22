"""Rules mode tests: levels, the checks on an opening order, confirmation, the journal.
    cd _trade && python3 -m unittest discover tests
"""
import os
import sys
import tempfile
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
import trade                                             # noqa: E402
import rules                                             # noqa: E402

IST = timezone(timedelta(hours=5, minutes=30))
trade.D.client_id, trade.D.token = mockdhan.CLIENT, mockdhan.TOKEN
if not trade.INST.by_key:
    trade.INST.load(force=True)


class Clock:
    t = datetime(2026, 11, 18, 11, 0, tzinfo=IST)        # a Wednesday, market open

    def __call__(self):
        return self.t


def day(t, o, h, l, c):
    return {"t": int(t.timestamp()), "o": o, "h": h, "l": l, "c": c, "v": 1e6}


class Levels(unittest.TestCase):
    def test_previous_day_and_week(self):
        d0 = datetime(2026, 10, 1, tzinfo=IST)
        rows, px, i = [], 100.0, 0
        while len(rows) < 40:
            dt = d0 + timedelta(days=i)
            i += 1
            if dt.weekday() < 5:
                rows.append(day(dt, px, px + 1, px - 1, px + 0.2))
                px += 0.2
        lv = rules.find_levels(rows, rows[-1]["h"] + 0.1, 0.3, datetime(2026, 11, 26, tzinfo=IST))
        names = " ".join(x["name"] for x in lv["levels"])
        self.assertIn("Yesterday's high", names)
        self.assertIn("Last week's high", names)
        self.assertTrue(lv["levels"][0]["near"])                 # PDH is 0.1 away, ATR about 2
        self.assertFalse(lv["levels"][-1]["near"])

    def test_reaction(self):
        self.assertTrue(rules.reaction({"o": 100.2, "h": 101, "l": 99.9, "c": 100.8}, 100, 0.2, True))
        self.assertFalse(rules.reaction({"o": 100.8, "h": 101, "l": 99.9, "c": 100.2}, 100, 0.2, True))   # red candle
        self.assertFalse(rules.reaction({"o": 101, "h": 102, "l": 100.9, "c": 101.8}, 100, 0.2, True))    # never touched
        self.assertTrue(rules.reaction({"o": 99.9, "h": 100.1, "l": 99, "c": 99.2}, 100, 0.2, False))


class Checks(unittest.TestCase):
    def setUp(self):
        self.clock = Clock()
        self.r = rules.Rules(tempfile.mkdtemp(), trade.D, trade.INST, trade.QUOTES, self.clock)
        trade.RULES = self.r
        trade.BOT = None
        self.r.place_fn = trade.place
        trade.RECENT.clear()
        trade.SETTINGS["limits"] = {}
        self.key = next(k for k, i in trade.INST.by_key.items() if i["sym"] == "INFY" and i["seg"] == "NSE_EQ")
        lv = self.r.levels_for(self.key)
        self.level = lv["levels"][0]
        mockdhan.M.inst[("NSE_EQ", self.key.split(":")[1])]["px"] = self.level["price"]   # price at the level
        self.r.daily.clear()
        trade.QUOTES.pop(self.key, None)

    def tearDown(self):
        trade.RULES = None

    def order(self, **kw):
        b = {"key": self.key, "side": "BUY", "qty": 10, "product": "INTRADAY", "type": "LIMIT",
             "price": self.level["price"], "level": self.level["id"]}
        b.update(kw)
        return trade.place(b)

    def test_needs_a_stop(self):
        code, r = self.order()
        self.assertEqual(code, 400)
        self.assertIn("Rule 3", r["error"])

    def test_size_from_the_stop(self):
        p = self.level["price"]
        code, r = self.order(qty=500, super={"sl": p - 10})
        self.assertEqual(code, 400)
        self.assertIn("at most 150", r["error"])                 # ₹1,500 ÷ ₹10
        code, r = self.order(qty=150, super={"sl": p - 10})
        self.assertEqual(code, 200, r)
        self.assertEqual(self.r.s["sent"][self.r.today()][-1]["level"], self.level["name"])

    def test_needs_a_near_level(self):
        p = self.level["price"]
        code, r = self.order(level="", super={"sl": p - 10})
        self.assertEqual(code, 400)
        self.assertIn("Rule 1", r["error"])
        far = next((x for x in self.r.levels_for(self.key)["levels"] if not x["near"]), None)
        if far:
            code, r = self.order(level=far["id"], super={"sl": p - 10})
            self.assertIn("not near enough", r["error"])

    def test_closing_is_never_blocked(self):
        p = self.level["price"]
        self.assertEqual(self.order(qty=20, super={"sl": p - 10})[0], 200)
        trade.RECENT.clear()
        code, r = trade.place({"key": self.key, "side": "SELL", "qty": 20, "product": "INTRADAY", "type": "MARKET"})
        self.assertEqual(code, 200, r)                           # no level, no stop: it only closes

    def test_trades_a_day(self):
        trade.SETTINGS["limits"] = {"max_trades_day": 1}
        p = self.level["price"]
        self.assertEqual(self.order(qty=10, super={"sl": p - 10})[0], 200)
        trade.RECENT.clear()
        code, r = self.order(qty=11, super={"sl": p - 10})
        self.assertIn("limit is 1", r["error"])

    def test_confirmation_waits_then_sends(self):
        p = self.level["price"]
        mockdhan.M.calls.clear()
        code, r = self.order(qty=20, super={"sl": p - 10}, confirm=True)
        self.assertEqual(code, 200, r)
        self.assertTrue(r["armed"])
        self.assertFalse([c for c in mockdhan.M.calls if c[1] in ("/orders", "/super/orders")])   # nothing sent yet
        a = self.r.s["armed"][0]
        t0 = a["since"]
        bar = 900 * (t0 // 900 + 1)
        self.r.candles15 = lambda k: [{"t": bar - 900, "o": p + 2, "h": p + 3, "l": p + 1, "c": p + 2.5},      # no touch
                                      {"t": bar, "o": p + 0.5, "h": p + 4, "l": p - 1, "c": p + 3}]          # touch + close up
        trade.QUOTES[self.key] = {"ltp": p + 3, "t": __import__("time").time()}
        self.clock.t = datetime.fromtimestamp(bar + 900 + 30, IST)
        self.r.tick()
        self.assertEqual(a["status"], "sent", a.get("note"))
        sup = [c for c in mockdhan.M.calls if c[1] == "/super/orders"][-1][2]
        self.assertEqual(sup["stopLossPrice"], trade.round_tick(p - 10, 0.05))
        self.assertLessEqual(sup["quantity"] * (sup["price"] - sup["stopLossPrice"]), 1500.01)

    def test_confirmation_cancelled_by_stop(self):
        p = self.level["price"]
        self.order(qty=20, super={"sl": p - 10}, confirm=True)
        a = self.r.s["armed"][0]
        trade.QUOTES[self.key] = {"ltp": p - 11, "t": __import__("time").time()}
        self.r.candles15 = lambda k: []
        self.r.tick()
        self.assertEqual(a["status"], "invalid")

    def test_locked_in_market_hours(self):
        self.assertIn("can't be changed", self.r.update_settings({"on": False}))
        self.clock.t = datetime(2026, 11, 18, 16, 0, tzinfo=IST)
        self.assertEqual(self.r.update_settings({"on": False, "risk_pct": 0.5}), "")
        self.assertFalse(self.r.cfg()["on"])

    def test_journal_and_streak(self):
        self.clock.t = datetime(2026, 11, 18, 16, 0, tzinfo=IST)
        self.assertIn("Say whether", self.r.save_journal("2026-11-18", {}))
        for d in ("2026-11-14", "2026-11-17", "2026-11-18"):     # Fri, Tue, Wed: Monday missing
            self.r.save_journal(d, {"followed": "yes", "fix": "wait for 9:30"})
        self.assertEqual(self.r.streak(), 2)
        self.clock.t = datetime(2026, 11, 19, 9, 5, tzinfo=IST)
        plan = self.r.plan([], None, [], {"max_trades_day": 5, "daily_loss_limit": 3000}, -500)
        self.assertEqual(plan["fix_today"], "wait for 9:30")
        self.assertEqual(plan["loss_left"], 2500)


if __name__ == "__main__":
    unittest.main()
