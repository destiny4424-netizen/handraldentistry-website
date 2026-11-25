"""Screener → Check a stock: the scanner's intraday and swing rules on one stock, on made-up candles.
    cd _trade && python3 -m unittest discover tests
"""
import os
import sys
import unittest
from datetime import datetime, timedelta, timezone

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import lookup                                            # noqa: E402

IST = timezone(timedelta(hours=5, minutes=30))
TODAY = datetime(2026, 11, 18, tzinfo=IST)               # a Wednesday


def ts(day, h, m):
    return int(day.replace(hour=h, minute=m).timestamp())


def session(day, base, rng=1.0, vol=1000, breakout=None):
    """5-minute candles 9:15–15:30 drifting inside base ± rng; breakout=(minute index, new level) jumps up."""
    out, px = [], base
    for i in range(75):
        t = ts(day, 9, 15) + 300 * i
        o = px
        if breakout and i >= breakout[0]:
            px = breakout[1] + 0.3 * ((i - breakout[0]) % 3)
            v = vol * (6 if i < breakout[0] + 3 else 2)
        else:
            px = base + rng * (0.5 if i % 4 < 2 else -0.5)
            v = vol
        out.append({"t": t, "o": o, "h": max(o, px) + 0.1, "l": min(o, px) - 0.1, "c": px, "v": v})
    return out


def days_back(n):
    d, out = TODAY - timedelta(days=1), []
    while len(out) < n:
        if d.weekday() < 5:
            out.append(d)
        d -= timedelta(days=1)
    return list(reversed(out))


class FakeDhan:
    def __init__(self, c5, daily):
        self.c5, self.dl = c5, daily

    @staticmethod
    def _pack(rows):
        return {"timestamp": [r["t"] for r in rows], "open": [r["o"] for r in rows], "high": [r["h"] for r in rows],
                "low": [r["l"] for r in rows], "close": [r["c"] for r in rows], "volume": [r["v"] for r in rows]}

    def intraday(self, *a):
        return self._pack(self.c5)

    def daily(self, sid, seg, *a):
        return self._pack(self.dl if seg != "IDX_I" else [])


def build(today_breakout, with_today=True):
    prev = days_back(5)
    c5 = [c for d in prev for c in session(d, 100)]
    if with_today:
        c5 += session(TODAY, 100, breakout=(12, 102.5) if today_breakout else None)[:30]     # until 11:45
    daily = []
    for i, d in enumerate(days_back(80)[:-5]):         # older days: a gentle uptrend from 90 to about 97
        px = 90 + i * 0.09
        daily.append({"t": ts(d, 0, 0), "o": px, "h": px + 1, "l": px - 1, "c": px + 0.2, "v": 75000})
    for d in prev:                                     # the last 5 sessions: the same as their 5-minute candles
        cs = [c for c in c5 if ts(d, 0, 0) <= c["t"] < ts(d, 23, 59)]
        daily.append({"t": ts(d, 0, 0), "o": cs[0]["o"], "h": max(c["h"] for c in cs), "l": min(c["l"] for c in cs),
                      "c": cs[-1]["c"], "v": sum(c["v"] for c in cs)})
    return c5, daily


class Lookup(unittest.TestCase):
    def run_check(self, c5, daily, now):
        lk = lookup.Lookup(FakeDhan(c5, daily), now_fn=lambda: now)
        return lk.check("TEST", "NSE_EQ:1", None, "13")

    def test_breakout_gives_the_scanners_plan(self):
        c5, daily = build(True)
        r = self.run_check(c5, daily, ts(TODAY, 11, 46))
        i = r["intraday"]
        self.assertTrue(r["ok"])
        self.assertTrue(i["found"], i)
        self.assertEqual(i["side"], "BUY")
        self.assertGreater(i["t1"], i["entry"])
        self.assertLess(i["sl"], i["entry"])
        self.assertEqual(len(i["checks"]), 5)
        self.assertTrue(i["checks"][0]["ok"])                          # broke a key level
        self.assertIn("swing", r)

    def test_no_breakout_gives_levels_both_sides(self):
        c5, daily = build(False)
        r = self.run_check(c5, daily, ts(TODAY, 11, 46))
        i = r["intraday"]
        self.assertFalse(i["found"])
        sides = {p["side"]: p for p in i["plans"]}
        self.assertEqual(set(sides), {"BUY", "SELL"})
        b, s = sides["BUY"], sides["SELL"]
        self.assertTrue(b["sl"] < b["entry"] < b["t1"] <= b["t2"])
        self.assertTrue(s["sl"] > s["entry"] > s["t1"] >= s["t2"])
        self.assertGreater(b["entry"], r["price"])
        self.assertLess(s["entry"], r["price"])

    def test_before_the_open_plans_today(self):
        c5, daily = build(False, with_today=False)                    # last candles are yesterday's
        r = self.run_check(c5, daily, ts(TODAY, 8, 30))
        self.assertTrue(r["pre_open"])
        self.assertIn("not open yet", r["intraday"]["verdict"])
        self.assertTrue(r["intraday"]["plans"])
        last = [c for c in c5 if c["t"] >= ts(days_back(1)[0], 9, 15)]
        pdh = max(c["h"] for c in last)
        self.assertTrue(any(abs(p["entry"] - pdh) < 0.01 for p in r["intraday"]["plans"]))   # yesterday's high

    def test_not_enough_candles(self):
        r = lookup.Lookup(FakeDhan([], []), now_fn=lambda: ts(TODAY, 11, 0)).check("TEST", "NSE_EQ:1")
        self.assertFalse(r["ok"])


if __name__ == "__main__":
    unittest.main()
