import unittest

from signal_extraction import extract_signals


class TestExtractSignalsUppercaseHeuristic(unittest.TestCase):
    def test_buy_signal_with_price(self):
        signals = extract_signals("I'm going to buy AAPL at $150 right now", source="overlay")
        self.assertEqual(len(signals), 1)
        sig = signals[0]
        self.assertEqual(sig.action, "buy")
        self.assertEqual(sig.symbol, "AAPL")
        self.assertEqual(sig.asset_class, "equity")
        self.assertEqual(sig.price_hint, 150.0)

    def test_sell_signal_crypto(self):
        signals = extract_signals("time to sell BTC here", source="overlay")
        self.assertEqual(len(signals), 1)
        self.assertEqual(signals[0].action, "sell")
        self.assertEqual(signals[0].symbol, "BTC")
        self.assertEqual(signals[0].asset_class, "crypto")

    def test_blocklisted_acronym_not_treated_as_symbol(self):
        signals = extract_signals("the CEO said buy the dip but no ticker here", source="overlay")
        self.assertEqual(signals, [])

    def test_no_action_word_no_signal(self):
        signals = extract_signals("AAPL is trading near its highs today", source="overlay")
        self.assertEqual(signals, [])

    def test_lowercase_text_finds_nothing_without_watchlist(self):
        signals = extract_signals("i think you should buy apple soon", source="audio")
        self.assertEqual(signals, [])

    def test_empty_text(self):
        self.assertEqual(extract_signals(""), [])

    def test_action_word_itself_not_matched_as_symbol(self):
        # "SHORT" is 5 uppercase letters and could look like a ticker;
        # it must not be matched as the symbol for its own action.
        signals = extract_signals("SHORT here on TSLA now", source="overlay")
        self.assertEqual(len(signals), 1)
        self.assertEqual(signals[0].symbol, "TSLA")


class TestExtractSignalsWithWatchlist(unittest.TestCase):
    def test_lowercase_transcript_matches_watchlist_case_insensitively(self):
        watchlist = {"AAPL", "TSLA", "BTC"}
        signals = extract_signals(
            "i think we should buy aapl around one fifty", source="audio", known_symbols=watchlist
        )
        self.assertEqual(len(signals), 1)
        self.assertEqual(signals[0].symbol, "AAPL")

    def test_symbol_not_in_watchlist_is_ignored(self):
        watchlist = {"AAPL"}
        signals = extract_signals("buy tsla now", source="audio", known_symbols=watchlist)
        self.assertEqual(signals, [])


if __name__ == "__main__":
    unittest.main()
