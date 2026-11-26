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


if __name__ == "__main__":
    unittest.main()
