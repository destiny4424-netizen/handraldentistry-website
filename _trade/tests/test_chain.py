"""Option chains for stocks and indices, including when Dhan's option chain has nothing.
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


class Chains(unittest.TestCase):
    def setUp(self):
        trade.CHAIN_CACHE.clear()
        trade.CHAIN_SEG.clear()
        mockdhan.M.chain_down = False

    def tearDown(self):
        mockdhan.M.chain_down = False

    def priced(self, c):
        return [r for r in c["rows"] if (r["CE"] or {}).get("ltp") and (r["PE"] or {}).get("ltp")]

    def test_stock_chain(self):
        c = trade.chain("RELIANCE", "")
        self.assertEqual(c["source"], "chain")
        self.assertTrue(self.priced(c))
        self.assertGreater(c["spot"], 0)

    def test_stock_chain_when_dhan_has_none(self):
        mockdhan.M.chain_down = True
        c = trade.chain("SBIN", "")
        self.assertNotIn("error", c)
        self.assertEqual(c["source"], "quotes")                 # built from each option's own quote
        self.assertGreater(len(self.priced(c)), 5)
        self.assertGreater(c["spot"], 0)
        r = self.priced(c)[0]
        self.assertTrue(r["CE"]["key"].startswith("NSE_FNO:"))  # Buy has a contract to open

    def test_index_chain(self):
        c = trade.chain("NIFTY", "")
        self.assertEqual(c["source"], "chain")
        self.assertTrue(self.priced(c))


if __name__ == "__main__":
    unittest.main()
