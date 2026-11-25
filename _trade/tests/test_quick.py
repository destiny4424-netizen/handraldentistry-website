"""Quick option trade: strike and expiry choice, and the stop-loss placed after the fill.
    cd _trade && python3 -m unittest discover tests
"""
import os
import sys
import tempfile
import unittest
from datetime import date

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
os.environ.setdefault("MT_DATA_DIR", tempfile.mkdtemp())

import quick                                             # noqa: E402


class Picks(unittest.TestCase):
    def test_strike_one_out_of_the_money(self):
        strikes = [1340, 1360, 1380, 1400, 1420]
        self.assertEqual(quick.pick_strike(strikes, 1386, "up"), (1380, 1400))     # call: one above the ATM
        self.assertEqual(quick.pick_strike(strikes, 1386, "down"), (1380, 1360))   # put: one below
        self.assertEqual(quick.pick_strike(strikes, 1395, "up"), (1400, 1420))
        self.assertEqual(quick.pick_strike(strikes, 1421, "up"), (1420, None))     # no strike further out

    def test_expiry_skips_the_last_day(self):
        ex = ["2026-11-24", "2026-12-29", "2027-01-26"]
        self.assertEqual(quick.pick_expiry(ex, date(2026, 11, 10)), "2026-11-24")
        self.assertEqual(quick.pick_expiry(ex, date(2026, 11, 23)), "2026-12-29")  # 1 day to go: next month
        self.assertEqual(quick.pick_expiry(ex, date(2026, 11, 24)), "2026-12-29")  # expiry day: next month

    def test_prices(self):
        self.assertEqual(quick.default_stop(24.0, 0.05), 16.8)
        self.assertEqual(quick.sl_limit(16.8, 0.05), 15.95)                         # 5% under the trigger
        self.assertEqual(quick.sl_limit(0.5, 0.05), 0.4)                            # at least 2 ticks under
        m = quick.money(50000, 24.0, 1000, 16.8)
        self.assertEqual((m["used"], m["remaining"], m["risk"]), (24000.0, 26000.0, 7200.0))


class ExpiryDay(unittest.TestCase):
    def test_expiry_gone_after_the_close(self):
        from datetime import datetime
        import instruments
        inst = instruments.Instruments.__new__(instruments.Instruments)
        inst.opts = {"RELIANCE": {"2026-11-24": {}, "2026-12-29": {}}}
        at = lambda h, m: datetime(2026, 11, 24, h, m, tzinfo=instruments.IST)
        self.assertEqual(inst.chain_info("RELIANCE", at(10, 0))[0], "2026-11-24")   # still trading
        self.assertEqual(inst.chain_info("RELIANCE", at(18, 51)), ["2026-12-29"])    # evening of expiry day
        self.assertEqual(inst.chain_info("RELIANCE", datetime(2026, 11, 25, 7, 0, tzinfo=instruments.IST)), ["2026-12-29"])


class FakeDhan:
    def __init__(self):
        self.status, self.filled, self.avg = "PENDING", 0, 24.0
        self.placed, self.modified, self.notes = [], [], []
        self.place_ok = True

    def order(self, oid):
        return {"orderStatus": self.status, "filledQty": self.filled, "averageTradedPrice": self.avg}

    def place(self, b):
        self.placed.append(b)
        return (200, {"ok": True, "orders": [{"orderId": f"SL{len(self.placed)}"}]}) if self.place_ok else (400, {"error": "RMS reject"})

    def modify(self, oid, m):
        self.modified.append((oid, m))


class WatcherTest(unittest.TestCase):
    def make(self):
        d = FakeDhan()
        w = quick.Watcher(tempfile.mkdtemp(), d.order, d.place, d.modify, notify=lambda t, b: d.notes.append(t))
        w.add("B1", "NSE_FNO:123", "RELIANCE 1400 CE", 1000, 24.0, 16.8, "INTRADAY", 0.05)
        return d, w

    def test_no_stop_before_the_fill(self):
        d, w = self.make()
        w.check()
        self.assertEqual(d.placed, [])
        self.assertEqual(w.items[0]["status"], "waiting")

    def test_full_fill_places_the_stop(self):
        d, w = self.make()
        d.status, d.filled = "TRADED", 1000
        w.check()
        self.assertEqual(len(d.placed), 1)
        sl = d.placed[0]
        self.assertEqual((sl["side"], sl["qty"], sl["type"], sl["trigger"], sl["price"]), ("SELL", 1000, "STOP_LOSS", 16.8, 15.95))
        self.assertEqual(w.items[0]["status"], "done")
        w.check()                                          # nothing more once done
        self.assertEqual(len(d.placed), 1)
        self.assertTrue(any("stop-loss placed" in n for n in d.notes))

    def test_partial_fill_then_rest(self):
        d, w = self.make()
        d.status, d.filled = "PART_TRADED", 400
        w.check()
        self.assertEqual(d.placed[0]["qty"], 400)
        self.assertEqual(w.items[0]["status"], "partial")
        d.status, d.filled = "TRADED", 1000
        w.check()
        self.assertEqual(len(d.placed), 1)                 # resized, not a second stop
        self.assertEqual(d.modified[0][0], "SL1")
        self.assertEqual(d.modified[0][1]["quantity"], 1000)
        self.assertEqual(w.items[0]["status"], "done")

    def test_rejected_buy_is_dropped(self):
        d, w = self.make()
        d.status = "REJECTED"
        w.check()
        self.assertEqual(d.placed, [])
        self.assertEqual(w.items[0]["status"], "dropped")

    def test_failed_stop_is_retried_then_flagged(self):
        d, w = self.make()
        d.status, d.filled, d.place_ok = "TRADED", 1000, False
        for _ in range(7):
            w.check()
        self.assertEqual(len(d.placed), 5)                 # 5 tries, then it stops and says so
        self.assertEqual(w.items[0]["status"], "no_stop")
        self.assertEqual(sum("NO stop-loss" in n for n in d.notes), 2)   # first failure and last try only

    def test_saved_across_restarts(self):
        d, w = self.make()
        w2 = quick.Watcher(os.path.dirname(w.path), d.order, d.place, d.modify)
        self.assertEqual(w2.items[0]["order_id"], "B1")


class WithPretendDhan(unittest.TestCase):
    """The whole flow against the pretend Dhan: plan, limit buy, fill, stop-loss order at Dhan."""
    @classmethod
    def setUpClass(cls):
        sys.path.insert(0, HERE)
        import test_core                                  # starts the pretend Dhan and loads instruments
        cls.trade, cls.mock = test_core.trade, test_core.mockdhan

    def test_flow(self):
        trade, mock = self.trade, self.mock
        trade.RECENT.clear()
        trade.SETTINGS["limits"] = {}
        if trade.RULES:
            trade.RULES.cfg["on"] = False
        trade.QUICK = quick.Watcher(tempfile.mkdtemp(), trade.D.order, trade.place, trade.D.modify_order)
        code, p = trade.quick_plan("RELIANCE", "up")
        self.assertEqual(code, 200, p)
        self.assertEqual(p["kind"], "CE")
        self.assertGreater(p["strike"], p["atm"])
        self.assertTrue(p["price"] > 0 and 0 < p["stop"] < p["price"])
        code, pd = trade.quick_plan("RELIANCE", "down")
        self.assertEqual((pd["kind"], pd["strike"] < pd["atm"]), ("PE", True))
        # a limit price well above the market fills at once in the pretend Dhan
        code, r = trade.quick_order({"key": p["key"], "lots": 1, "price": round(p["price"] * 1.5, 1), "stop": p["stop"]})
        self.assertEqual(code, 200, r)
        self.assertIn("money", r)
        buy = [c for c in mock.M.calls if c[1] == "/orders"][-1]
        self.assertEqual((buy[2]["orderType"], buy[2]["transactionType"]), ("LIMIT", "BUY"))
        trade.QUICK.check()
        it = trade.QUICK.items[-1]
        self.assertEqual(it["status"], "done", it["note"])
        sl = [c for c in mock.M.calls if c[1] == "/orders"][-1][2]
        self.assertEqual((sl["transactionType"], sl["orderType"], sl["quantity"]), ("SELL", "STOP_LOSS", p["lot"]))
        self.assertAlmostEqual(sl["triggerPrice"], p["stop"])
        # a stop above the price is refused before anything is sent
        code, r = trade.quick_order({"key": p["key"], "lots": 1, "price": p["price"], "stop": p["price"] + 1})
        self.assertEqual(code, 400)


if __name__ == "__main__":
    unittest.main()
