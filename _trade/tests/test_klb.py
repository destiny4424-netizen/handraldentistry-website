"""Runs tests/klb.test.js (the HTF Key Level Breakout indicator port) with the Python tests.
    cd _trade && python3 -m unittest discover tests
"""
import os
import shutil
import subprocess
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))


class KLB(unittest.TestCase):
    @unittest.skipUnless(shutil.which("node"), "node isn't installed")
    def test_indicator_port(self):
        r = subprocess.run(["node", os.path.join(HERE, "klb.test.js")], capture_output=True, text=True, timeout=60)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("All checks passed", r.stdout)


class SameAsTheChart(unittest.TestCase):
    """klb.py (the server's scan) and static/htfklb.js (the chart) give the same trades on the same candles."""

    @staticmethod
    def candles(seed, days=30, tf=15):
        import random
        rng, out, px = random.Random(seed), [], 1000.0
        day0 = 1795292100 - 33300                      # an IST midnight
        d = 0
        while len([1 for x in out]) < days * (375 // tf) and d < days * 2:
            if (day0 // 86400 + d + 3) % 7 < 5:        # weekdays only
                for k in range(375 // tf):
                    o = px
                    jump = rng.choice([0, 0, 0, 0, 0, 0, 0, 1]) * rng.uniform(4, 12) * rng.choice([1, -1])
                    c = max(1.0, px + rng.gauss(0, 3) + jump)
                    out.append({"t": day0 + d * 86400 + 33300 + k * tf * 60, "o": round(o, 2), "h": round(max(o, c) + abs(rng.gauss(0, 1.5)), 2),
                                "l": round(min(o, c) - abs(rng.gauss(0, 1.5)), 2), "c": round(c, 2)})
                    px = c
            d += 1
        return out

    @unittest.skipUnless(shutil.which("node"), "node isn't installed")
    def test_same_trades(self):
        import json
        import sys
        import tempfile
        sys.path.insert(0, os.path.dirname(HERE))
        import klb
        cases = [({}, 15), ({"rrMin": 0, "rrMax": 0}, 15), ({"rrMin": 0, "rrMax": 0, "useRetest": True, "trailR": 1.5}, 15),
                 ({"rrMin": 1, "rrMax": 4, "htf": "D", "dispMult": 0.3}, 15), ({"rrMin": 0, "rrMax": 0, "htf": "60", "cooldown": 0}, 5)]
        for seed in (1, 2, 3):
            for opts, tf in cases:
                bars = self.candles(seed, tf=tf)
                py = klb.compute(bars, tf, opts)
                with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as f:
                    json.dump({"bars": bars, "tf": tf, "opts": opts}, f)
                js = subprocess.run(["node", "-e", "const K=require(process.argv[1]);const a=JSON.parse(require('fs').readFileSync(process.argv[2]));"
                                     "const r=K.compute(a.bars,a.tf,a.opts);console.log(JSON.stringify({trades:r.trades,stats:r.stats}))",
                                     os.path.join(os.path.dirname(HERE), "static", "htfklb.js"), f.name], capture_output=True, text=True, timeout=60)
                os.unlink(f.name)
                self.assertEqual(js.returncode, 0, js.stderr)
                j = json.loads(js.stdout)
                label = f"seed {seed} tf {tf} {opts}"
                self.assertEqual(len(py["trades"]), len(j["trades"]), label)
                for a, b in zip(py["trades"], j["trades"]):
                    for k in ("dir", "i", "exitI", "status"):
                        self.assertEqual(a.get(k), b.get(k), f"{label} {k}")
                    for k in ("entry", "stop0", "stop", "tgt", "rr", "r", "exitP"):
                        if a.get(k) is not None or b.get(k) is not None:
                            self.assertAlmostEqual(a[k], b[k], places=6, msg=f"{label} {k}")
                for k in ("wins", "losses", "skipped", "warmup"):
                    self.assertEqual(py["stats"][k], j["stats"][k], f"{label} {k}")


class Scan(unittest.TestCase):
    """The 15-minute scan over every F&O stock (movers.Movers.run_klb)."""

    def test_crossings_signals_and_alerts(self):
        import sys
        from datetime import datetime, timedelta, timezone
        sys.path.insert(0, os.path.dirname(HERE))
        sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(HERE)), "_scanner"))
        import movers
        bars = SameAsTheChart.candles(1, tf=15)
        last = bars[-1]["t"] + 900
        clock = {"t": last + 30}
        ist = timezone(timedelta(hours=5, minutes=30))
        got = []
        settings = {"rrMin": 0, "rrMax": 0}
        m = movers.Movers(None, None, lambda: datetime.fromtimestamp(clock["t"], ist), lambda: {},
                          klb_opts_fn=lambda: settings, on_klb=lambda rows: got.append(rows))
        m.c15 = {"TESTSTOCK": ("NSE_EQ:1", bars)}
        v = m.run_klb(alert=True)
        last_day = datetime.fromtimestamp(bars[-1]["t"], ist).strftime("%Y-%m-%d")
        self.assertTrue(v["rows"])
        self.assertTrue(all(r["day"] == last_day for r in v["rows"]))                # today's (the last session's) only
        self.assertEqual(v["opts"]["htf"], "240")                                     # 15m chart -> 4h levels
        sig = [r for r in v["rows"] if r.get("signal")]
        for r in sig:
            self.assertTrue(r["sl"] < r["entry"] < r["tgt"] if r["side"] == "BUY" else r["sl"] > r["entry"] > r["tgt"])
        for r in v["rows"]:
            if not r.get("signal"):
                self.assertTrue(r["why"] and r["why"] != "signal")
        fresh = [r for r in sig if r["t"] + 900 >= clock["t"] - 1200]
        self.assertEqual(sum(len(x) for x in got), len(fresh))                        # only the candle that just closed
        m.run_klb(alert=True)
        self.assertEqual(sum(len(x) for x in got), len(fresh))                        # never twice
        settings.update(rrMin=50, rrMax=0)                                             # new inputs, same candles
        v2 = m.run_klb()
        self.assertFalse([r for r in v2["rows"] if r.get("signal")])
        self.assertEqual(len(v2["rows"]), len(v["rows"]))                              # the crossings stay, as crossings
        self.assertTrue(any("R:R" in (r["why"] or "") for r in v2["rows"]))


if __name__ == "__main__":
    unittest.main()
