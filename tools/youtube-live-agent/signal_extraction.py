"""Heuristic extraction of trade calls from transcribed/OCR/chat text.

This is best-effort parsing for a PAPER-TRADING experiment only. Live
stream speech-to-text and OCR are noisy, and a misheard or misread word
can produce a wrong signal -- every extracted signal keeps the source
text it came from so it can be audited before (or after) any paper order
is placed.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Optional

BUY_WORDS = {
    "buy", "buying", "bought", "long", "longing", "add", "adding",
    "accumulate", "accumulating",
}
SELL_WORDS = {
    "sell", "selling", "sold", "short", "shorting", "exit", "exiting",
    "close", "closing", "dump", "dumping",
}

# Common uppercase words/acronyms that are not ticker symbols, to cut down
# on false positives when scanning for bare uppercase tokens.
TICKER_BLOCKLIST = {
    "CEO", "CFO", "IPO", "ATH", "ATL", "USD", "USDT", "OK", "US", "USA",
    "TV", "PC", "AI", "II", "III", "OMG", "LOL", "ETF", "FOMO", "YOLO",
    "DYOR", "NFA", "TA", "RSI", "MACD", "PS", "Q1", "Q2", "Q3", "Q4",
}

CRYPTO_SYMBOLS = {
    "BTC", "ETH", "SOL", "DOGE", "XRP", "ADA", "AVAX", "LTC", "BNB",
    "MATIC", "DOT", "LINK", "SHIB", "TRX",
}

# Words immediately before an action word that flip its meaning, e.g.
# "wouldn't buy TSLA" or "not going to short this". Checked over a short
# lookback window so we don't fire a signal on the opposite of what was said.
NEGATION_WORDS = {
    "not", "no", "never", "don't", "dont", "doesn't", "doesnt",
    "didn't", "didnt", "won't", "wont", "wouldn't", "wouldnt",
    "shouldn't", "shouldnt", "couldn't", "couldnt", "can't", "cant",
    "isn't", "isnt", "ain't", "aint",
}
NEGATION_LOOKBACK = 4

SYMBOL_RE = re.compile(r"^[A-Z]{1,5}$")
PRICE_RE = re.compile(r"\$?\s?(\d{1,3}(?:,\d{3})*(?:\.\d+)?)")


@dataclass
class TradeSignal:
    action: str  # "buy" or "sell"
    symbol: str
    asset_class: str  # "crypto" or "equity"
    price_hint: Optional[float]
    source_text: str
    source: str  # e.g. "audio", "overlay", "chat"


def _classify_action(word: str) -> Optional[str]:
    w = word.lower()
    if w in BUY_WORDS:
        return "buy"
    if w in SELL_WORDS:
        return "sell"
    return None


def _match_symbol(token: str, known_symbols: Optional[set[str]]) -> Optional[str]:
    """Return the normalized symbol for `token`, or None if it isn't one.

    If `known_symbols` is given, matching is case-insensitive against that
    watchlist (useful for lowercase speech-to-text transcripts). Otherwise
    falls back to a bare-uppercase heuristic (works well for OCR'd
    overlays and chat text, which tend to preserve ticker casing).

    Handles OCR'd pair notation like "BTC/USD" by matching on the base
    asset before the slash.
    """
    candidate = token.split("/")[0] if "/" in token else token
    if known_symbols:
        upper = candidate.upper()
        return upper if upper in known_symbols else None
    if SYMBOL_RE.match(candidate) and candidate not in TICKER_BLOCKLIST:
        return candidate
    return None


def _extract_price(snippet: str) -> Optional[float]:
    match = PRICE_RE.search(snippet)
    if not match:
        return None
    try:
        return float(match.group(1).replace(",", ""))
    except ValueError:
        return None


def _is_negated(cleaned: list[str], action_index: int) -> bool:
    """Check a short lookback window before the action word for a negation cue."""
    start = max(0, action_index - NEGATION_LOOKBACK)
    return any(tok.lower() in NEGATION_WORDS for tok in cleaned[start:action_index])


def extract_signals(
    text: str,
    source: str = "audio",
    window: int = 6,
    known_symbols: Optional[set[str]] = None,
) -> list[TradeSignal]:
    """Find buy/sell action words near a ticker-like token and return signals.

    `known_symbols`, if provided, restricts symbol matching to that
    watchlist (case-insensitive) instead of the bare-uppercase heuristic --
    pass this when working with lowercase speech-to-text transcripts.
    """
    if not text:
        return []

    tokens = text.split()
    cleaned = [t.strip(".,!?:;()\"'") for t in tokens]
    signals: list[TradeSignal] = []

    for i, raw in enumerate(cleaned):
        action = _classify_action(raw)
        if action is None:
            continue
        if _is_negated(cleaned, i):
            continue
        lo, hi = max(0, i - window), min(len(cleaned), i + window + 1)
        # Search by increasing distance from the action word, not raw index
        # order, so the nearest ticker-like token wins over a farther decoy.
        # Ties (equal distance on both sides) prefer the word after the
        # action, matching the common "buy SYMBOL" phrasing.
        candidate_indices = sorted(
            (j for j in range(lo, hi) if j != i),
            key=lambda j: (abs(j - i), 0 if j > i else 1),
        )
        for j in candidate_indices:
            symbol = _match_symbol(cleaned[j], known_symbols)
            if symbol is None:
                continue
            asset_class = "crypto" if symbol in CRYPTO_SYMBOLS else "equity"
            start, end = min(i, j), max(i, j)
            snippet = " ".join(tokens[max(0, start - 3):end + 4])
            signals.append(TradeSignal(
                action=action,
                symbol=symbol,
                asset_class=asset_class,
                price_hint=_extract_price(snippet),
                source_text=snippet,
                source=source,
            ))
            break  # one symbol match per action mention is enough
    return signals
