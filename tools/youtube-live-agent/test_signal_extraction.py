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


class TestNegationHandling(unittest.TestCase):
    def test_simple_negation_before_action_suppresses_signal(self):
        signals = extract_signals("I wouldn't buy TSLA right now", source="overlay")
        self.assertEqual(signals, [])

    def test_negation_further_back_suppresses_signal(self):
        signals = extract_signals("I am definitely not going to short TSLA today", source="overlay")
        self.assertEqual(signals, [])

    def test_negation_outside_lookback_window_still_fires(self):
        # "not" is more than NEGATION_LOOKBACK tokens before the action word,
        # so it shouldn't suppress this one -- it's about something else.
        signals = extract_signals(
            "not sure about the market today but I will buy AAPL anyway", source="overlay"
        )
        self.assertEqual(len(signals), 1)
        self.assertEqual(signals[0].symbol, "AAPL")

    def test_positive_action_without_negation_still_fires(self):
        signals = extract_signals("I will definitely buy TSLA right now", source="overlay")
        self.assertEqual(len(signals), 1)
        self.assertEqual(signals[0].symbol, "TSLA")


class TestNearestSymbolWins(unittest.TestCase):
    def test_picks_closest_symbol_after_action_over_leftmost_decoy(self):
        # MSFT is a decoy that is leftmost in the window (index 0) but is
        # farther from "buy" than TSLA, which sits right next to it. A
        # left-to-right scan would wrongly pick MSFT; distance-based
        # search must pick TSLA.
        text = "MSFT was just mentioned then lets buy TSLA right now"
        signals = extract_signals(text, source="overlay")
        self.assertEqual(len(signals), 1)
        self.assertEqual(signals[0].symbol, "TSLA")

    def test_picks_closest_symbol_before_action_over_leftmost_decoy(self):
        # MSFT is again the leftmost token in the window, but TSLA sits
        # right before "buy" and is the true nearest match.
        text = "MSFT mentioned once TSLA ready lets buy now"
        signals = extract_signals(text, source="overlay")
        self.assertEqual(len(signals), 1)
        self.assertEqual(signals[0].symbol, "TSLA")


class TestSlashPairNotation(unittest.TestCase):
    def test_ocr_slash_pair_matches_base_asset(self):
        signals = extract_signals("chart shows BTC/USD buy signal triggered here", source="overlay")
        self.assertEqual(len(signals), 1)
        self.assertEqual(signals[0].symbol, "BTC")
        self.assertEqual(signals[0].asset_class, "crypto")


class TestRegressionSafety(unittest.TestCase):
    def test_hyphenated_sell_off_is_not_an_action_word(self):
        signals = extract_signals("market sell-off happening near AAPL highs", source="overlay")
        self.assertEqual(signals, [])

    def test_substring_of_action_word_is_not_matched(self):
        signals = extract_signals("the buyer of AAPL shares was surprised", source="overlay")
        self.assertEqual(signals, [])

    def test_multiple_independent_signals_in_one_text(self):
        signals = extract_signals("let's buy AAPL now and short TSLA later", source="overlay")
        by_symbol = {s.symbol: s.action for s in signals}
        self.assertEqual(by_symbol.get("AAPL"), "buy")
        self.assertEqual(by_symbol.get("TSLA"), "sell")

    def test_no_digits_in_snippet_yields_no_price_hint(self):
        signals = extract_signals("let's buy AAPL right now no cap", source="overlay")
        self.assertEqual(len(signals), 1)
        self.assertIsNone(signals[0].price_hint)


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
