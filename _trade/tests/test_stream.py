"""The live price stream: Dhan's binary packets, a connection to the pretend feed, and the fallback.
    cd _trade && python3 -m unittest discover tests
"""
import os
import sys
import tempfile
import threading
import time
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
import stream                                            # noqa: E402
import trade                                             # noqa: E402

FEED_PORT = 3096
mockdhan.serve_feed(FEED_PORT)
URL = f"ws://127.0.0.1:{FEED_PORT}/"


def wait(fn, secs=8):
    end = time.time() + secs
    while time.time() < end:
        v = fn()
        if v:
            return v
        time.sleep(0.1)
    return None


class Packets(unittest.TestCase):
    def test_parse(self):
        i = {"prev": 100.0, "tick": 0.05}
        buf = (mockdhan.packet_full("NSE_EQ", "2885", i, 101.5) + mockdhan.packet_quote("IDX_I", "13", i, 25900.5)
               + mockdhan.packet_ticker("NSE_FNO", "50001", 12.25) + mockdhan.packet_prev("NSE_EQ", "2885", 100.0))
        got = stream.parse(buf)
        self.assertEqual([k for k, _ in got], ["NSE_EQ:2885", "IDX_I:13", "NSE_FNO:50001", "NSE_EQ:2885"])
        full, quote, tick, prev = (f for _, f in got)
        self.assertAlmostEqual(full["ltp"], 101.5, places=3)
        self.assertEqual(full["oi"], 4321)
        self.assertEqual(full["bids"][0][1], 100)
        self.assertAlmostEqual(full["asks"][0][0], 101.55, places=2)
        self.assertAlmostEqual(quote["ltp"], 25900.5, places=0)
        self.assertEqual(quote["vol"], 500000)
        self.assertAlmostEqual(tick["ltp"], 12.25, places=3)
        self.assertAlmostEqual(prev["prev"], 100.0, places=3)

    def test_disconnect_and_junk(self):
        self.assertEqual(stream.parse(mockdhan.packet_disconnect(807)), [("_disconnect", {"code": 807})])
        self.assertEqual(stream.parse(b"\xff" * 20), [])                 # unknown packet: skipped, no crash


class Live(unittest.TestCase):
    def setUp(self):
        mockdhan.FEED.update(bad_prices=False, down=False)
        self.got = {}
        self.s = stream.Stream(URL, lambda: (mockdhan.CLIENT, mockdhan.TOKEN), lambda k, f: self.got.setdefault(k, []).append(f))
        threading.Thread(target=self.s.run, daemon=True).start()
        self.i = mockdhan.M.inst[("NSE_EQ", "2885")]

    def tearDown(self):
        self.s.enabled = False
        self.s._drop()

    def test_prices_arrive(self):
        self.assertTrue(wait(lambda: self.s.connected))
        self.s.set_keys(["NSE_EQ:2885", "IDX_I:13"])
        self.assertTrue(wait(lambda: any(f.get("ltp") for f in self.got.get("NSE_EQ:2885", []))))
        ltp = [f["ltp"] for f in self.got["NSE_EQ:2885"] if f.get("ltp")][-1]
        self.assertAlmostEqual(ltp, mockdhan.M.q(self.i), delta=0.02)
        self.assertTrue(any(f.get("prev") for f in self.got["NSE_EQ:2885"]))
        self.assertTrue(wait(lambda: self.got.get("IDX_I:13")))
        self.assertTrue(self.s.healthy())
        self.assertEqual(self.s.mode()[0], "stream")
        codes = {m["RequestCode"] for m in mockdhan.FEED["subs"][-2:]}
        self.assertEqual(codes, {17, 21})                               # index: quote mode; stock: full mode

    def test_unsubscribe(self):
        self.assertTrue(wait(lambda: self.s.connected))
        self.s.set_keys(["NSE_EQ:2885"])
        self.assertTrue(wait(lambda: self.got.get("NSE_EQ:2885")))
        self.s.set_keys([])
        self.assertTrue(wait(lambda: any(m["RequestCode"] == 22 for m in mockdhan.FEED["subs"][-3:])))

    def test_wrong_token(self):
        bad = stream.Stream(URL, lambda: (mockdhan.CLIENT, "wrong"), lambda k, f: None)
        threading.Thread(target=bad.run, daemon=True).start()
        self.assertTrue(wait(lambda: "access token is wrong" in bad.error))
        self.assertFalse(bad.healthy())
        bad.enabled = False


class Fallback(unittest.TestCase):
    def setUp(self):
        trade.QUOTES.clear()
        trade._strikes[0] = 0
        self.s = stream.Stream(URL, lambda: None, trade.on_stream)
        trade.STREAM = self.s

    def tearDown(self):
        trade.STREAM = None

    def test_stream_quote_and_rest_merge(self):
        trade.on_stream("NSE_EQ:2885", {"prev": 100.0})
        self.assertNotIn("NSE_EQ:2885", trade.QUOTES)                    # no price yet: nothing shown
        trade.on_stream("NSE_EQ:2885", {"ltp": 102.0, "vol": 5})
        q = trade.QUOTES["NSE_EQ:2885"]
        self.assertEqual((q["ltp"], q["chg"], q["pct"], q["src"]), (102.0, 2.0, 2.0, "stream"))
        m, pair = trade.merge_rest("NSE_EQ:2885", {"ltp": 101.9, "prev": 100.0, "uc": 120.0, "t": 0})
        self.assertEqual((m["ltp"], m["uc"], pair), (102.0, 120.0, (101.9, 102.0)))

    def test_mismatch_turns_the_stream_off(self):
        trade.cross_check([(100, 100.2), (50, 50.1)])
        self.assertEqual(trade._strikes[0], 0)                           # small differences: fine
        trade.cross_check([(100, 150), (50, 75)])
        self.assertEqual(self.s.off_until, 0.0)                          # one bad check isn't enough
        trade.cross_check([(100, 150), (50, 75)])
        self.assertGreater(self.s.off_until, time.time() + 3000)
        self.assertEqual(self.s.mode()[0], "polling")
        self.assertIn("didn't match", self.s.mode()[1])


if __name__ == "__main__":
    unittest.main()
