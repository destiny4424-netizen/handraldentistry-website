#!/usr/bin/env bash
# Puts OrderBlock Scanner on a secure web address, using Caddy (automatic HTTPS
# certificates). Visitors sign in inside the app with the scanner password.
# Run by install.sh; safe to run again. Installed as /usr/local/sbin/orderblock-scanner-web.
#
#   web.sh --prepare   only choose the address and password (written for the service)
#   web.sh             also configure Caddy and wait for the https address to answer
#
# Settings (all optional):
#   SCANNER_DOMAIN=scanner.example.com   web address. Default: scanner.handraldentistry.com
#                                        if it already points at this droplet, else a free
#                                        scanner.<ip>.sslip.io address (no DNS changes needed)
#   SCANNER_PASSWORD=...                 choose the password (default: keep the current one,
#                                        or generate a strong one the first time)
#   SCANNER_NEW_PASSWORD=1               generate a new password (signs every device out)
#
# The address and password are kept in /etc/orderblock-scanner/login.txt (root only).
set -euo pipefail

CONF=/etc/orderblock-scanner
LOGIN=$CONF/login.txt
ENVFILE=/etc/default/orderblock-scanner
SITE=/etc/caddy/orderblock-scanner.caddy
PORT=3030

main() {
  install -d -m 755 "$CONF"
  prepare
  [ "${1:-}" = "--prepare" ] && return 0

  install_caddy
  check_ports || return 1

  cat > "$SITE" <<EOF
# Managed by orderblock-scanner-web; changes here are overwritten.
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
EOF
  chmod 644 "$SITE"

  # Keep the other sites in the Caddyfile (Handral Books); replace only the stock example.
  local main_cf=/etc/caddy/Caddyfile
  if [ ! -f "$main_cf" ] || grep -q 'The Caddyfile is an easy way to configure' "$main_cf"; then
    echo "import $SITE" > "$main_cf"
  elif ! grep -q "import $SITE" "$main_cf"; then
    printf '\nimport %s\n' "$SITE" >> "$main_cf"
  fi
  if ! caddy validate --config "$main_cf" --adapter caddyfile >/dev/null 2>&1; then
    echo "Caddy configuration is invalid:"
    caddy validate --config "$main_cf" --adapter caddyfile || true
    return 1
  fi

  if command -v ufw >/dev/null && ufw status | grep -q 'Status: active'; then
    ufw allow 80/tcp >/dev/null; ufw allow 443/tcp >/dev/null
  fi
  systemctl enable caddy >/dev/null 2>&1
  systemctl reload caddy 2>/dev/null || systemctl restart caddy

  wait_for_https "$DOMAIN"
}

# Work out the address and password, and hand them to the service.
prepare() {
  local ip password=""
  ip=$(public_ip)
  [ -n "$ip" ] || { echo "Could not work out this droplet's public IP."; return 1; }

  DOMAIN=${SCANNER_DOMAIN:-$(cat "$CONF/domain" 2>/dev/null || true)}
  if [ -z "$DOMAIN" ] || [ "$DOMAIN" = "scanner.${ip//./-}.sslip.io" ]; then
    if [ "$(getent ahostsv4 scanner.handraldentistry.com | awk 'NR==1{print $1}')" = "$ip" ]; then
      DOMAIN=scanner.handraldentistry.com
    else
      DOMAIN="scanner.${ip//./-}.sslip.io"
    fi
  fi
  if [ -n "${SCANNER_DOMAIN:-}" ] && \
     [ "$(getent ahostsv4 "$DOMAIN" | awk 'NR==1{print $1}')" != "$ip" ]; then
    echo "    Note: $DOMAIN does not point at $ip yet. Add a DNS A record for it;"
    echo "    the secure certificate is issued automatically once it does."
  fi
  echo "$DOMAIN" > "$CONF/domain"

  password=${SCANNER_PASSWORD:-}
  if [ -z "$password" ] && [ -z "${SCANNER_NEW_PASSWORD:-}" ] && [ -f "$LOGIN" ]; then
    password=$(sed -n 's/^Password: //p' "$LOGIN")
  fi
  [ -n "$password" ] || password=$(new_password)

  umask 077
  printf 'OBS_PASSWORD=%s\nOBS_PUBLIC_URL=https://%s\n' "$password" "$DOMAIN" > "$ENVFILE"
  printf 'Address: https://%s\nPassword: %s\n' "$DOMAIN" "$password" > "$LOGIN"
  chmod 600 "$ENVFILE" "$LOGIN"
  echo "    Address: https://$DOMAIN"
}

public_ip() {
  curl -fsS -m 3 http://169.254.169.254/metadata/v1/interfaces/public/0/ipv4/address 2>/dev/null \
    || curl -fsS -m 5 https://api.ipify.org 2>/dev/null \
    || hostname -I | awk '{print $1}'
}

new_password() {
  # 16 characters without look-alikes (no 0/O, 1/l/I): about 80 bits.
  local p
  p=$(LC_ALL=C tr -dc 'abcdefghjkmnpqrstuvwxyz23456789' < /dev/urandom | head -c 16 || true)
  echo "${p:0:4}-${p:4:4}-${p:8:4}-${p:12:4}"
}

install_caddy() {
  command -v caddy >/dev/null && return 0
  echo "    Installing Caddy (web server with automatic HTTPS)"
  export DEBIAN_FRONTEND=noninteractive
  apt-get install -y -qq debian-keyring debian-archive-keyring apt-transport-https gnupg >/dev/null 2>&1 || true
  if curl -fsSL https://dl.cloudsmith.io/public/caddy/stable/gpg.key \
       | gpg --batch --yes --dearmor -o /usr/share/keyrings/caddy-stable-archive-keyring.gpg \
     && curl -fsSL https://dl.cloudsmith.io/public/caddy/stable/debian.deb.txt \
       > /etc/apt/sources.list.d/caddy-stable.list; then
    apt-get update -qq
  fi
  apt-get install -y -qq caddy >/dev/null
}

# Ports 80 and 443 must be free for Caddy (or already Caddy's).
check_ports() {
  local busy
  busy=$(ss -ltnpH '( sport = :80 or sport = :443 )' 2>/dev/null | grep -v '"caddy"' || true)
  if [ -n "$busy" ]; then
    echo "Another web server is using port 80 or 443:"
    echo "$busy"
    echo "The scanner itself is running; send this output to get the web address set up."
    return 1
  fi
}

wait_for_https() {
  local code
  echo "    Getting the security certificate for $1 (can take a minute)"
  for _ in $(seq 1 24); do
    code=$(curl -s -o /dev/null -w '%{http_code}' -m 5 "https://$1/healthz" || true)
    if [ "$code" = 200 ]; then
      echo "    https://$1 is working."
      return 0
    fi
    sleep 5
  done
  echo "    Not reachable yet. If the droplet has a DigitalOcean cloud firewall, allow"
  echo "    inbound ports 80 and 443. Caddy keeps retrying on its own."
  return 1
}

main "$@"; exit $?
