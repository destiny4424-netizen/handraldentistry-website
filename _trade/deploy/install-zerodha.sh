#!/usr/bin/env bash
# Stand-alone setup of Handral Trading Desk for a ZERODHA account (Kite Connect). Safe to run again.
# It needs nothing else: works on a fresh Ubuntu/Debian droplet of its own, or next to other apps on a droplet.
#
#   curl -fsSL https://raw.githubusercontent.com/destiny4424-netizen/handraldentistry-website/main/_trade/deploy/install-zerodha.sh | sudo bash
#
# It has its own everything, shared with no other app (the Dhan desk included):
#   code     /opt/handral-zerodha/repo        (its own copy from GitHub)
#   user     zerodha                          (other apps' users can't read its data, nor it theirs)
#   data     /var/lib/handral-zerodha         (Kite keys, the day's login, settings, order log, trades)
#   service  handral-zerodha on 127.0.0.1:3051, and its own updater (handral-zerodha-update.timer, every 5 min)
#   address  its own https address and password (Caddy), by default zerodha.<droplet-ip>.sslip.io
#
# Settings (all optional):
#   ZERODHA_DOMAIN=desk.example.com   web address (needs a DNS A record pointing at this droplet)
#   ZERODHA_PASSWORD=...              choose the password (default: keep the current one, or make a strong one)
#   ZERODHA_NEW_PASSWORD=1            make a new password (signs every device out)
set -euo pipefail

REPO_URL=${ZERODHA_REPO_URL:-https://github.com/destiny4424-netizen/handraldentistry-website.git}
BRANCH=${ZERODHA_BRANCH:-main}
BASE=/opt/handral-zerodha
REPO=$BASE/repo
CONF=/etc/handral-zerodha
LOGIN=$CONF/login.txt
ENVFILE=/etc/default/handral-zerodha
SITE=/etc/caddy/handral-zerodha.caddy
DATA=/var/lib/handral-zerodha
PORT=3051

main() {
  [ "$(id -u)" = 0 ] || { echo "Please run with sudo."; exit 1; }

  step "Installing packages"
  export DEBIAN_FRONTEND=noninteractive
  apt-get update -qq
  apt-get install -y -qq git python3 python3-cryptography curl ca-certificates >/dev/null

  step "Its own user and data folder"
  id zerodha >/dev/null 2>&1 || useradd --system --home-dir "$DATA" --shell /usr/sbin/nologin zerodha
  install -d -o zerodha -g zerodha -m 750 "$DATA"
  install -d -m 755 "$CONF"
  echo "$BRANCH" > "$CONF/branch"

  step "Its own copy of the code ($BRANCH)"
  if [ -d "$REPO/.git" ]; then
    git -C "$REPO" remote set-url origin "$REPO_URL"
    git -C "$REPO" fetch --quiet origin "$BRANCH"
    git -C "$REPO" checkout --quiet -B "$BRANCH" "origin/$BRANCH"
    git -C "$REPO" reset --quiet --hard "origin/$BRANCH"
  else
    install -d -m 755 "$BASE"
    git clone --quiet --branch "$BRANCH" "$REPO_URL" "$REPO"
  fi

  step "Address and password"
  prepare

  step "Installing and starting the service and its updater"
  install -m 755 "$REPO/_trade/deploy/zerodha/update.sh" /usr/local/sbin/handral-zerodha-update
  /usr/local/sbin/handral-zerodha-update --install

  step "Secure web address"
  local web_ok=1
  web || web_ok=0

  local ip
  ip=$(sed -n 's/^MT_PUBLIC_IP=//p' "$ENVFILE")
  cat <<MSG

Done. The Zerodha desk runs on this server around the clock, starts on boot, restarts if it stops,
and installs new versions from GitHub by itself (rolling back if one doesn't start).

ONE-TIME SETUP ON ZERODHA (logged in as the account holder):
  1. developers.kite.trade -> Create new app (type: Kite Connect; Zerodha charges a monthly fee)
       Redirect URL:  https://$DOMAIN/kite/callback
       Static IP:     $ip   (Zerodha accepts API orders only from the registered IP)
  2. Open the desk below -> Account -> Zerodha login -> paste the app's API key and API secret -> Save
  3. Tap "Log in with Zerodha" (Zerodha's own page, with the TOTP). Zerodha ends each login at 6 AM,
     so log in once every trading morning.

If something looks wrong:
  systemctl status handral-zerodha
  journalctl -u handral-zerodha -u handral-zerodha-update -n 50
MSG
  echo
  echo "Open this on the phone (Safari) or PC and sign in with the password:"
  echo "------------------------------------------------------------"
  cat "$LOGIN"
  echo "------------------------------------------------------------"
  echo "To see it again: sudo cat $LOGIN"
  [ "$web_ok" = 1 ] || echo "(The https address is not answering yet; see the message above.)"
}

step() { echo "==> $*"; }

public_ip() {
  curl -fsS -m 3 http://169.254.169.254/metadata/v1/interfaces/public/0/ipv4/address 2>/dev/null \
    || curl -fsS -m 5 https://api.ipify.org 2>/dev/null || hostname -I | awk '{print $1}'
}

prepare() {
  local ip password=""
  ip=$(public_ip)
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
  if [ -n "$extra" ]; then echo "$extra" >> "$ENVFILE"; fi
  printf 'Address: https://%s\nPassword: %s\n' "$DOMAIN" "$password" > "$LOGIN"
  chmod 600 "$ENVFILE" "$LOGIN"
  umask 022
  echo "    Address: https://$DOMAIN"
}

install_caddy() {
  command -v caddy >/dev/null && return 0
  echo "    Installing Caddy (web server with automatic HTTPS)"
  apt-get install -y -qq debian-keyring debian-archive-keyring apt-transport-https gnupg >/dev/null 2>&1 || true
  if curl -fsSL https://dl.cloudsmith.io/public/caddy/stable/gpg.key \
       | gpg --batch --yes --dearmor -o /usr/share/keyrings/caddy-stable-archive-keyring.gpg \
     && curl -fsSL https://dl.cloudsmith.io/public/caddy/stable/debian.deb.txt \
       > /etc/apt/sources.list.d/caddy-stable.list; then
    apt-get update -qq
  fi
  apt-get install -y -qq caddy >/dev/null
}

web() {
  install_caddy
  local busy
  busy=$(ss -ltnpH '( sport = :80 or sport = :443 )' 2>/dev/null | grep -v '"caddy"' || true)
  if [ -n "$busy" ]; then
    echo "    Another web server is using port 80 or 443:"; echo "$busy"
    echo "    The desk itself is running on 127.0.0.1:$PORT; the https address needs those ports."
    return 1
  fi
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
  # Keep any other sites in the Caddyfile; replace only Caddy's stock example.
  local main_cf=/etc/caddy/Caddyfile
  if [ ! -f "$main_cf" ] || grep -q 'The Caddyfile is an easy way to configure' "$main_cf"; then
    echo "import $SITE" > "$main_cf"
  elif ! grep -q "import $SITE" "$main_cf"; then
    printf '\nimport %s\n' "$SITE" >> "$main_cf"
  fi
  if ! caddy validate --config "$main_cf" --adapter caddyfile >/dev/null 2>&1; then
    echo "    Caddy configuration is invalid:"
    caddy validate --config "$main_cf" --adapter caddyfile || true
    return 1
  fi
  if command -v ufw >/dev/null && ufw status | grep -q 'Status: active'; then
    ufw allow 80/tcp >/dev/null; ufw allow 443/tcp >/dev/null
  fi
  systemctl enable caddy >/dev/null 2>&1
  systemctl reload caddy 2>/dev/null || systemctl restart caddy
  local code
  echo "    Getting the security certificate for $DOMAIN (can take a minute)"
  for _ in $(seq 1 24); do
    code=$(curl -s -o /dev/null -w '%{http_code}' -m 5 "https://$DOMAIN/healthz" || true)
    [ "$code" = 200 ] && { echo "    https://$DOMAIN is working."; return 0; }
    sleep 5
  done
  echo "    Not reachable yet. If the droplet has a DigitalOcean cloud firewall, allow inbound 80 and 443."
  return 1
}

main "$@"; exit $?
