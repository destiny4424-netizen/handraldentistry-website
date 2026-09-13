# Gold Opening-Range Breakout Backtest

Backtests the strategy: mark the high/low of the 05:30-05:45 IST candle,
then trade a breakout of either side, managed with a trailing stop.

## Important limitation

This sandbox's network policy blocks all market-data providers (Yahoo
Finance, Stooq, Twelve Data, etc. all returned `403 policy denial` when
tested) — only dev-infra domains like npm/pypi/github are reachable. So
this backtest **could not be run on real historical XAU/USD data** from
here. What's included instead:

- `backtest.py` — the full, working backtest engine.
- `generate_synthetic_data.py` — makes a synthetic (fake, random-walk)
  1-minute price series purely to prove the engine runs end-to-end.
  `trades_demo.csv` and the console output you see from it are **not**
  a real performance result — a random walk has no exploitable structure,
  so don't read anything into its win rate.

To get a real answer, run `backtest.py` yourself against real data:

1. Export 1-minute (or finer) OHLC XAU/USD history as CSV, columns
   `timestamp,open,high,low,close` (timestamp must be parseable and
   ideally UTC or tz-aware). Sources: your broker/MT4/MT5 export,
   Dukascopy's free historical data downloader, TradingView export, or
   a paid API (Twelve Data, Polygon, etc.).
2. Run:
   ```
   python3 backtest.py your_data.csv --out-trades trades.csv
   ```
3. Read the console summary (win rate, average R, profit factor, max
   drawdown) and inspect `trades.csv` for the full trade log.

## Strategy rules implemented

- **Opening range**: high/low of all bars in `[05:30, 05:45)` IST (config­urable via `--range-start`/`--range-end`/`--tz`).
- **Entry**: first bar after 05:45 whose high exceeds the range high (long) or whose low breaks the range low (short). Only one trade per day by default.
- **Initial stop**: opposite side of the opening range.
- **Trailing stop**: once price has moved `--breakeven-r` (default 1R) in favor, the stop moves to breakeven; from there it ratchets to stay `--trail-mult` × R (default 1R) behind the best price reached since entry — it only ever tightens, never loosens.
- **Forced exit**: if neither stop nor target logic closes the trade, it's flattened at `--eod-cutoff` (default 23:59 IST) so trades never carry into the next day's opening range.
- **Costs**: `--spread-points` adds a round-turn cost to entry/exit (0 by default — set this to your broker's typical XAU/USD spread in price points, e.g. 0.3-0.5, for realistic results).

All of this is configurable from the CLI — run `python3 backtest.py --help`.

## Things worth testing once you have real data

- Try different `--trail-mult` values (tighter vs. looser trailing) — opening-range breakouts often whipsaw right after the break, so a too-tight trail can chop you out immediately.
- Set `--min-range-points` to skip days where the 15-minute range is too small to trade profitably after spread.
- Check whether the "5:30-5:45 IST" window lines up with an actual liquidity event for gold (e.g. a session open) versus a quiet clock time — a breakout strategy only works when the range is set by real order flow, not by an arbitrary time window with no volume behind it.
- A win rate under ~35-40% is normal for breakout systems that rely on a few large winners (positive R) to offset many small losers — judge it by average R and profit factor, not win rate alone.
