import os
from dataclasses import dataclass

from dotenv import load_dotenv

load_dotenv()


def _env(name: str, default: str | None = None) -> str:
    value = os.getenv(name, default)
    if value is None:
        raise RuntimeError(f"Missing required environment variable: {name}")
    return value


@dataclass(frozen=True)
class Config:
    delta_base_url: str
    delta_symbol: str
    delta_resolution: str
    ema_fast: int
    ema_slow: int
    candle_lookback: int
    poll_seconds: int
    touch_tolerance: float
    rearm_tolerance: float
    telegram_bot_token: str
    telegram_chat_id: str
    log_file: str


def load_config() -> Config:
    return Config(
        delta_base_url=_env("DELTA_BASE_URL", "https://testnet-api.delta.exchange").rstrip("/"),
        delta_symbol=_env("DELTA_SYMBOL", "BTCUSD"),
        delta_resolution=_env("DELTA_RESOLUTION", "15"),
        ema_fast=int(_env("EMA_FAST", "20")),
        ema_slow=int(_env("EMA_SLOW", "50")),
        candle_lookback=int(_env("CANDLE_LOOKBACK", "200")),
        poll_seconds=int(_env("POLL_SECONDS", "60")),
        touch_tolerance=float(_env("TOUCH_TOLERANCE", "0.0015")),
        rearm_tolerance=float(_env("REARM_TOLERANCE", "0.003")),
        telegram_bot_token=_env("TELEGRAM_BOT_TOKEN"),
        telegram_chat_id=_env("TELEGRAM_CHAT_ID"),
        log_file=_env("LOG_FILE", "ema_bot.log"),
    )
