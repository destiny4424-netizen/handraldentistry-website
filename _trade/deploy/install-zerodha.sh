#!/usr/bin/env bash
# One-time setup of a second Handral Trading Desk for a ZERODHA account (Kite Connect), next to the Dhan desk.
# Run it after the Dhan desk is installed (install.sh); safe to run again.
#
#   curl -fsSL https://raw.githubusercontent.com/destiny4424-netizen/handraldentistry-website/main/_trade/deploy/install-zerodha.sh | sudo bash
#
# What it does:
#   - runs the same desk code as a second service, handral-zerodha, on 127.0.0.1:3051, with its own data
#     (/var/lib/handral-zerodha: settings, order log, trades) and its own password
#   - gives it its own secure web address (Caddy), by default zerodha.<droplet-ip>.sslip.io
#   - the Dhan desk's updater (every 5 minutes) installs new versions for both desks
#
# Settings (all optional):
#   ZERODHA_DOMAIN=zerodha.example.com   web address (needs a DNS A record pointing at this droplet)
#   ZERODHA_PASSWORD=...                 choose the password (default: keep the current one, or make a strong one)
#   ZERODHA_NEW_PASSWORD=1               make a new password (signs every device out)
set -euo pipefail

CONF=/etc/handral-zerodha
LOGIN=$CONF/login.txt
ENVFILE=/etc/default/handral-zerodha
SITE=/etc/caddy/handral-zerodha.caddy
DATA=/var/lib/handral-zerodha
REPO=/opt/meridian-trade/repo
PORT=3051

main() {
  [ "$(id -u)" = 0 ] || { echo "Please run with sudo."; exit 1; }
  if [ ! -d "$REPO/.git" ] || ! id trade >/dev/null 2>&1; then
    echo "Install the Dhan trading desk first (install.sh); this one runs from the same code."
    exit 1
  fi

  step "Getting the latest code"
  local branch
  branch=$(cat /etc/meridian-trade/branch 2>/dev/null || echo main)
  git -C "$REPO" fetch --quiet origin "$branch" && git -C "$REPO" reset --quiet --hard "origin/$branch" || \
    echo "    (couldn't reach GitHub; using the code already here)"

  step "Data folder and settings"
  install -d -o trade -g trade -m 750 "$DATA"
  install -d -m 755 "$CONF"
  prepare

  step "Installing and starting the service"
  install -m 644 "$REPO/_trade/deploy/handral-zerodha.service" /etc/systemd/system/
  install -m 755 "$REPO/_trade/deploy/update.sh" /usr/local/sbin/meridian-trade-update
  systemctl daemon-reload
  systemctl enable handral-zerodha.service >/dev/null
  systemctl restart handral-zerodha.service
  local i up=0
  for i in $(seq 1 20); do
    [ "$(curl -fsS -m 3 "http://127.0.0.1:$PORT/healthz" 2>/dev/null)" = ok ] && { up=1; break; }
    sleep 1
  done
  [ "$up" = 1 ] || { echo "The Zerodha desk didn't start. See: journalctl -u handral-zerodha -n 50"; exit 1; }

  step "Secure web address"
  web || true

  local ip
  ip=$(sed -n 's/^MT_PUBLIC_IP=//p' "$ENVFILE")
  cat <<MSG

Done. The Zerodha desk runs on this server around the clock, and updates with the Dhan desk.

ONE-TIME SETUP ON ZERODHA (on the Zerodha account holder's login):
  1. developers.kite.trade -> sign up / log in -> Create new app (type: Kite Connect; Zerodha charges for it)
       Redirect URL:  https://$DOMAIN/kite/callback
       Static IP:     $ip   (Zerodha needs it for API orders; family members may share one IP)
  2. Open the desk below -> Account -> Zerodha login -> paste the app's API key and API secret -> Save
  3. Tap "Log in with Zerodha" (Zerodha's own page, with the TOTP). Zerodha ends each login at 6 AM,
     so log in once every trading morning.

Useful only if something looks wrong:
  systemctl status handral-zerodha
  journalctl -u handral-zerodha -n 50
MSG
  echo
  echo "Open this on the phone (Safari) or PC and sign in with the password:"
  echo "------------------------------------------------------------"
  cat "$LOGIN"
  echo "------------------------------------------------------------"
  echo "To see it again: sudo cat $LOGIN"
}

step() { echo "==> $*"; }

prepare() {
  local ip password=""
  ip=$(curl -fsS -m 3 http://169.254.169.254/metadata/v1/interfaces/public/0/ipv4/address 2>/dev/null \
       || curl -fsS -m 5 https://api.ipify.org 2>/dev/null || hostname -I | awk '{print $1}')
  [ -n "$ip" ] || { echo "Could not work out this droplet's public IP."; exit 1; }
  DOMAIN=${ZERODHA_DOMAIN:-$(cat "$CONF/domain" 2>/dev/null || true)}
  [ -n "$DOMAIN" ] || DOMAIN="zerodha.${ip//./-}.sslip.io"
  echo "$DOMAIN" > "$CONF/domain"
  password=${ZERODHA_PASSWORD:-}
  if [ -z "$password" ] && [ -z "${ZERODHA_NEW_PASSWORD:-}" ] && [ -f "$LOGIN" ]; then
    password=$(sed -n 's/^Password: //p' "$LOGIN")
  fi
  if [ -z "$password" ]; then
    password=$(LC_ALL=C tr -dc 'abcdefghjkmnpqrstuvwxyz23456789' < /dev/urandom | head -c 16 || true)
    password="${password:0:4}-${password:4:4}-${password:8:4}-${password:12:4}"
  fi
  local extra=""
  [ -f "$ENVFILE" ] && extra=$(grep -E '^MT_KITE_API_(KEY|SECRET)=' "$ENVFILE" || true)
  umask 077
  printf 'MT_PASSWORD=%s\nMT_PUBLIC_URL=https://%s\nMT_PUBLIC_IP=%s\n' "$password" "$DOMAIN" "$ip" > "$ENVFILE"
  [ -n "$extra" ] && echo "$extra" >> "$ENVFILE"
  printf 'Address: https://%s\nPassword: %s\n' "$DOMAIN" "$password" > "$LOGIN"
  chmod 600 "$ENVFILE" "$LOGIN"
  echo "    Address: https://$DOMAIN"
}

web() {
  command -v caddy >/dev/null || { echo "    Caddy isn't installed (it comes with the Dhan desk's web.sh)."; return 1; }
  cat > "$SITE" <<CADDY
# Managed by install-zerodha.sh; changes here are overwritten.
$DOMAIN {
	encode gzip
	request_body {
		max_size 1MB
	}
	header {
		Strict-Transport-Security "max-age=31536000"
		X-Content-Type-Options nosniff
		X-Frame-Options DENY
		Referrer-Policy no-referrer
		-Server
	}
	reverse_proxy 127.0.0.1:$PORT
}
CADDY
  chmod 644 "$SITE"
  local main_cf=/etc/caddy/Caddyfile
  grep -q "import $SITE" "$main_cf" 2>/dev/null || printf '\nimport %s\n' "$SITE" >> "$main_cf"
  if ! caddy validate --config "$main_cf" --adapter caddyfile >/dev/null 2>&1; then
    echo "    Caddy configuration is invalid; the web address isn't set up:"
    caddy validate --config "$main_cf" --adapter caddyfile || true
    return 1
  fi
  systemctl reload caddy 2>/dev/null || systemctl restart caddy
  local code
  echo "    Getting the security certificate for $DOMAIN (can take a minute)"
  for _ in $(seq 1 24); do
    code=$(curl -s -o /dev/null -w '%{http_code}' -m 5 "https://$DOMAIN/healthz" || true)
    [ "$code" = 200 ] && { echo "    https://$DOMAIN is working."; return 0; }
    sleep 5
  done
  echo "    Not reachable yet; Caddy keeps retrying on its own."
  return 1
}

main "$@"; exit $?
