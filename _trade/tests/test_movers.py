"""Market tab: breakouts, OI build-up and the snapshot against the pretend Dhan.
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
if mockdhan.M is None:
    mockdhan.serve(PORT, tick_every=3600)
os.environ.setdefault("MT_DHAN_API", f"http://127.0.0.1:{PORT}/v2")
os.environ.setdefault("MT_MASTER_URL", f"http://127.0.0.1:{PORT}/scrip-master.csv")
import trade                                             # noqa: E402
import movers                                            # noqa: E402

IST = timezone(timedelta(hours=5, minutes=30))
trade.D.client_id, trade.D.token = mockdhan.CLIENT, mockdhan.TOKEN
if not trade.INST.by_key:
    trade.INST.load(force=True)


def c15(hm, o, h, l, c, v=1000):
    t = datetime(2026, 11, 24, int(hm[:2]), int(hm[3:]), tzinfo=IST)
    return {"t": int(t.timestamp()), "o": o, "h": h, "l": l, "c": c, "v": v}


class Breakouts(unittest.TestCase):
    def test_opening_range_and_yesterday(self):
        day = [c15("09:15", 100, 102, 99, 101), c15("09:30", 101, 101.8, 100.5, 101.5),
               c15("09:45", 101.5, 103, 101.4, 102.6, 3000),            # closes above the 102 range high
               c15("10:00", 102.6, 104.5, 102.5, 104.2),                # above yesterday's high 104
               c15("10:15", 104.2, 105, 103, 104.8)]                    # no new break (only the first counts)
        b = movers.opening_range_breaks(day, pdh=104, pdl=95)
        self.assertEqual([(x["level_name"], x["at"]) for x in b],
                         [("15-min range high", "09:45"), ("Yesterday's high", "10:00")])
        self.assertEqual(b[0]["vol_x"], 2.1)                                # 3000 vs a 1400 average
        self.assertAlmostEqual(b[0]["beyond_pct"], 0.59, places=2)

    def test_breakdown(self):
        day = [c15("09:15", 100, 101, 99, 100), c15("09:30", 100, 100, 98, 98.5)]
        self.assertEqual(movers.opening_range_breaks(day)[0]["side"], "down")

    def test_buildup(self):
        self.assertEqual(movers.buildup(1.2, 4.0), "Long build-up")
        self.assertEqual(movers.buildup(-1.2, 4.0), "Short build-up")
        self.assertEqual(movers.buildup(1.2, -4.0), "Short covering")
        self.assertEqual(movers.buildup(-1.2, -4.0), "Long unwinding")
        self.assertIsNone(movers.buildup(0.1, 4.0))                        # price barely moved


class Snapshot(unittest.TestCase):
    def test_against_pretend_dhan(self):
        m = movers.Movers(trade.D, trade.INST, lambda: datetime.now(IST), lambda: {})
        m.load_prev_oi()
        s = m.snapshot(max_age=0)
        self.assertGreaterEqual(s["count"], 15)
        pcts = [r["pct"] for r in s["gainers"]]
        self.assertEqual(pcts, sorted(pcts, reverse=True))
        self.assertEqual(s["losers"][0]["pct"], min(r["pct"] for r in s["gainers"] + s["losers"]))
        self.assertEqual(sum(s["breadth"].values()), s["count"])
        self.assertTrue(s["oi_ready"])
        self.assertTrue(any(r["oi_pct"] is not None for r in s["gainers"] + s["losers"]))
        self.assertTrue(s["sectors"])
        self.assertIn("NIFTY", [i["name"] for i in s["indices"]])


if __name__ == "__main__":
    unittest.main()
