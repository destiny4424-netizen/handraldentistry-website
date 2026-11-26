"""Trailing the stop-loss from Positions: finding the stop, moving it, adding one, refusing a wrong-side stop.
    cd _trade && python3 -m unittest discover tests
"""
import os
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
os.environ.setdefault("MT_DATA_DIR", tempfile.mkdtemp())

import mockdhan                                          # noqa: E402

PORT = int(os.environ.setdefault("MT_TEST_PORT", "3097"))
if mockdhan.M is None:
    mockdhan.serve(PORT, tick_every=3600)
os.environ.setdefault("MT_DHAN_API", f"http://127.0.0.1:{PORT}/v2")
os.environ.setdefault("MT_MASTER_URL", f"http://127.0.0.1:{PORT}/scrip-master.csv")
import trade                                             # noqa: E402

trade.D.client_id, trade.D.token = mockdhan.CLIENT, mockdhan.TOKEN
if not trade.INST.by_key:
    trade.INST.load(force=True)


def key_of(sym):
    return next(k for k, i in trade.INST.by_key.items() if i["sym"] == sym and i["seg"] == "NSE_EQ")


class Trail(unittest.TestCase):
    def setUp(self):
        trade.RECENT.clear()
        trade.SETTINGS["limits"] = {"max_order_value": 0}
        trade.RULES = trade.BOT = None

    def pin(self, sym, px):
        i = mockdhan.M.inst[("NSE_EQ", key_of(sym).split(":")[1])]
        i["px"], i["pinned"] = px, True
        trade.QUOTES[key_of(sym)] = {"ltp": px, "t": 0}
        mockdhan.M.match()

    def position(self, sym):
        pos = trade.attach_stops(trade.positions_view(trade.get_positions())[0])
        return next(p for p in pos if p["_key"] == key_of(sym) and int(p.get("netQty") or 0))

    def test_move_existing_stop(self):
        k = key_of("HINDUNILVR")
        self.pin("HINDUNILVR", 2400)
        self.assertEqual(trade.place({"key": k, "side": "BUY", "qty": 10, "product": "INTRADAY", "type": "MARKET"})[0], 200)
        trade.RECENT.clear()
        self.assertEqual(trade.place({"key": k, "side": "SELL", "qty": 10, "product": "INTRADAY", "type": "STOP_LOSS_MARKET",
                                      "trigger": 2350})[0], 200)
        p = self.position("HINDUNILVR")
        st = p["_stops"][0]
        self.assertEqual((st["kind"], st["trigger"], st["qty"]), ("order", 2350.0, 10))
        code, r = trade.move_stop({"key": k, "order_id": st["order_id"], "kind": "order", "trigger": 2375.03})
        self.assertEqual(code, 200, r)
        self.assertEqual(r["trigger"], 2375.05)                              # rounded to the tick
        self.assertEqual(self.position("HINDUNILVR")["_stops"][0]["trigger"], 2375.05)
        code, r = trade.move_stop({"key": k, "order_id": st["order_id"], "kind": "order", "trigger": 2401})
        self.assertEqual(code, 400)                                          # above the price: would sell at once
        self.assertIn("below the price", r["error"])

    def test_add_stop_when_none(self):
        k = key_of("SUNPHARMA")
        self.pin("SUNPHARMA", 1760)
        self.assertEqual(trade.place({"key": k, "side": "SELL", "qty": 5, "product": "INTRADAY", "type": "MARKET"})[0], 200)
        p = self.position("SUNPHARMA")
        self.assertEqual(p["_stops"], [])
        code, r = trade.move_stop({"key": k, "trigger": 1700})
        self.assertEqual(code, 400)                                          # a short's stop must be above the price
        trade.RECENT.clear()
        code, r = trade.move_stop({"key": k, "trigger": 1790})
        self.assertEqual(code, 200, r)
        st = self.position("SUNPHARMA")["_stops"][0]
        self.assertEqual((st["trigger"], st["qty"], st["type"]), (1790.0, 5, "STOP_LOSS_MARKET"))

    def test_super_order_stop_leg(self):
        k = key_of("LT")
        self.pin("LT", 3600)
        code, r = trade.place({"key": k, "side": "BUY", "qty": 3, "product": "INTRADAY", "type": "LIMIT", "price": 3600,
                               "super": {"target": 3700, "sl": 3550}})
        self.assertEqual(code, 200, r)
        p = self.position("LT")
        st = next(s for s in p["_stops"] if s["kind"] == "super")
        self.assertEqual(st["trigger"], 3550.0)
        code, r = trade.move_stop({"key": k, "order_id": st["order_id"], "kind": "super", "trigger": 3580})
        self.assertEqual(code, 200, r)
        st2 = next(s for s in self.position("LT")["_stops"] if s["kind"] == "super")
        self.assertEqual(st2["trigger"], 3580.0)


    def option(self, px, q="nifty 26000 ce"):
        it = trade.INST.search(q)[0]
        i = mockdhan.M.inst[(it["seg"], str(it["id"]))]
        i["px"], i["pinned"] = px, True
        trade.QUOTES.pop(it["key"], None)
        mockdhan.M.match()
        return it

    def opt_position(self, key):
        pos = trade.attach_stops(trade.positions_view(trade.get_positions())[0])
        return next(p for p in pos if p["_key"] == key and int(p.get("netQty") or 0))

    def test_option_stop_is_a_stop_limit(self):
        it = self.option(1012.0)
        k, lot = it["key"], it["lot"]
        self.assertEqual(trade.place({"key": k, "side": "BUY", "qty": lot, "product": "INTRADAY", "type": "MARKET"})[0], 200)
        trade.RECENT.clear()
        code, r = trade.move_stop({"key": k, "trigger": 1003})
        self.assertEqual(code, 200, r)
        st = self.opt_position(k)["_stops"][0]
        self.assertEqual((st["type"], st["trigger"]), ("STOP_LOSS", 1003.0))
        self.assertEqual(st["price"], 992.95)                                  # 1% under the trigger, on the tick
        code, r = trade.move_stop({"key": k, "order_id": st["order_id"], "kind": "order", "trigger": 1005})
        self.assertEqual(code, 200, r)
        st = self.opt_position(k)["_stops"][0]
        self.assertEqual((st["trigger"], st["price"]), (1005.0, 994.95))       # moved, same gap
        trade.RECENT.clear()
        self.assertEqual(trade.place({"key": k, "side": "SELL", "qty": lot, "product": "INTRADAY", "type": "MARKET"})[0], 200)

    def test_old_option_sl_m_becomes_a_stop_limit_when_moved(self):
        it = self.option(500.0, "nifty 25900 ce")
        k, lot = it["key"], it["lot"]
        self.assertEqual(trade.place({"key": k, "side": "BUY", "qty": lot, "product": "INTRADAY", "type": "MARKET"})[0], 200)
        trade.RECENT.clear()
        self.assertEqual(trade.place({"key": k, "side": "SELL", "qty": lot, "product": "INTRADAY", "type": "STOP_LOSS_MARKET",
                                      "trigger": 480})[0], 200)
        st = self.opt_position(k)["_stops"][0]
        self.assertEqual(st["type"], "STOP_LOSS_MARKET")
        code, r = trade.move_stop({"key": k, "order_id": st["order_id"], "kind": "order", "trigger": 485})
        self.assertEqual(code, 200, r)
        st = self.opt_position(k)["_stops"][0]
        self.assertEqual((st["type"], st["trigger"], st["price"]), ("STOP_LOSS", 485.0, 480.15))
        trade.RECENT.clear()
        self.assertEqual(trade.place({"key": k, "side": "SELL", "qty": lot, "product": "INTRADAY", "type": "MARKET"})[0], 200)

    def test_no_live_price_no_stop(self):
        k = key_of("ITC")
        self.pin("ITC", 415)
        self.assertEqual(trade.place({"key": k, "side": "BUY", "qty": 10, "product": "INTRADAY", "type": "MARKET"})[0], 200)
        trade.QUOTES.pop(k, None)
        real = trade.D.quote

        def down(*a, **kw):
            raise trade.dhanlib.DhanError("feed down")
        trade.D.quote = down
        try:
            code, r = trade.move_stop({"key": k, "trigger": 400})
        finally:
            trade.D.quote = real
        self.assertEqual(code, 400)
        self.assertIn("Couldn't get the live price", r["error"])
        self.assertEqual(self.position("ITC")["_stops"], [])                 # nothing was sent


if __name__ == "__main__":
    unittest.main()
