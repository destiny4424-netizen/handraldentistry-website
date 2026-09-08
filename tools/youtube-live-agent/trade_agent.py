#!/usr/bin/env python3
"""PAPER-TRADING ONLY agent that watches a YouTube live stream for spoken
or on-screen trade calls and simulates trades via Alpaca's paper API.

SAFETY / DISCLAIMER
--------------------
- This tool only ever places PAPER (simulated, no real money) trades.
  `broker.py` hard-codes `paper=True` and exposes no way to point this
  agent at a live brokerage account.
- Speech-to-text and OCR on a live stream are inherently noisy. Treat
  every detected "signal" as an unverified heuristic guess, not a
  confirmed trade call -- review the audit log (--output) regularly.
- This is not financial advice, and following trade calls made in a
  livestream (even in simulation) carries no guarantee of profitability.
  "Live trading signal" streams are also a well-known vector for
  pump-and-dump style scams; treat any real-money decision separately
  and with independent research.

Usage:
    export ALPACA_API_KEY=your_paper_key
    export ALPACA_SECRET_KEY=your_paper_secret
    python trade_agent.py --url https://www.youtube.com/watch?v=VIDEO_ID \\
        --duration 1800 --segment-seconds 30 --notional 100 \\
        --watchlist watchlist.txt --output audit_log.json

    # Detect and log signals without placing any paper orders:
    python trade_agent.py --url ... --dry-run
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import tempfile
import time
from pathlib import Path
from typing import Optional

from broker import PaperBroker
from capture import capture_segment, resolve_stream_url
from signal_extraction import extract_signals
from transcribe import ocr_frame, transcribe_audio

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger("trade_agent")


class Cooldown:
    """Prevents re-firing the same symbol/action repeatedly within a window."""

    def __init__(self, seconds: int = 300, clock=time.time):
        self.seconds = seconds
        self._clock = clock
        self._last: dict[tuple[str, str], float] = {}

    def should_fire(self, action: str, symbol: str) -> bool:
        key = (action, symbol)
        now = self._clock()
        last = self._last.get(key)
        if last is not None and now - last < self.seconds:
            return False
        self._last[key] = now
        return True


def load_watchlist(path: Optional[str]) -> Optional[set[str]]:
    if not path:
        return None
    symbols = set()
    with open(path) as f:
        for line in f:
            line = line.strip().upper()
            if line and not line.startswith("#"):
                symbols.add(line)
    return symbols or None


def run(args: argparse.Namespace) -> list[dict]:
    broker = None
    if not args.dry_run:
        api_key = args.alpaca_key or os.environ.get("ALPACA_API_KEY")
        secret_key = args.alpaca_secret or os.environ.get("ALPACA_SECRET_KEY")
        if not api_key or not secret_key:
            raise SystemExit(
                "Missing Alpaca paper-trading credentials. Set ALPACA_API_KEY / "
                "ALPACA_SECRET_KEY, or pass --dry-run to only log detected signals."
            )
        broker = PaperBroker(api_key, secret_key)

    watchlist = load_watchlist(args.watchlist)
    cooldown = Cooldown(seconds=args.cooldown)
    audit_log: list[dict] = []
    elapsed = 0

    log.info("Resolving live stream URL...")
    stream_url = resolve_stream_url(args.url)

    with tempfile.TemporaryDirectory() as tmp:
        tmp_dir = Path(tmp)
        while elapsed <= args.duration:
            audio_path, frame_path = capture_segment(stream_url, tmp_dir, args.segment_seconds)
            try:
                transcript = transcribe_audio(audio_path, args.whisper_model)
                overlay_text = ocr_frame(frame_path)
            finally:
                audio_path.unlink(missing_ok=True)
                frame_path.unlink(missing_ok=True)

            log.info("Transcript: %s", transcript[:200])
            log.info("Overlay OCR: %s", overlay_text[:200])

            signals = (
                extract_signals(transcript, source="audio", known_symbols=watchlist)
                + extract_signals(overlay_text, source="overlay", known_symbols=watchlist)
            )

            for sig in signals:
                entry = {
                    "timestamp": time.time(),
                    "action": sig.action,
                    "symbol": sig.symbol,
                    "asset_class": sig.asset_class,
                    "price_hint": sig.price_hint,
                    "source": sig.source,
                    "source_text": sig.source_text,
                    "executed": False,
                }
                if not cooldown.should_fire(sig.action, sig.symbol):
                    log.info("Skipping duplicate signal within cooldown: %s %s", sig.action, sig.symbol)
                    audit_log.append(entry)
                    continue

                log.info(
                    "Detected signal: %s %s (%s) from %s -- %r",
                    sig.action, sig.symbol, sig.asset_class, sig.source, sig.source_text,
                )
                if broker is not None:
                    try:
                        order = broker.place_paper_order(
                            symbol=sig.symbol,
                            side=sig.action,
                            asset_class=sig.asset_class,
                            notional=args.notional,
                        )
                        entry["executed"] = True
                        entry["order"] = order
                        log.info("Placed PAPER order: %s", order)
                    except Exception as e:
                        entry["error"] = str(e)
                        log.error("Failed to place paper order for %s %s: %s", sig.action, sig.symbol, e)
                audit_log.append(entry)

            if args.output:
                with open(args.output, "w") as f:
                    json.dump(audit_log, f, indent=2)

            elapsed += args.segment_seconds

    executed = sum(1 for a in audit_log if a["executed"])
    log.info("Done. %d signals detected, %d paper orders executed.", len(audit_log), executed)
    return audit_log


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "PAPER-TRADING ONLY agent: detects trade calls in a YouTube live "
            "stream's audio/overlay and simulates trades via Alpaca. Never "
            "places real-money trades."
        )
    )
    parser.add_argument("--url", required=True, help="YouTube live stream URL")
    parser.add_argument("--duration", type=int, default=1800, help="Total seconds to watch the stream")
    parser.add_argument("--segment-seconds", type=int, default=30, help="Seconds of audio/one frame captured per cycle")
    parser.add_argument("--cooldown", type=int, default=300, help="Seconds before the same symbol/action can fire again")
    parser.add_argument("--notional", type=float, default=100.0, help="Simulated dollar amount per paper trade")
    parser.add_argument("--whisper-model", default="base", help="faster-whisper model size (tiny/base/small/medium)")
    parser.add_argument("--watchlist", help="Path to a newline-separated list of known tickers/crypto symbols "
                                             "to match case-insensitively (recommended for spoken audio)")
    parser.add_argument("--alpaca-key", help="Alpaca paper API key (or set ALPACA_API_KEY)")
    parser.add_argument("--alpaca-secret", help="Alpaca paper API secret (or set ALPACA_SECRET_KEY)")
    parser.add_argument("--dry-run", action="store_true", help="Only detect and log signals; never place paper orders")
    parser.add_argument("--output", help="Path to save the JSON audit log of every detected signal")
    return parser


def main(argv: Optional[list[str]] = None) -> None:
    parser = build_parser()
    args = parser.parse_args(argv)
    run(args)


if __name__ == "__main__":
    main()
