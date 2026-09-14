#!/usr/bin/env bash
# Run this ON THE DROPLET (as root or with sudo) after cloning/copying this
# project to /opt/ema-crossover-telegram-bot. Idempotent-ish: safe to re-run.
set -euo pipefail

APP_DIR="/opt/ema-crossover-telegram-bot"
SERVICE_USER="ema-bot"

if [[ $EUID -ne 0 ]]; then
  echo "Run this script as root (sudo)." >&2
  exit 1
fi

if [[ ! -d "$APP_DIR" ]]; then
  echo "$APP_DIR does not exist. Copy the bot's files there first, e.g.:" >&2
  echo "  git clone <your repo url> /tmp/repo && cp -r /tmp/repo/ema-crossover-telegram-bot $APP_DIR" >&2
  exit 1
fi

id -u "$SERVICE_USER" &>/dev/null || useradd --system --no-create-home --shell /usr/sbin/nologin "$SERVICE_USER"

apt-get update -y
apt-get install -y python3 python3-venv

python3 -m venv "$APP_DIR/venv"
"$APP_DIR/venv/bin/pip" install --upgrade pip
"$APP_DIR/venv/bin/pip" install -r "$APP_DIR/requirements.txt"

if [[ ! -f "$APP_DIR/.env" ]]; then
  cp "$APP_DIR/.env.example" "$APP_DIR/.env"
  echo "Created $APP_DIR/.env from the example -- edit it with your real Delta and Telegram credentials before starting the service."
fi

mkdir -p /var/log/ema-bot
chown -R "$SERVICE_USER":"$SERVICE_USER" "$APP_DIR" /var/log/ema-bot
chmod 600 "$APP_DIR/.env"

cp "$APP_DIR/deploy/ema-bot.service" /etc/systemd/system/ema-bot.service
systemctl daemon-reload
systemctl enable ema-bot

echo
echo "Setup done. Edit $APP_DIR/.env with real credentials, then run:"
echo "  systemctl start ema-bot"
echo "  systemctl status ema-bot"
echo "  journalctl -u ema-bot -f    # or: tail -f /var/log/ema-bot/ema-bot.log"
