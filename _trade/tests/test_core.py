"""Unit tests: order checks, the requests sent to Dhan, and the insights maths.
    cd _trade && python3 -m unittest discover tests
"""
import os
import sys
import tempfile
import time
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
os.environ["MT_DATA_DIR"] = tempfile.mkdtemp()

import mockdhan                                          # noqa: E402

PORT = 3097
mockdhan.serve(PORT, tick_every=3600)
os.environ["MT_DHAN_API"] = f"http://127.0.0.1:{PORT}/v2"
os.environ["MT_MASTER_URL"] = f"http://127.0.0.1:{PORT}/scrip-master.csv"
import trade                                             # noqa: E402
import insights                                          # noqa: E402
import dhan                                              # noqa: E402

trade.D.client_id, trade.D.token = mockdhan.CLIENT, mockdhan.TOKEN
trade.INST.load(force=True)


def key_of(sym, kind="EQUITY", seg="NSE_EQ"):
    return next(k for k, i in trade.INST.by_key.items() if i["sym"] == sym and i["kind"] == kind and i["seg"] == seg)


class Instruments(unittest.TestCase):
    def test_search(self):
        r = trade.INST.search("reliance")
        self.assertEqual(r[0]["sym"], "RELIANCE")
        r = trade.INST.search("nifty 26000 ce")
        self.assertTrue(r and all(x["opt"] == "CE" and x["strike"] == 26000 and x["ul"] == "NIFTY" for x in r))
        r = trade.INST.search("sbin fut")
        self.assertTrue(r and all(x["kind"] == "FUTSTK" for x in r))

    def test_tick_in_paise(self):
        self.assertEqual(trade.INST.get(key_of("RELIANCE"))["tick"], 0.05)

    def test_chain(self):
        c = trade.chain("NIFTY", "")
        self.assertTrue(c["rows"] and c["spot"] > 0 and c["lot"] == 75)
        self.assertTrue(all(r["CE"]["key"] for r in c["rows"]))


class Orders(unittest.TestCase):
    def setUp(self):
        trade.RECENT.clear()
        trade.SETTINGS["limits"] = {}
        mockdhan.M.calls.clear()

    def last_call(self, path):
        return [c for c in mockdhan.M.calls if c[1] == path][-1]

    def test_lot_multiple(self):
        k = trade.INST.search("nifty 26000 ce")[0]["key"]
        code, r = trade.place({"key": k, "side": "BUY", "qty": 70, "product": "INTRADAY", "type": "MARKET"})
        self.assertEqual(code, 400)
        self.assertIn("lot size", r["error"])

    def test_index_not_tradable(self):
        code, r = trade.place({"key": "IDX_I:13", "side": "BUY", "qty": 1, "product": "INTRADAY", "type": "MARKET"})
        self.assertEqual(code, 400)

    def test_wrong_product(self):
        k = trade.INST.search("nifty 26000 ce")[0]["key"]
        code, r = trade.place({"key": k, "side": "BUY", "qty": 75, "product": "CNC", "type": "MARKET"})
        self.assertEqual(code, 400)

    def test_market_order_body(self):
        code, r = trade.place({"key": key_of("SBIN"), "side": "BUY", "qty": 3, "product": "CNC", "type": "MARKET"})
        self.assertEqual(code, 200, r)
        m, p, b = self.last_call("/orders")
        self.assertEqual(m, "POST")
        self.assertEqual(b["dhanClientId"], mockdhan.CLIENT)
        self.assertEqual((b["transactionType"], b["exchangeSegment"], b["productType"], b["orderType"], b["validity"]),
                         ("BUY", "NSE_EQ", "CNC", "MARKET", "DAY"))
        self.assertEqual((b["securityId"], b["quantity"], b["price"], b["triggerPrice"]), ("3045", 3, 0.0, 0.0))
        self.assertNotIn("amoTime", b)

    def test_limit_rounded_to_tick(self):
        code, r = trade.place({"key": key_of("ITC"), "side": "BUY", "qty": 1, "product": "INTRADAY", "type": "LIMIT",
                               "price": 400.03})
        self.assertEqual(code, 200, r)
        self.assertEqual(self.last_call("/orders")[2]["price"], 400.05)

    def test_double_tap(self):
        o = {"key": key_of("INFY"), "side": "BUY", "qty": 1, "product": "INTRADAY", "type": "LIMIT", "price": 1000}
        self.assertEqual(trade.place(o)[0], 200)
        self.assertEqual(trade.place(o)[0], 409)

    def test_stop_loss_checks(self):
        code, r = trade.place({"key": key_of("TCS"), "side": "SELL", "qty": 1, "product": "INTRADAY", "type": "STOP_LOSS",
                               "price": 3400, "trigger": 3390})
        self.assertEqual(code, 400)
        code, r = trade.place({"key": key_of("TCS"), "side": "SELL", "qty": 1, "product": "INTRADAY", "type": "STOP_LOSS",
                               "price": 3390, "trigger": 3400})
        self.assertEqual(code, 200, r)
        b = self.last_call("/orders")[2]
        self.assertEqual((b["price"], b["triggerPrice"]), (3390.0, 3400.0))

    def test_super_order(self):
        k = key_of("WIPRO")
        code, r = trade.place({"key": k, "side": "BUY", "qty": 10, "product": "INTRADAY", "type": "LIMIT", "price": 240,
                               "super": {"target": 230, "sl": 235}})
        self.assertEqual(code, 400)                     # target below a buy entry
        code, r = trade.place({"key": k, "side": "BUY", "qty": 10, "product": "INTRADAY", "type": "LIMIT", "price": 240,
                               "super": {"target": 250, "sl": 235, "trail": 1}})
        self.assertEqual(code, 200, r)
        b = self.last_call("/super/orders")[2]
        self.assertEqual((b["price"], b["targetPrice"], b["stopLossPrice"], b["trailingJump"]), (240.0, 250.0, 235.0, 1.0))
        self.assertNotIn("validity", b)

    def test_big_order_needs_ok(self):
        trade.SETTINGS["limits"] = {"max_order_value": 10000}
        o = {"key": key_of("MARUTI"), "side": "BUY", "qty": 2, "product": "CNC", "type": "LIMIT", "price": 12000}
        code, r = trade.place(o)
        self.assertEqual(code, 400)
        self.assertTrue(r["needs_big_ok"])
        self.assertEqual(trade.place(dict(o, big_ok=True))[0], 200)

    def test_daily_loss_limit_blocks_new_but_allows_exit(self):
        k = key_of("ZOMATO")
        self.assertEqual(trade.place({"key": k, "side": "BUY", "qty": 100, "product": "INTRADAY", "type": "MARKET"})[0], 200)
        inst = mockdhan.M.inst[("NSE_EQ", "5097")]
        inst["px"] *= 0.5                               # the price halves: a big loss
        trade.SETTINGS["limits"] = {"daily_loss_limit": 1000}
        code, r = trade.place({"key": k, "side": "BUY", "qty": 1, "product": "INTRADAY", "type": "MARKET"})
        self.assertEqual(code, 400)
        self.assertIn("loss limit", r["error"])
        code, r = trade.place({"key": k, "side": "SELL", "qty": 100, "product": "INTRADAY", "type": "MARKET"})
        self.assertEqual(code, 200, r)

    def test_dhan_error_is_shown(self):
        mockdhan.M.kill = True
        try:
            code, r = trade.place({"key": key_of("LT"), "side": "BUY", "qty": 1, "product": "INTRADAY", "type": "MARKET"})
        finally:
            mockdhan.M.kill = False
        self.assertEqual(code, 400)
        self.assertIn("Kill switch", r["error"])

    def test_modify_and_cancel(self):
        code, r = trade.place({"key": key_of("AXISBANK"), "side": "BUY", "qty": 1, "product": "INTRADAY", "type": "LIMIT",
                               "price": 900})
        oid = r["orders"][0]["orderId"]
        trade.D.modify_order(oid, {"orderType": "LIMIT", "quantity": 2, "price": 901, "validity": "DAY"})
        b = self.last_call(f"/orders/{oid}")[2]
        self.assertEqual((b["orderId"], b["quantity"], b["price"]), (oid, 2, 901.0))
        trade.D.cancel_order(oid)
        self.assertEqual(self.last_call(f"/orders/{oid}")[0], "DELETE")

    def test_bad_token(self):
        d = dhan.Dhan("x", "wrong-token-wrong-token", trade.API_BASE)
        with self.assertRaises(dhan.DhanError) as e:
            d.funds()
        self.assertEqual(e.exception.code, "DH-901")


def fill(t, side, qty, price, sid="1", kind="OPTIDX", product="INTRADAY", seg="NSE_FNO"):
    return {"uid": f"{t}{side}{qty}{price}{sid}", "order_id": "", "time": t, "date": t[:10], "seg": seg, "sid": sid,
            "sym": "X" + sid, "kind": kind, "product": product, "side": side, "qty": qty, "price": price,
            "charges": 10.0, "est": False, "expiry": "", "strike": 0, "opt": "CE", "source": "history"}


class Insights(unittest.TestCase):
    def test_round_trip(self):
        closed, open_ = insights.round_trips([fill("2026-11-02 09:20:00", "BUY", 75, 100),
                                              fill("2026-11-02 09:40:00", "SELL", 75, 110)])
        self.assertEqual(len(closed), 1)
        t = closed[0]
        self.assertEqual((t["gross"], t["charges"], t["net"], t["held_min"], t["side"]), (750.0, 20.0, 730.0, 20.0, "LONG"))

    def test_scale_in_and_average_down(self):
        closed, _ = insights.round_trips([fill("2026-11-02 09:20:00", "BUY", 75, 100),
                                          fill("2026-11-02 09:25:00", "BUY", 75, 90),
                                          fill("2026-11-02 09:50:00", "SELL", 150, 92)])
        self.assertEqual(len(closed), 1)
        self.assertTrue(closed[0]["avg_down"])
        self.assertEqual(closed[0]["gross"], 150 * 92 - 75 * 100 - 75 * 90)

    def test_flip(self):
        closed, open_ = insights.round_trips([fill("2026-11-02 09:20:00", "BUY", 10, 100, kind="FUTSTK"),
                                              fill("2026-11-02 09:30:00", "SELL", 30, 105, kind="FUTSTK"),
                                              fill("2026-11-02 10:00:00", "BUY", 20, 100, kind="FUTSTK")])
        self.assertEqual(len(closed), 2)
        self.assertEqual(closed[0]["gross"], 50.0)
        self.assertEqual(closed[1]["side"], "SHORT")
        self.assertEqual(closed[1]["gross"], 100.0)

    def test_old_holdings_sold(self):
        closed, open_ = insights.round_trips([fill("2026-11-02 10:00:00", "SELL", 5, 100, kind="EQUITY",
                                                   product="CNC", seg="NSE_EQ")])
        self.assertEqual((closed, open_), ([], []))

    def test_revenge_habit(self):
        fs = []
        for d in range(1, 9):
            day = f"2026-10-{d:02d}"
            fs += [fill(f"{day} 10:00:00", "BUY", 75, 100, sid=str(d)), fill(f"{day} 10:10:00", "SELL", 75, 90, sid=str(d)),
                   fill(f"{day} 10:15:00", "BUY", 75, 100, sid=f"r{d}"), fill(f"{day} 10:30:00", "SELL", 75, 95, sid=f"r{d}")]
        closed, _ = insights.round_trips(fs)
        keys = [h["key"] for h in insights.habits(closed)]
        self.assertIn("revenge", keys)

    def test_store_dedupe(self):
        s = insights.Store(os.path.join(tempfile.mkdtemp(), "t.db"))
        day = [{"orderId": "1", "exchangeTradeId": "9", "transactionType": "BUY", "exchangeSegment": "NSE_EQ",
                "productType": "INTRADAY", "securityId": "1", "tradingSymbol": "A", "tradedQuantity": 1,
                "tradedPrice": 10, "exchangeTime": "2026-11-02 10:00:00"}]
        s.put_day(day, "2026-11-02")
        s.put_day(day, "2026-11-02")
        self.assertEqual(s.count()[0], 1)
        s.put_history([dict(day[0], brokerageCharges=20, stt=0.1)])
        self.assertEqual(s.count()[0], 1)
        self.assertFalse(s.fills()[0]["est"])
        self.assertAlmostEqual(s.fills()[0]["charges"], 20.1)

    def test_full_report_on_demo_history(self):
        trade.sync(full=True)
        time.sleep(0.1)
        r = insights.report(trade.STORE.fills())
        self.assertGreater(r["summary"]["trades"], 50)
        self.assertTrue(r["habits"])


if __name__ == "__main__":
    unittest.main()
