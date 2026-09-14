import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from delta_client import Candle
from strategy import CrossoverPullbackStrategy, ema_series


def make_candles(closes, t0=0, step=900):
    candles = []
    for i, close in enumerate(closes):
        t = t0 + i * step
        candles.append(Candle(time=t, open=close, high=close, low=close, close=close))
    return candles


def test_bullish_crossover_then_pullback_signals():
    # 60 flat candles to seed EMA50, then a rally that pushes EMA20 above
    # EMA50, then a pullback candle whose low touches EMA20.
    flat = [100.0] * 60
    rally = [100 + i * 2 for i in range(1, 21)]  # 102..140

    candles = make_candles(flat + rally)
    # EMA20 lags well behind price during a rally -- find where it actually
    # sits so the pullback candle's low genuinely reaches it.
    fast = ema_series([c.close for c in candles], 20)
    ema20_at_last = fast[-1]

    last = candles[-1]
    candles[-1] = Candle(
        time=last.time, open=last.open, high=last.high, low=ema20_at_last * 0.999, close=last.close
    )

    strat = CrossoverPullbackStrategy(ema_fast=20, ema_slow=50, touch_tolerance=0.0015, rearm_tolerance=0.003)
    signals = strat.process(candles)

    assert any(s.direction == "up" for s in signals), "expected a bullish pullback signal"
    print("OK: bullish crossover + pullback produced a signal")


def test_no_signal_without_pullback():
    flat = [100.0] * 60
    rally = [100 + i * 5 for i in range(1, 21)]  # steep, no pullback near EMA20

    candles = make_candles(flat + rally)
    strat = CrossoverPullbackStrategy(ema_fast=20, ema_slow=50, touch_tolerance=0.0015, rearm_tolerance=0.003)
    signals = strat.process(candles)

    assert signals == [], f"expected no signals without a pullback, got {signals}"
    print("OK: no pullback -> no signal")


def test_incremental_calls_do_not_duplicate_signals():
    flat = [100.0] * 60
    rally = [100 + i * 2 for i in range(1, 21)]

    candles = make_candles(flat + rally)
    fast = ema_series([c.close for c in candles], 20)
    ema20_at_last = fast[-1]

    last = candles[-1]
    candles[-1] = Candle(
        time=last.time, open=last.open, high=last.high, low=ema20_at_last * 0.999, close=last.close
    )

    strat = CrossoverPullbackStrategy(ema_fast=20, ema_slow=50, touch_tolerance=0.0015, rearm_tolerance=0.003)
    first = strat.process(candles)
    second = strat.process(candles)  # same data again, nothing new

    assert len(first) >= 1
    assert second == []
    print("OK: re-processing the same candles does not re-signal")


if __name__ == "__main__":
    test_bullish_crossover_then_pullback_signals()
    test_no_signal_without_pullback()
    test_incremental_calls_do_not_duplicate_signals()
    print("All strategy tests passed")
