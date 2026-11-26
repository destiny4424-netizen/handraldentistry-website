"""Stocks you trade join the watchlist by themselves.
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
if mockdhan.M is None:                                   # one pretend Dhan for all test files
    mockdhan.serve(PORT, tick_every=3600)
os.environ.setdefault("MT_DHAN_API", f"http://127.0.0.1:{PORT}/v2")
os.environ.setdefault("MT_MASTER_URL", f"http://127.0.0.1:{PORT}/scrip-master.csv")
import trade                                             # noqa: E402

trade.D.client_id, trade.D.token = mockdhan.CLIENT, mockdhan.TOKEN
if not trade.INST.by_key:
    trade.INST.load(force=True)


def eq(sym):
    return trade.stock_key(sym)


def trade_row(key):
    seg, sid = key.split(":")
    return {"exchangeSegment": seg, "securityId": sid, "exchangeTime": "2026-11-26 10:00:00"}


class WatchTraded(unittest.TestCase):
    def setUp(self):
        trade.SETTINGS["watchlists"] = [{"name": "My stocks", "keys": [eq("RELIANCE"), eq("SBIN")]}]
        trade.SETTINGS.pop("wl_auto", None)

    def keys(self):
        return trade.one_list(trade.SETTINGS["watchlists"])[0]["keys"]

    def test_stocks_and_stock_options_added_index_options_not(self):
        hul_opt = next(k for k, i in trade.INST.by_key.items() if i["ul"] == "HINDUNILVR" and i["opt"])
        nifty_opt = trade.INST.search("nifty 26000 ce")[0]["key"]
        added = trade.watch_traded([trade_row(eq("WIPRO")), trade_row(hul_opt), trade_row(nifty_opt), trade_row(eq("SBIN"))])
        self.assertEqual(set(added), {eq("WIPRO"), eq("HINDUNILVR")})          # SBIN was already there; NIFTY skipped
        self.assertEqual(set(self.keys()[:2]), {eq("WIPRO"), eq("HINDUNILVR")})   # new ones at the top
        self.assertEqual(self.keys()[2:], [eq("RELIANCE"), eq("SBIN")])
        self.assertEqual(trade.watch_traded([trade_row(eq("WIPRO"))]), [])      # already there: nothing new

    def test_removed_stock_stays_removed_that_day(self):
        trade.watch_traded([trade_row(eq("WIPRO"))])
        trade.SETTINGS["watchlists"] = [{"name": "My stocks", "keys": [k for k in self.keys() if k != eq("WIPRO")]}]
        self.assertEqual(trade.watch_traded([trade_row(eq("WIPRO"))]), [])
        self.assertNotIn(eq("WIPRO"), self.keys())

    def test_real_trades_from_dhan(self):
        trade.RECENT.clear()
        trade.SETTINGS["limits"] = {}
        trade.RULES = trade.BOT = None
        code, r = trade.place({"key": eq("ITC"), "side": "BUY", "qty": 1, "product": "INTRADAY", "type": "MARKET"})
        self.assertEqual(code, 200, r)
        self.assertIn(eq("ITC"), trade.watch_traded())                          # read from Dhan's trade book
        self.assertEqual(self.keys()[0], eq("ITC"))


if __name__ == "__main__":
    unittest.main()
