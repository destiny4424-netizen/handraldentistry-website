"""Poll Delta Exchange candles, run the EMA20/50 crossover+pullback strategy,
and send a Telegram alert whenever a new entry signal fires.

Alerts only -- this bot never places orders. Run it under systemd on the
droplet (see deploy/ema-bot.service) so it restarts automatically.
"""
from __future__ import annotations

import logging
import time
from datetime import datetime, timezone

from config import load_config
from delta_client import DeltaClient
from strategy import CrossoverPullbackStrategy, Signal
from telegram_notifier import TelegramNotifier


def resolution_seconds(resolution: str) -> int:
    try:
        return int(resolution) * 60
    except ValueError:
        return 24 * 60 * 60  # e.g. "1D"


def drop_unclosed_candle(candles: list, candle_seconds: int) -> list:
    """Delta's most recent candle may still be forming; only act on closed ones."""
    if not candles:
        return candles
    now = time.time()
    if candles[-1].time + candle_seconds > now:
        return candles[:-1]
    return candles


def format_signal(signal: Signal, symbol: str, resolution: str) -> str:
    when = datetime.fromtimestamp(signal.candle.time, tz=timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    arrow = "\U0001F7E2 LONG" if signal.direction == "up" else "\U0001F534 SHORT"
    return (
        f"<b>{arrow} setup - {symbol} ({resolution}m)</b>\n"
        f"EMA20/EMA50 crossover, pullback to EMA20 confirmed.\n\n"
        f"Candle: {when}\n"
        f"Close: {signal.candle.close:.2f}\n"
        f"Low/High: {signal.candle.low:.2f} / {signal.candle.high:.2f}\n"
        f"EMA20: {signal.ema_fast:.2f}\n"
        f"EMA50: {signal.ema_slow:.2f}\n\n"
        f"This is an alert only -- no order was placed."
    )


def main() -> None:
    cfg = load_config()

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
        handlers=[logging.FileHandler(cfg.log_file), logging.StreamHandler()],
    )
    logger = logging.getLogger("ema_bot")

    client = DeltaClient(cfg.delta_base_url, cfg.delta_symbol, cfg.delta_resolution)
    strategy = CrossoverPullbackStrategy(
        ema_fast=cfg.ema_fast,
        ema_slow=cfg.ema_slow,
        touch_tolerance=cfg.touch_tolerance,
        rearm_tolerance=cfg.rearm_tolerance,
    )
    notifier = TelegramNotifier(cfg.telegram_bot_token, cfg.telegram_chat_id)
    candle_seconds = resolution_seconds(cfg.delta_resolution)

    logger.info(
        "Starting EMA%d/EMA%d crossover+pullback bot for %s (%sm) on %s",
        cfg.ema_fast, cfg.ema_slow, cfg.delta_symbol, cfg.delta_resolution, cfg.delta_base_url,
    )

    warmed_up = False

    while True:
        try:
            candles = client.fetch_candles(cfg.candle_lookback)
            candles = drop_unclosed_candle(candles, candle_seconds)

            if len(candles) < cfg.ema_slow:
                logger.warning("Not enough closed candles yet (%d/%d)", len(candles), cfg.ema_slow)
            else:
                signals = strategy.process(candles)

                if not warmed_up:
                    # Don't alert on signals buried in historical backfill on startup.
                    logger.info("Warm-up complete on %d candles, %d historical signal(s) ignored", len(candles), len(signals))
                    warmed_up = True
                else:
                    for signal in signals:
                        text = format_signal(signal, cfg.delta_symbol, cfg.delta_resolution)
                        logger.info("Signal: %s", text.replace("\n", " | "))
                        notifier.send(text)

        except Exception:
            logger.exception("Error in poll loop")

        time.sleep(cfg.poll_seconds)


if __name__ == "__main__":
    main()
