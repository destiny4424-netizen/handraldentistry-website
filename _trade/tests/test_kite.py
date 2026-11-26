"""The Zerodha copy of the desk: Kite Connect adapter (kite.py), Zerodha's instrument list, its live stream and the
super orders the adapter runs itself. Against the pretend Zerodha (mockkite.py), never a real account.
    cd _trade && python3 -m unittest tests.test_kite
"""
import os
import struct
import sys
import tempfile
import time
import unittest
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))

import kite                                              # noqa: E402
import kitestream                                        # noqa: E402
import mockkite                                          # noqa: E402
from dhan import DhanError                               # noqa: E402
from instruments import KiteInstruments                  # noqa: E402

KPORT = int(os.environ.get("MT_TEST_KITE_PORT", "3093"))
if mockkite.M is None:
    mockkite.serve(KPORT, tick_every=3600)
BASE = f"http://127.0.0.1:{KPORT}"
DATA = tempfile.mkdtemp()
INST = KiteInstruments(DATA, BASE + "/instruments")
INST.load(force=True)


def client():
    k = kite.Kite(mockkite.API_KEY, mockkite.TOKEN, BASE, inst=INST, data_dir=tempfile.mkdtemp())
    k.client_id = mockkite.USER
    return k


def key(sym, kind="EQUITY", seg="NSE_EQ"):
    return next(k for k, i in INST.by_key.items() if i["sym"] == sym and i["kind"] == kind and i["seg"] == seg)


def pin(k, px, pinned=True):
    body = ('{"token": "%s", "px": %s, "pinned": %s}' % (k.split(":")[1], px, "true" if pinned else "false")).encode()
    urllib.request.urlopen(urllib.request.Request(BASE + "/mock/pin", data=body, method="POST"), timeout=5).read()


def flat(k):
    """Close whatever the pretend market holds, so each test starts flat."""
    for p in k.positions():
        if p["netQty"]:
            k.place_order({"securityId": p["securityId"], "exchangeSegment": p["exchangeSegment"], "quantity": abs(p["netQty"]),
                           "transactionType": "SELL" if p["netQty"] > 0 else "BUY", "orderType": "MARKET",
                           "productType": p["productType"]})
    for o in k._raw_orders():
        if kite.status_out(o) in kite.WORKING:
            k.cancel_order(o["order_id"])


class Instruments(unittest.TestCase):
    def test_kinds_and_keys(self):
        it = INST.get(INST.under["RELIANCE"])
        self.assertEqual((it["seg"], it["kind"], it["ts"], it["kx"]), ("NSE_EQ", "EQUITY", "RELIANCE", "NSE"))
        self.assertEqual(INST.get(INST.under["NIFTY"])["kind"], "INDEX")    # "NIFTY 50" -> NIFTY
        self.assertEqual(INST.get(INST.under["BANKNIFTY"])["ts"], "NIFTY BANK")
        self.assertEqual(INST.get(INST.under["SENSEX"])["exch"], "BSE")
        exps = INST.chain_info("NIFTY")
        self.assertTrue(exps)
        opts = INST.chain_keys("NIFTY", exps[0])
        o = INST.get(next(iter(opts.values())))
        self.assertEqual((o["kind"], o["seg"], o["ul"]), ("OPTIDX", "NSE_FNO", "NIFTY"))
        self.assertIn("CALL" if o["opt"] == "CE" else "PUT", o["name"])
        f = INST.get(INST.futs["RELIANCE"][0][1])
        self.assertEqual(f["kind"], "FUTSTK")
        self.assertGreater(f["lot"], 1)
        gold = next(i for i in INST.by_key.values() if i["seg"] == "MCX_COMM")
        self.assertEqual((gold["kind"], gold["lot"], gold["mult"]), ("FUTCOM", 1, 100))    # quantity in lots, as on Dhan
        self.assertEqual(gold["tick"], 1.0)                                                  # rupees, not paise
        self.assertEqual(INST.tok[it["id"]], it["key"])

    def test_search(self):
        r = INST.search("nifty 26000 ce")
        self.assertTrue(r and all(x["opt"] == "CE" and x["strike"] == 26000 and x["ul"] == "NIFTY" for x in r))
        self.assertTrue(all(x["kind"] == "FUTSTK" for x in INST.search("sbin fut")))

    def test_skips_non_equity_series(self):
        path = os.path.join(tempfile.mkdtemp(), "kite-instruments.csv")
        with open(path, "w") as f:
            f.write("instrument_token,exchange_token,tradingsymbol,name,last_price,expiry,strike,tick_size,lot_size,"
                    "instrument_type,segment,exchange\n"
                    "1,1,ABC,ABC LTD,0,,0,0.05,1,EQ,NSE,NSE\n2,2,ABC-BE,ABC LTD,0,,0,0.05,1,EQ,NSE,NSE\n"
                    "3,3,SGBX-GB,SGB,0,,0,0.01,1,EQ,NSE,NSE\n4,4,ABC24JANFUT,ABC,0,2020-01-25,0,0.05,500,FUT,NFO-FUT,NFO\n")
        x = KiteInstruments(os.path.dirname(path), "http://unused")
        x._parse()
        self.assertEqual(sorted(i["ts"] for i in x.by_key.values()), ["ABC", "ABC-BE"])     # no bond, no expired future


class Adapter(unittest.TestCase):
    def setUp(self):
        self.k = client()
        flat(self.k)

    def test_login_exchange(self):
        k = kite.Kite(mockkite.API_KEY, "", BASE, inst=INST)
        k.api_secret = mockkite.API_SECRET
        s = k.exchange(mockkite.REQUEST)
        self.assertEqual((s["access_token"], s["user_id"]), (mockkite.TOKEN, mockkite.USER))
        with self.assertRaises(DhanError):
            k.exchange("someone-elses-token")
        self.assertIn("redirect_params=state%3Dabc", k.login_url("abc"))
        self.assertIn("api_key=" + mockkite.API_KEY, k.login_url())

    def test_expired_login(self):
        k = kite.Kite(mockkite.API_KEY, "old-token", BASE, inst=INST)
        k.client_id = "X"
        with self.assertRaises(DhanError) as e:
            k.funds()
        self.assertEqual(e.exception.code, "DH-901")
        self.assertIn("log in", str(e.exception).lower())
        self.assertEqual(k.token, "")                  # the app then shows "not logged in"

    def test_token_expiry_6am(self):
        from datetime import datetime
        at = datetime(2026, 11, 26, 9, 0, tzinfo=kite.IST).timestamp()
        self.assertEqual(datetime.fromtimestamp(kite.token_expiry(at), kite.IST).strftime("%d %H:%M"), "27 06:00")
        early = datetime(2026, 11, 26, 5, 0, tzinfo=kite.IST).timestamp()
        self.assertEqual(datetime.fromtimestamp(kite.token_expiry(early), kite.IST).strftime("%d %H:%M"), "26 06:00")

    def test_order_round_trip(self):
        k, rk = self.k, key("RELIANCE")
        sid = rk.split(":")[1]
        mockkite.CALLS.clear()
        r = k.place_order({"securityId": sid, "exchangeSegment": "NSE_EQ", "transactionType": "BUY", "quantity": 3,
                           "orderType": "LIMIT", "productType": "INTRADAY", "price": 100.0, "validity": "DAY",
                           "correlationId": "abc-123"})
        method, path, p = mockkite.CALLS[-1]
        self.assertEqual((method, path), ("POST", "/orders/regular"))
        self.assertEqual((p["tradingsymbol"], p["exchange"], p["product"], p["order_type"], p["tag"]),
                         ("RELIANCE", "NSE", "MIS", "LIMIT", "abc123"))
        o = k.order(r["orderId"])
        self.assertEqual((o["orderStatus"], o["exchangeSegment"], o["securityId"], o["productType"], o["orderType"]),
                         ("PENDING", "NSE_EQ", sid, "INTRADAY", "LIMIT"))
        k.modify_order(r["orderId"], {"orderType": "STOP_LOSS", "quantity": 3, "price": 90, "triggerPrice": 91})
        self.assertEqual(mockkite.CALLS[-1][2]["order_type"], "SL")
        self.assertEqual(k.order(r["orderId"])["orderType"], "STOP_LOSS")
        k.cancel_order(r["orderId"])
        self.assertEqual(k.order(r["orderId"])["orderStatus"], "CANCELLED")
        self.assertIn(r["orderId"], [o["orderId"] for o in k.orders()])

    def test_market_protection(self):
        k, sid = self.k, key("SBIN").split(":")[1]
        o = {"securityId": sid, "exchangeSegment": "NSE_EQ", "transactionType": "BUY", "quantity": 1,
             "orderType": "MARKET", "productType": "INTRADAY"}
        k.place_order(o)
        self.assertEqual(mockkite.CALLS[-1][2].get("market_protection"), "-1")
        mockkite.OPTS["market_protection_unknown"] = True            # a Kite that doesn't know the field
        try:
            k.place_order(o)
            self.assertNotIn("market_protection", mockkite.CALLS[-1][2])
            self.assertFalse(k._mp)
        finally:
            mockkite.OPTS["market_protection_unknown"] = False

    def test_positions_trades_funds(self):
        k, rk = self.k, key("INFY")
        sid = rk.split(":")[1]
        pin(rk, 1500)
        try:
            k.place_order({"securityId": sid, "exchangeSegment": "NSE_EQ", "transactionType": "BUY", "quantity": 4,
                           "orderType": "MARKET", "productType": "INTRADAY"})
            pin(rk, 1510)
            p = next(x for x in k.positions() if x["securityId"] == sid)
            self.assertEqual((p["netQty"], p["productType"], p["positionType"]), (4, "INTRADAY", "LONG"))
            self.assertAlmostEqual(p["unrealizedProfit"], 40.0, places=1)
            t = [x for x in k.trades_today() if x["securityId"] == sid][-1]
            self.assertEqual((t["exchangeSegment"], t["tradedQuantity"], t["tradedPrice"], t["productType"]),
                             ("NSE_EQ", 4, 1500.0, "INTRADAY"))
            f = k.funds()
            self.assertGreater(f["availabelBalance"], 0)
            m = k.margin({"securityId": sid, "exchangeSegment": "NSE_EQ", "transactionType": "BUY", "quantity": 10,
                          "orderType": "LIMIT", "productType": "INTRADAY", "price": 1500})
            self.assertGreater(m["totalMargin"], 0)
            self.assertIn("insufficientBalance", m)
        finally:
            pin(rk, 1500, pinned=False)

    def test_quotes_and_candles(self):
        k, rk, nk = self.k, key("TCS"), INST.under["NIFTY"]
        q = k.quote({"NSE_EQ": [rk.split(":")[1]], "IDX_I": [nk.split(":")[1]]}, "quote")
        self.assertEqual(set(q), {("NSE_EQ", rk.split(":")[1]), ("IDX_I", nk.split(":")[1])})
        x = q[("NSE_EQ", rk.split(":")[1])]
        self.assertAlmostEqual(x["net_change"], x["last_price"] - x["ohlc"]["close"], places=2)
        d = k.daily(rk.split(":")[1], "NSE_EQ", "EQUITY", "2026-01-01", "2026-11-20")
        self.assertTrue(d["timestamp"] and len(d["timestamp"]) == len(d["close"]))
        self.assertIsInstance(d["timestamp"][0], int)
        i = k.intraday(rk.split(":")[1], "NSE_EQ", "EQUITY", "15", "2026-11-20 09:15:00", "2026-11-26 15:30:00")
        self.assertEqual(len(i["open"]), 75)
        oi = k.call("POST", "/charts/historical", {"securityId": INST.futs["TCS"][0][1].split(":")[1],
                                                   "exchangeSegment": "NSE_FNO", "instrument": "FUTSTK", "oi": True,
                                                   "fromDate": "2026-11-01", "toDate": "2026-11-20"})
        self.assertEqual(len(oi["open_interest"]), len(oi["close"]))
        with self.assertRaises(DhanError):
            k.option_chain(1, "IDX_I", "2026-12-01")       # trade.py then builds the chain from quotes

    def test_gtt(self):
        k, sid = self.k, key("ITC").split(":")[1]
        r = k.place_forever({"transactionType": "SELL", "exchangeSegment": "NSE_EQ", "productType": "CNC",
                             "orderType": "LIMIT", "securityId": sid, "quantity": 10, "price": 380, "triggerPrice": 385})
        self.assertTrue(r["orderId"])
        g = next(x for x in k.forever_orders() if x["orderId"] == r["orderId"])
        self.assertEqual((g["orderStatus"], g["triggerPrice"], g["securityId"]), ("PENDING", 385, sid))
        k.cancel_forever(r["orderId"])
        self.assertEqual(next(x for x in k.forever_orders() if x["orderId"] == r["orderId"])["orderStatus"], "CANCELLED")

    def test_holdings_convert_exit_all(self):
        k = self.k
        h = k.holdings()
        self.assertEqual({x["tradingSymbol"] for x in h}, {"ITC", "HDFCBANK"})
        self.assertTrue(all(x["totalQty"] and x["avgCostPrice"] and x["securityId"] for x in h))
        rk = key("AXISBANK")
        sid = rk.split(":")[1]
        k.place_order({"securityId": sid, "exchangeSegment": "NSE_EQ", "transactionType": "BUY", "quantity": 2,
                       "orderType": "MARKET", "productType": "INTRADAY"})
        k.convert_position({"fromProductType": "INTRADAY", "toProductType": "CNC", "exchangeSegment": "NSE_EQ",
                            "positionType": "LONG", "securityId": sid, "convertQty": 2})
        self.assertEqual(mockkite.CALLS[-1][2]["new_product"], "CNC")
        self.assertEqual(next(p for p in k.positions() if p["securityId"] == sid)["productType"], "CNC")
        k.place_order({"securityId": sid, "exchangeSegment": "NSE_EQ", "transactionType": "BUY", "quantity": 1,
                       "orderType": "LIMIT", "productType": "INTRADAY", "price": 1.0})
        r = k.exit_all()
        self.assertEqual(r["status"], "SUCCESS")
        self.assertFalse([p for p in k.positions() if p["netQty"]])
        self.assertFalse([o for o in k.orders() if o["orderStatus"] in kite.WORKING])
        self.assertEqual(k.kill_switch_status()["killSwitchStatus"], "NOT_AVAILABLE")
        with self.assertRaises(DhanError):
            k.kill_switch(True)


class SuperOrders(unittest.TestCase):
    def setUp(self):
        self.k = client()
        flat(self.k)
        self.rk = key("HDFCBANK")
        self.sid = self.rk.split(":")[1]
        pin(self.rk, 1700)

    def tearDown(self):
        pin(self.rk, 1700, pinned=False)

    def sup(self, rid):
        return next(s for s in self.k.super_orders() if s["orderId"] == rid)

    def leg(self, rid, name):
        return next(x for x in self.sup(rid)["legDetails"] if x["legName"] == name)

    def place(self, **kw):
        o = {"securityId": self.sid, "exchangeSegment": "NSE_EQ", "transactionType": "BUY", "quantity": 10,
             "orderType": "MARKET", "productType": "INTRADAY", "price": 1700, "targetPrice": 1720, "stopLossPrice": 1690,
             "trailingJump": 0}
        o.update(kw)
        return self.k.place_super_order(o)["orderId"]

    def test_stop_goes_in_after_fill_then_target(self):
        k = self.k
        rid = self.place()
        k.tick()
        s = k.sup[rid]
        self.assertEqual(s["entry_status"], "TRADED")
        sl = k._raw_order(s["sl_order"])
        self.assertEqual((sl["order_type"], sl["transaction_type"], sl["trigger_price"], sl["quantity"]), ("SL-M", "SELL", 1690, 10))
        self.assertNotIn(s["sl_order"], [o["orderId"] for o in k.orders()])      # legs live in the super order, not twice
        self.assertEqual(self.leg(rid, "STOP_LOSS_LEG")["orderStatus"], "PENDING")
        pin(self.rk, 1721)                                                       # target reached
        k.tick()
        k.tick()
        self.assertEqual(self.leg(rid, "TARGET_LEG")["orderStatus"], "TRADED")
        self.assertEqual(self.leg(rid, "STOP_LOSS_LEG")["orderStatus"], "CANCELLED")
        self.assertEqual(self.sup(rid)["orderStatus"], "CLOSED")
        self.assertFalse([p for p in k.positions() if p["securityId"] == self.sid and p["netQty"]])
        self.assertEqual(len([o for o in k._raw_orders() if o["order_id"] == s["sl_order"] and o["status"] == "COMPLETE"]), 1)

    def test_stop_hit(self):
        k = self.k
        rid = self.place()
        k.tick()
        pin(self.rk, 1689)
        time.sleep(0.05)
        k.tick()
        self.assertEqual(self.leg(rid, "STOP_LOSS_LEG")["orderStatus"], "TRADED")
        self.assertEqual(self.leg(rid, "TARGET_LEG")["orderStatus"], "CANCELLED")
        self.assertFalse([p for p in k.positions() if p["securityId"] == self.sid and p["netQty"]])

    def test_trailing_and_moving_the_stop(self):
        k = self.k
        rid = self.place(targetPrice=0, trailingJump=5)
        k.tick()
        pin(self.rk, 1711)                                                       # 2 jumps in our favour
        k.tick()
        self.assertEqual(k._raw_order(k.sup[rid]["sl_order"])["trigger_price"], 1700)
        k.modify_super_leg(rid, "STOP_LOSS_LEG", {"stopLossPrice": 1705, "trailingJump": 0})
        self.assertEqual(k._raw_order(k.sup[rid]["sl_order"])["trigger_price"], 1705)
        k.modify_super_leg(rid, "TARGET_LEG", {"targetPrice": 1710})
        k.tick()
        k.tick()
        self.assertEqual(self.leg(rid, "TARGET_LEG")["orderStatus"], "TRADED")

    def test_option_stop_is_stop_limit(self):
        k = self.k
        ok = next(v for v in INST.chain_keys("NIFTY", INST.chain_info("NIFTY")[0]).values())
        it = INST.get(ok)
        pin(ok, 100)
        try:
            rid = k.place_super_order({"securityId": it["id"], "exchangeSegment": "NSE_FNO", "transactionType": "BUY",
                                       "quantity": it["lot"], "orderType": "MARKET", "productType": "INTRADAY", "price": 100,
                                       "targetPrice": 0, "stopLossPrice": 80, "trailingJump": 0})["orderId"]
            k.tick()
            sl = k._raw_order(k.sup[rid]["sl_order"])
            self.assertEqual((sl["order_type"], sl["trigger_price"], sl["price"]), ("SL", 80, 79.2))
        finally:
            flat(k)
            pin(ok, 100, pinned=False)

    def test_closed_elsewhere_and_cancel(self):
        k = self.k
        rid = self.place()
        k.tick()
        sl = k.sup[rid]["sl_order"]
        k.place_order({"securityId": self.sid, "exchangeSegment": "NSE_EQ", "transactionType": "SELL", "quantity": 10,
                       "orderType": "MARKET", "productType": "INTRADAY"})               # closed by hand in Kite
        k.sup[rid]["filled_at"] = 0
        k.tick()
        self.assertEqual(self.sup(rid)["orderStatus"], "CLOSED")
        self.assertEqual(k._raw_order(sl)["status"], "CANCELLED")                      # its stop can't open a short
        rid2 = self.place(orderType="LIMIT", price=1500)                                  # waits below the market
        k.tick()
        k.cancel_super(rid2, "ENTRY_LEG")
        self.assertEqual(k.order(rid2)["orderStatus"], "CANCELLED")
        self.assertEqual(self.sup(rid2)["orderStatus"], "CLOSED")

    def test_rejected_entry_and_saved(self):
        k = self.k
        rid = self.place(quantity=100000)                                              # far beyond the funds
        k.tick()
        self.assertEqual(self.sup(rid)["orderStatus"], "CLOSED")
        self.assertIn("Margin", self.sup(rid)["omsErrorDescription"])
        k2 = kite.Kite(mockkite.API_KEY, mockkite.TOKEN, BASE, inst=INST, data_dir=os.path.dirname(k.sup_file))
        self.assertIn(rid, k2.sup)                                                       # survives a restart


class Stream(unittest.TestCase):
    def test_parse(self):
        nse, cds, idx = (1000 << 8) | 1, (1001 << 8) | 3, (1002 << 8) | 9
        q = struct.pack(">Iiiiiiiiiii", nse, 129065, 10, 129000, 5000, 1, 2, 128000, 130000, 127500, 128800)
        c = struct.pack(">Ii", cds, 833525000)
        i = struct.pack(">Iiiiiii", idx, 2590000, 2600000, 2580000, 2585000, 2580000, 10000)
        msg = struct.pack(">H", 3) + b"".join(struct.pack(">H", len(x)) + x for x in (q, c, i))
        keys = {str(nse): "NSE_EQ:A", str(cds): "NSE_CURRENCY:B", str(idx): "IDX_I:C"}
        out = dict(kitestream.parse(msg, keys.get))
        self.assertEqual(out["NSE_EQ:A"]["ltp"], 1290.65)
        self.assertEqual(out["NSE_EQ:A"]["prev"], 1288.0)
        self.assertEqual(out["NSE_EQ:A"]["vol"], 5000)
        self.assertEqual(out["NSE_CURRENCY:B"]["ltp"], 83.3525)
        self.assertEqual((out["IDX_I:C"]["ltp"], out["IDX_I:C"]["prev"]), (25900.0, 25800.0))
        self.assertEqual(kitestream.parse(b"\x00", keys.get), [])                  # heartbeat

    def test_live_from_pretend_ticker(self):
        port = KPORT - 1
        mockkite.serve_feed(port)
        got = {}
        rk = key("ICICIBANK")
        s = kitestream.KiteStream(f"ws://127.0.0.1:{port}/", lambda: (mockkite.API_KEY, mockkite.TOKEN),
                                  lambda k, f: got.setdefault(k, f), key_of=INST.tok.get)
        import threading
        threading.Thread(target=s.run, daemon=True).start()
        s.set_keys([rk])
        for _ in range(40):
            if rk in got:
                break
            time.sleep(0.1)
            s.set_keys([rk])
        self.assertIn(rk, got)
        self.assertGreater(got[rk]["ltp"], 0)
        self.assertTrue(s.healthy())
        self.assertIn({"a": "mode", "v": ["quote", [int(rk.split(":")[1])]]}, mockkite.FEED["subs"])


if __name__ == "__main__":
    unittest.main()
