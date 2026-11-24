"""MCX: contract sizes, the open P&L Dhan sends per unit, order value, risk and insights.
The positions are made up (this repository is public).   cd _trade && python3 -m unittest discover tests
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
import mcx                                               # noqa: E402
import insights                                          # noqa: E402

trade.D.client_id, trade.D.token = mockdhan.CLIENT, mockdhan.TOKEN
if not trade.INST.by_key:
    trade.INST.load(force=True)


def sample_positions():
    """Made-up positions in the shape Dhan's API sends them: open MCX P&L per unit, closed MCX in rupees."""
    return [
        {"tradingSymbol": "SBIN-Dec2026-900-CE", "exchangeSegment": "NSE_FNO", "securityId": "1", "netQty": 750,
         "buyAvg": 12.0, "sellAvg": 0, "realizedProfit": 0, "unrealizedProfit": -1500.0, "multiplier": 1000},
        {"tradingSymbol": "SILVER100-Dec2026-FUT", "exchangeSegment": "MCX_COMM", "securityId": "2", "netQty": -40,
         "buyAvg": 0, "sellAvg": 1500.0, "realizedProfit": 0, "unrealizedProfit": -200.0, "multiplier": 10},
        {"tradingSymbol": "GOLDGUINEA-Dec2026-FUT", "exchangeSegment": "MCX_COMM", "securityId": "3", "netQty": 0,
         "realizedProfit": -5000.0, "unrealizedProfit": 0},
        {"tradingSymbol": "SILVERM-Dec2026-100000-PE", "exchangeSegment": "MCX_COMM", "securityId": "4", "netQty": 0,
         "realizedProfit": -1000.0, "unrealizedProfit": 0},
        {"tradingSymbol": "GOLDPETAL-Dec2026-FUT", "exchangeSegment": "MCX_COMM", "securityId": "5", "netQty": 0,
         "realizedProfit": 3000.0, "unrealizedProfit": 0}]


class Positions(unittest.TestCase):
    def test_open_mcx_in_rupees(self):
        pos = mcx.fix_positions(sample_positions())
        self.assertEqual(pos[1]["unrealizedProfit"], -2000.0)           # -200 per unit x 10
        self.assertEqual(pos[0]["unrealizedProfit"], -1500.0)           # NSE untouched
        self.assertEqual(pos[2]["realizedProfit"], -5000.0)             # closed MCX untouched
        self.assertAlmostEqual(trade.today_pnl(pos), -1500 - 2000 - 5000 - 1000 + 3000, places=1)

    def test_never_applied_twice(self):
        p = sample_positions()[1]
        q = {"MCX_COMM:2": {"ltp": 1505.0}}                               # short 40 @ 1,500: -200 per unit
        self.assertEqual(mcx.fix_positions([dict(p)], q)[0]["unrealizedProfit"], -2000.0)
        already = dict(p, unrealizedProfit=-2000.0)                     # if Dhan starts sending it in rupees
        self.assertEqual(mcx.fix_positions([already], q)[0]["unrealizedProfit"], -2000.0)

    def test_specs(self):
        self.assertEqual(mcx.multiplier("SILVER100 DEC FUT"), 10)
        self.assertEqual(mcx.multiplier("SILVERM-Dec2026-100000-PE"), 5)
        self.assertEqual(mcx.multiplier("GOLDGUINEA-Dec2026-FUT"), 1)
        self.assertIsNone(mcx.multiplier("MENTHAOIL-Dec2026-FUT"))


class Orders(unittest.TestCase):
    def setUp(self):
        self.gold = next(k for k, i in trade.INST.by_key.items() if i["seg"] == "MCX_COMM")
        trade.RECENT.clear()
        trade.SETTINGS["limits"] = {"max_order_value": 0}

    def test_lots_and_value(self):
        it = trade.INST.get(self.gold)
        self.assertEqual((it["lot"], it["mult"]), (1, 100))                # GOLD: 1 kg lot, price per 10 g
        o, it, stop, warn = trade.build_order({"key": self.gold, "side": "BUY", "qty": 1, "product": "INTRADAY",
                                               "type": "LIMIT", "price": 126500})
        self.assertEqual(stop, [])
        self.assertEqual(o["quantity"], 1)                                # 1 lot goes to Dhan as quantity 1
        self.assertEqual(o["_value"], 12650000.0)                         # 1 kg at ₹1,26,500 per 10 g

    def test_risk_in_rupees(self):
        p = trade.preview({"key": self.gold, "side": "BUY", "qty": 1, "product": "INTRADAY", "type": "LIMIT",
                           "price": 126500, "super": {"sl": 126400}})
        self.assertEqual(p["risk"], 10000.0)                              # ₹100 x 1 lot x 100

    def test_unknown_contract_blocked(self):
        it = trade.INST.get(self.gold)
        saved = it["mult"]
        it["mult"] = None
        try:
            o, it2, stop, warn = trade.build_order({"key": self.gold, "side": "BUY", "qty": 1, "product": "INTRADAY",
                                                    "type": "LIMIT", "price": 126500})
            self.assertTrue(any("contract size" in x for x in stop))
        finally:
            it["mult"] = saved

    def test_rules_size(self):
        import rules
        r = rules.Rules(tempfile.mkdtemp(), trade.D, trade.INST, trade.QUOTES, lambda: __import__("datetime").datetime.now())
        self.assertEqual(r.max_qty(1500, 1510, 1, 10), 15)                # ₹1,500 / (₹10 x 10) = 15 lots of SILVER100
        self.assertEqual(r.max_qty(1500, 1510, 1, 1), 150)


class Insights(unittest.TestCase):
    def test_mcx_round_trip_in_rupees(self):
        f = lambda t, side, px: {"uid": t + side, "order_id": "", "time": t, "date": t[:10], "seg": "MCX_COMM", "sid": "2",
                                 "sym": "SILVER100 DEC FUT", "kind": "FUTCOM", "product": "MARGIN", "side": side, "qty": 40,
                                 "price": px, "charges": 0.0, "est": False, "expiry": "", "strike": 0, "opt": "", "source": "history"}
        closed, _ = insights.round_trips([f("2026-11-24 10:00:00", "SELL", 1500.0), f("2026-11-24 11:00:00", "BUY", 1505.0)])
        self.assertAlmostEqual(closed[0]["gross"], -2000.0, places=1)


if __name__ == "__main__":
    unittest.main()
