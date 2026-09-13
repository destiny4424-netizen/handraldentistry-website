#!/usr/bin/env python3
"""
Generates SYNTHETIC 1-minute OHLC data for smoke-testing backtest.py.

This is NOT real market data and must never be used to draw conclusions
about a strategy's real-world performance. It exists only to prove the
backtest engine runs end-to-end. Feed backtest.py real XAU/USD 1-minute
data (see README.md) to get meaningful results.
"""

import argparse

import numpy as np
import pandas as pd


def generate(days: int, seed: int, out_path: str):
    rng = np.random.default_rng(seed)
    start = pd.Timestamp("2026-08-01", tz="Asia/Kolkata")
    rows = []
    price = 2400.0

    for d in range(days):
        day = start + pd.Timedelta(days=d)
        if day.weekday() >= 5:  # skip weekends, gold markets are closed
            continue
        minutes = pd.date_range(day, day + pd.Timedelta(hours=23, minutes=59), freq="1min", tz="Asia/Kolkata")
        for ts in minutes:
            drift = rng.normal(0, 0.15)
            price = max(1.0, price + drift)
            o = price
            h = o + abs(rng.normal(0, 0.2))
            l = o - abs(rng.normal(0, 0.2))
            c = rng.uniform(l, h)
            rows.append((ts, o, h, l, c))
            price = c

    df = pd.DataFrame(rows, columns=["timestamp", "open", "high", "low", "close"])
    df.to_csv(out_path, index=False)
    print(f"Wrote {len(df)} synthetic 1-minute bars across ~{days} calendar days to {out_path}")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=int, default=20)
    ap.add_argument("--seed", type=int, default=42)
    ap.add_argument("--out", default="synthetic_xauusd_1m.csv")
    args = ap.parse_args()
    generate(args.days, args.seed, args.out)
