"""Minimal read-only client for Delta Exchange's public candle history endpoint.

Only the public GET /v2/history/candles endpoint is used, so no API key is
required for this alerts-only bot. Field names below match Delta's documented
candle response as of this writing (time/open/high/low/close/volume) -- if
Delta changes their API, update `_parse_candles` accordingly.
"""
from __future__ import annotations

import time
from dataclasses import dataclass

import requests


@dataclass(frozen=True)
class Candle:
    time: int  # unix seconds, candle open time
    open: float
    high: float
    low: float
    close: float


class DeltaClient:
    def __init__(self, base_url: str, symbol: str, resolution: str, timeout: int = 15):
        self.base_url = base_url
        self.symbol = symbol
        self.resolution = resolution
        self.timeout = timeout

    def fetch_candles(self, lookback: int) -> list[Candle]:
        # Resolution is in minutes for numeric values; fall back to a 1-day
        # window multiplier for non-numeric resolutions like "1D".
        try:
            minutes = int(self.resolution)
            span_seconds = minutes * 60 * (lookback + 5)
        except ValueError:
            span_seconds = 24 * 60 * 60 * (lookback + 5)

        end = int(time.time())
        start = end - span_seconds

        resp = requests.get(
            f"{self.base_url}/v2/history/candles",
            params={
                "resolution": self.resolution,
                "symbol": self.symbol,
                "start": start,
                "end": end,
            },
            timeout=self.timeout,
        )
        resp.raise_for_status()
        payload = resp.json()

        if not payload.get("success", False):
            raise RuntimeError(f"Delta API returned an error: {payload}")

        return self._parse_candles(payload.get("result", []))

    @staticmethod
    def _parse_candles(raw: list[dict]) -> list[Candle]:
        candles = [
            Candle(
                time=int(row["time"]),
                open=float(row["open"]),
                high=float(row["high"]),
                low=float(row["low"]),
                close=float(row["close"]),
            )
            for row in raw
        ]
        # Delta returns newest-first; the strategy expects oldest-first.
        candles.sort(key=lambda c: c.time)
        return candles
