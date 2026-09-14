# EMA 20/50 Crossover + Pullback Telegram Alert Bot

Watches a Delta Exchange market, waits for an EMA20/EMA50 crossover followed
by a pullback that touches EMA20, and sends a Telegram alert. **Alerts only
-- it never places an order.**

## Strategy

1. Compute EMA20 and EMA50 on candle closes.
2. **Crossover**: EMA20 crossing above EMA50 opens an "uptrend watch";
   crossing below opens a "downtrend watch".
3. **Pullback**: while the trend holds, wait for a candle whose low (uptrend)
   or high (downtrend) comes back to touch EMA20. That touch is the alert.
4. **Trend break**: if EMA20 crosses back over EMA50 before a pullback
   happens, the watch is dropped -- wait for the next crossover.
5. **Re-arm**: after an alert, price must close clearly away from EMA20
   again before a second touch in the same trend can alert again (stops
   repeated alerts while price chops along the EMA).

This is signal logic only -- it does not manage stop-loss, targets, or
position size. Treat alerts as "worth looking at," not an execution
guarantee.

## Files

- `bot.py` -- main poll loop.
- `strategy.py` -- EMA + crossover/pullback state machine (`tests/test_strategy.py` covers it).
- `delta_client.py` -- fetches candles from Delta Exchange's public
  `GET /v2/history/candles` endpoint (no API key needed for alerts-only mode).
- `telegram_notifier.py` -- sends the alert via the Telegram Bot API.
- `config.py` / `.env.example` -- all settings, via environment variables.
- `deploy/` -- systemd unit + droplet setup script.

## Before you deploy: verify against Delta's current docs

I could not reach Delta Exchange's docs from this environment, so the
endpoint path, param names (`resolution`/`symbol`/`start`/`end`) and response
field names (`time`/`open`/`high`/`low`/`close`) in `delta_client.py` are
based on Delta's previously documented public API and **may have changed**.
Before going live:

1. Confirm your testnet base URL and the exact product `symbol` string via
   `GET {DELTA_BASE_URL}/v2/products`.
2. Hit `GET {DELTA_BASE_URL}/v2/history/candles?resolution=15&symbol=BTCUSD&start=...&end=...`
   yourself (curl/Postman) and compare the response shape to `_parse_candles`
   in `delta_client.py`. Adjust field names there if they differ.

## Setup

### 1. Telegram bot

1. Message `@BotFather` on Telegram, `/newbot`, follow the prompts -- you get
   a bot token.
2. Message your new bot once (anything), then visit
   `https://api.telegram.org/bot<TOKEN>/getUpdates` and read your numeric
   `chat.id` from the JSON. That's `TELEGRAM_CHAT_ID`.

### 2. Delta Exchange

Alerts-only mode only needs a base URL and symbol -- no API key. Get a
testnet account/URL from Delta if you don't already have one.

### 3. Local config

```bash
cd ema-crossover-telegram-bot
cp .env.example .env
# edit .env: DELTA_BASE_URL, DELTA_SYMBOL, TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID
python3 -m venv venv && source venv/bin/activate
pip install -r requirements.txt
python3 tests/test_strategy.py   # sanity-check the strategy logic
python3 bot.py                    # run locally to confirm it starts + polls
```

On startup the bot "warms up" silently on historical candles (so you don't
get a flood of alerts for old crossovers) and only alerts on signals from
candles that close after it started.

## Deploying to your droplet

```bash
# On your machine: push this repo, then on the droplet:
git clone <your-repo-url> /tmp/repo
sudo cp -r /tmp/repo/ema-crossover-telegram-bot /opt/ema-crossover-telegram-bot
cd /opt/ema-crossover-telegram-bot
sudo ./deploy/setup_droplet.sh
sudo nano .env        # fill in real DELTA_* / TELEGRAM_* values
sudo systemctl start ema-bot
sudo systemctl status ema-bot
sudo journalctl -u ema-bot -f
```

The systemd unit (`deploy/ema-bot.service`) restarts the bot automatically
if it crashes, runs it as an unprivileged `ema-bot` system user, and logs to
`/var/log/ema-bot/ema-bot.log`.

## Going from testnet to production

Change `DELTA_BASE_URL` to Delta's production API URL and `DELTA_SYMBOL` to
the live product symbol, then restart the service. Re-verify the candle
endpoint against production once more before trusting the alerts.

## If you later want auto-execution

This bot deliberately only alerts. Turning alerts into live orders means
adding: authenticated Delta order-placement calls (API key/secret, HMAC
signing), position sizing, stop-loss/target management, duplicate-order
guards, and a kill switch -- that's a separate, higher-risk piece of work
best done once you've watched the alerts against real price action for a
while.
