import unittest

from trade_agent import Cooldown, load_watchlist


class TestCooldown(unittest.TestCase):
    def test_fires_first_time(self):
        cooldown = Cooldown(seconds=60, clock=lambda: 0)
        self.assertTrue(cooldown.should_fire("buy", "AAPL"))

    def test_blocks_within_window(self):
        clock = {"t": 0}
        cooldown = Cooldown(seconds=60, clock=lambda: clock["t"])
        self.assertTrue(cooldown.should_fire("buy", "AAPL"))
        clock["t"] = 30
        self.assertFalse(cooldown.should_fire("buy", "AAPL"))

    def test_allows_after_window_expires(self):
        clock = {"t": 0}
        cooldown = Cooldown(seconds=60, clock=lambda: clock["t"])
        self.assertTrue(cooldown.should_fire("buy", "AAPL"))
        clock["t"] = 61
        self.assertTrue(cooldown.should_fire("buy", "AAPL"))

    def test_different_symbols_independent(self):
        clock = {"t": 0}
        cooldown = Cooldown(seconds=60, clock=lambda: clock["t"])
        self.assertTrue(cooldown.should_fire("buy", "AAPL"))
        self.assertTrue(cooldown.should_fire("buy", "TSLA"))

    def test_different_action_same_symbol_independent(self):
        clock = {"t": 0}
        cooldown = Cooldown(seconds=60, clock=lambda: clock["t"])
        self.assertTrue(cooldown.should_fire("buy", "AAPL"))
        self.assertTrue(cooldown.should_fire("sell", "AAPL"))


class TestLoadWatchlist(unittest.TestCase):
    def test_none_path_returns_none(self):
        self.assertIsNone(load_watchlist(None))

    def test_parses_file_uppercase_and_skips_comments(self, tmp_path=None):
        import tempfile
        import os
        fd, path = tempfile.mkstemp()
        try:
            with os.fdopen(fd, "w") as f:
                f.write("aapl\n# a comment\nTSLA\n\nbtc\n")
            result = load_watchlist(path)
            self.assertEqual(result, {"AAPL", "TSLA", "BTC"})
        finally:
            os.unlink(path)


if __name__ == "__main__":
    unittest.main()
