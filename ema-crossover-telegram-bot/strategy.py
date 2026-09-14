"""EMA(20/50) crossover + pullback-to-EMA20 signal logic.

Strategy, as a state machine per symbol:

1. NONE      -- no crossover currently being tracked.
2. Crossover -- EMA fast crosses EMA slow. Bullish (fast crosses above slow)
               starts an "up" watch; bearish (fast crosses below slow) starts
               a "down" watch.
3. Waiting for pullback -- while the trend holds (fast stays on the correct
               side of slow), wait for a candle whose high/low comes back to
               touch EMA fast. That touch is the entry signal.
4. Re-arm    -- after a signal, price must close clearly away from EMA fast
               again before another touch in the same trend can re-signal
               (stops repeat alerts while price chops along the EMA).
5. Trend break -- if fast crosses back over slow before a pullback signal,
               the watch is dropped and we wait for the next crossover.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from delta_client import Candle

Direction = Literal["up", "down"]


def ema_series(values: list[float], period: int) -> list[float | None]:
    """Standard EMA, seeded with an SMA of the first `period` values."""
    if len(values) < period:
        return [None] * len(values)

    result: list[float | None] = [None] * (period - 1)
    sma = sum(values[:period]) / period
    result.append(sma)

    k = 2 / (period + 1)
    prev = sma
    for price in values[period:]:
        prev = price * k + prev * (1 - k)
        result.append(prev)

    return result


@dataclass
class Signal:
    direction: Direction
    candle: Candle
    ema_fast: float
    ema_slow: float


@dataclass
class _Watch:
    direction: Direction
    armed: bool = True  # can this watch fire a signal right now?


class CrossoverPullbackStrategy:
    def __init__(self, ema_fast: int, ema_slow: int, touch_tolerance: float, rearm_tolerance: float):
        self.ema_fast_period = ema_fast
        self.ema_slow_period = ema_slow
        self.touch_tolerance = touch_tolerance
        self.rearm_tolerance = rearm_tolerance
        self._watch: _Watch | None = None
        self._last_processed_time: int | None = None

    def process(self, candles: list[Candle]) -> list[Signal]:
        """Feed the full closed-candle history; returns any new signals.

        Safe to call repeatedly with a growing candle list -- candles already
        processed (by open time) are skipped.
        """
        closes = [c.close for c in candles]
        fast = ema_series(closes, self.ema_fast_period)
        slow = ema_series(closes, self.ema_slow_period)

        signals: list[Signal] = []

        start_index = 1
        if self._last_processed_time is not None:
            for i, c in enumerate(candles):
                if c.time > self._last_processed_time:
                    start_index = max(i, 1)
                    break
            else:
                return signals  # nothing new

        for i in range(start_index, len(candles)):
            if fast[i] is None or slow[i] is None or fast[i - 1] is None or slow[i - 1] is None:
                continue

            signal = self._process_candle(
                candle=candles[i],
                fast_prev=fast[i - 1],
                slow_prev=slow[i - 1],
                fast_now=fast[i],
                slow_now=slow[i],
            )
            if signal:
                signals.append(signal)

            self._last_processed_time = candles[i].time

        return signals

    def _process_candle(
        self,
        candle: Candle,
        fast_prev: float,
        slow_prev: float,
        fast_now: float,
        slow_now: float,
    ) -> Signal | None:
        bullish_cross = fast_prev <= slow_prev and fast_now > slow_now
        bearish_cross = fast_prev >= slow_prev and fast_now < slow_now

        if bullish_cross:
            self._watch = _Watch(direction="up")
        elif bearish_cross:
            self._watch = _Watch(direction="down")

        if self._watch is None:
            return None

        if self._watch.direction == "up":
            if fast_now < slow_now:
                self._watch = None  # trend broke before a pullback signal
                return None

            # "Touch" = the low comes down to EMA-fast, or within tolerance of it.
            touched = candle.low <= fast_now * (1 + self.touch_tolerance)
            if touched and self._watch.armed:
                self._watch.armed = False
                return Signal("up", candle, fast_now, slow_now)

            if not self._watch.armed and candle.close > fast_now * (1 + self.rearm_tolerance):
                self._watch.armed = True

        else:  # down
            if fast_now > slow_now:
                self._watch = None
                return None

            # "Touch" = the high comes up to EMA-fast, or within tolerance of it.
            touched = candle.high >= fast_now * (1 - self.touch_tolerance)
            if touched and self._watch.armed:
                self._watch.armed = False
                return Signal("down", candle, fast_now, slow_now)

            if not self._watch.armed and candle.close < fast_now * (1 - self.rearm_tolerance):
                self._watch.armed = True

        return None
