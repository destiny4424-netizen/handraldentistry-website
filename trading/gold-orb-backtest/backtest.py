#!/usr/bin/env python3
"""
Opening-Range Breakout backtest engine.

Strategy: mark the high/low of the opening 15-minute candle of the session
(default 05:30-05:45 IST). After that candle closes, enter long if price
breaks above the range high, or short if price breaks below the range low.
Manage the trade with an initial stop at the opposite side of the range,
then a ratcheting trailing stop.

Feed it your own 1-minute (or finer) OHLC CSV -- this script does not
fetch data itself. See README.md for the expected CSV format and for
where to obtain real historical XAU/USD data.
"""

import argparse
import sys
from dataclasses import dataclass, field
from typing import Optional

import numpy as np
import pandas as pd


@dataclass
class Config:
    tz: str = "Asia/Kolkata"
    range_start: str = "05:30"
    range_end: str = "05:45"          # exclusive: bars in [range_start, range_end)
    eod_cutoff: str = "23:59"         # force-flat time if still open, same IST day
    trail_mult: float = 1.0           # trailing distance = trail_mult * R behind the peak
    breakeven_r: float = 1.0          # move stop to breakeven once price reaches this many R in favor
    one_trade_per_day: bool = True
    spread_points: float = 0.0        # round-turn cost added to entry, subtracted at exit (in price units)
    min_range_points: float = 0.0     # skip days whose opening range is smaller than this (avoids noise trades)


@dataclass
class Trade:
    day: pd.Timestamp
    direction: str
    entry_time: pd.Timestamp
    entry_price: float
    initial_stop: float
    range_high: float
    range_low: float
    exit_time: Optional[pd.Timestamp] = None
    exit_price: Optional[float] = None
    exit_reason: Optional[str] = None
    r_multiple: Optional[float] = None
    points: Optional[float] = None


def load_csv(path: str, tz: str) -> pd.DataFrame:
    df = pd.read_csv(path)
    cols = {c.lower().strip(): c for c in df.columns}
    required = ["timestamp", "open", "high", "low", "close"]
    missing = [c for c in required if c not in cols]
    if missing:
        raise ValueError(
            f"CSV is missing required column(s): {missing}. "
            f"Expected headers: timestamp,open,high,low,close[,volume]"
        )
    df = df.rename(columns={cols[c]: c for c in required})
    df["timestamp"] = pd.to_datetime(df["timestamp"], utc=True)
    df = df.set_index("timestamp").sort_index()
    df.index = df.index.tz_convert(tz)
    return df[["open", "high", "low", "close"]]


def compute_opening_ranges(df: pd.DataFrame, cfg: Config) -> pd.DataFrame:
    ranges = []
    for day, day_df in df.groupby(df.index.date):
        window = day_df.between_time(cfg.range_start, cfg.range_end, inclusive="left")
        if window.empty:
            continue
        ranges.append(
            {
                "day": pd.Timestamp(day),
                "range_high": window["high"].max(),
                "range_low": window["low"].min(),
                "range_end_time": window.index.max(),
            }
        )
    return pd.DataFrame(ranges).set_index("day")


def run_backtest(df: pd.DataFrame, cfg: Config) -> list:
    ranges = compute_opening_ranges(df, cfg)
    trades = []

    for day, day_df in df.groupby(df.index.date):
        day_ts = pd.Timestamp(day)
        if day_ts not in ranges.index:
            continue
        r = ranges.loc[day_ts]
        range_high, range_low, range_end_time = r["range_high"], r["range_low"], r["range_end_time"]

        if (range_high - range_low) < cfg.min_range_points:
            continue

        after_range = day_df[day_df.index > range_end_time]
        cutoff = pd.Timestamp.combine(day_ts.date(), pd.to_datetime(cfg.eod_cutoff).time()).tz_localize(cfg.tz)
        after_range = after_range[after_range.index <= cutoff]
        if after_range.empty:
            continue

        open_trade = None
        for ts, bar in after_range.iterrows():
            if open_trade is None:
                if bar["high"] > range_high:
                    entry_price = range_high + cfg.spread_points
                    open_trade = Trade(
                        day=day_ts, direction="long", entry_time=ts, entry_price=entry_price,
                        initial_stop=range_low, range_high=range_high, range_low=range_low,
                    )
                elif bar["low"] < range_low:
                    entry_price = range_low - cfg.spread_points
                    open_trade = Trade(
                        day=day_ts, direction="short", entry_time=ts, entry_price=entry_price,
                        initial_stop=range_high, range_high=range_high, range_low=range_low,
                    )
                if open_trade is not None and cfg.one_trade_per_day:
                    pass
                continue

            # manage open trade
            R = abs(open_trade.entry_price - open_trade.initial_stop)
            if open_trade.direction == "long":
                favorable_extreme = day_df.loc[open_trade.entry_time:ts, "high"].max()
                stop = open_trade.initial_stop
                if favorable_extreme - open_trade.entry_price >= cfg.breakeven_r * R:
                    stop = max(stop, open_trade.entry_price)
                trail = favorable_extreme - cfg.trail_mult * R
                stop = max(stop, trail)
                if bar["low"] <= stop:
                    open_trade.exit_time = ts
                    open_trade.exit_price = stop - cfg.spread_points
                    open_trade.exit_reason = "trailing_stop"
                    break
            else:
                favorable_extreme = day_df.loc[open_trade.entry_time:ts, "low"].min()
                stop = open_trade.initial_stop
                if open_trade.entry_price - favorable_extreme >= cfg.breakeven_r * R:
                    stop = min(stop, open_trade.entry_price)
                trail = favorable_extreme + cfg.trail_mult * R
                stop = min(stop, trail)
                if bar["high"] >= stop:
                    open_trade.exit_time = ts
                    open_trade.exit_price = stop + cfg.spread_points
                    open_trade.exit_reason = "trailing_stop"
                    break

        if open_trade is not None:
            if open_trade.exit_time is None:
                # force flat at end-of-day cutoff
                last_bar = after_range.iloc[-1]
                open_trade.exit_time = after_range.index[-1]
                open_trade.exit_price = last_bar["close"]
                open_trade.exit_reason = "eod_cutoff"

            R = abs(open_trade.entry_price - open_trade.initial_stop)
            sign = 1 if open_trade.direction == "long" else -1
            points = sign * (open_trade.exit_price - open_trade.entry_price)
            open_trade.points = points
            open_trade.r_multiple = points / R if R else 0.0
            trades.append(open_trade)

    return trades


def summarize(trades: list) -> dict:
    if not trades:
        return {"trade_count": 0}
    r_multiples = np.array([t.r_multiple for t in trades])
    points = np.array([t.points for t in trades])
    wins = r_multiples[r_multiples > 0]
    losses = r_multiples[r_multiples <= 0]
    equity_r = np.cumsum(r_multiples)
    running_max = np.maximum.accumulate(equity_r)
    drawdown = equity_r - running_max

    return {
        "trade_count": len(trades),
        "win_rate": len(wins) / len(trades) if trades else 0.0,
        "avg_r": r_multiples.mean(),
        "total_r": r_multiples.sum(),
        "total_points": points.sum(),
        "profit_factor": (wins.sum() / -losses.sum()) if losses.sum() != 0 else float("inf"),
        "max_drawdown_r": drawdown.min(),
        "best_trade_r": r_multiples.max(),
        "worst_trade_r": r_multiples.min(),
        "long_trades": sum(1 for t in trades if t.direction == "long"),
        "short_trades": sum(1 for t in trades if t.direction == "short"),
    }


def trades_to_dataframe(trades: list) -> pd.DataFrame:
    rows = [
        {
            "day": t.day.date(),
            "direction": t.direction,
            "entry_time": t.entry_time,
            "entry_price": t.entry_price,
            "initial_stop": t.initial_stop,
            "range_high": t.range_high,
            "range_low": t.range_low,
            "exit_time": t.exit_time,
            "exit_price": t.exit_price,
            "exit_reason": t.exit_reason,
            "points": t.points,
            "r_multiple": t.r_multiple,
        }
        for t in trades
    ]
    return pd.DataFrame(rows)


def main():
    ap = argparse.ArgumentParser(description="Opening-range breakout backtest")
    ap.add_argument("csv", help="Path to OHLC CSV: timestamp,open,high,low,close")
    ap.add_argument("--tz", default="Asia/Kolkata")
    ap.add_argument("--range-start", default="05:30")
    ap.add_argument("--range-end", default="05:45")
    ap.add_argument("--eod-cutoff", default="23:59")
    ap.add_argument("--trail-mult", type=float, default=1.0)
    ap.add_argument("--breakeven-r", type=float, default=1.0)
    ap.add_argument("--spread-points", type=float, default=0.0)
    ap.add_argument("--min-range-points", type=float, default=0.0)
    ap.add_argument("--out-trades", default=None, help="Optional path to write trade log CSV")
    args = ap.parse_args()

    cfg = Config(
        tz=args.tz,
        range_start=args.range_start,
        range_end=args.range_end,
        eod_cutoff=args.eod_cutoff,
        trail_mult=args.trail_mult,
        breakeven_r=args.breakeven_r,
        spread_points=args.spread_points,
        min_range_points=args.min_range_points,
    )

    df = load_csv(args.csv, cfg.tz)
    trades = run_backtest(df, cfg)
    stats = summarize(trades)

    print("=== Opening Range Breakout Backtest ===")
    print(f"Data range      : {df.index.min()} -> {df.index.max()}")
    print(f"Range window    : {cfg.range_start}-{cfg.range_end} {cfg.tz}")
    print(f"Trailing mult   : {cfg.trail_mult}R, breakeven at {cfg.breakeven_r}R")
    print()
    if stats["trade_count"] == 0:
        print("No trades generated -- check your data covers the range window and has enough days.")
        sys.exit(0)

    print(f"Trades          : {stats['trade_count']} (long={stats['long_trades']}, short={stats['short_trades']})")
    print(f"Win rate        : {stats['win_rate']*100:.1f}%")
    print(f"Avg R / trade   : {stats['avg_r']:.2f}")
    print(f"Total R         : {stats['total_r']:.2f}")
    print(f"Total points    : {stats['total_points']:.2f}")
    print(f"Profit factor   : {stats['profit_factor']:.2f}")
    print(f"Max drawdown    : {stats['max_drawdown_r']:.2f}R")
    print(f"Best / worst    : {stats['best_trade_r']:.2f}R / {stats['worst_trade_r']:.2f}R")

    if args.out_trades:
        trades_to_dataframe(trades).to_csv(args.out_trades, index=False)
        print(f"\nTrade log written to {args.out_trades}")


if __name__ == "__main__":
    main()
