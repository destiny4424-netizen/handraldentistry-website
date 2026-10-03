#!/usr/bin/env bash
# Puts Handral Books on a secure web address with a password, using Caddy
# (automatic HTTPS certificates). Run by install.sh; safe to run again.
# Installed as /usr/local/sbin/handral-books-web.
#
# Settings (all optional):
#   BOOKS_DOMAIN=books.example.com   web address to use. Default: books.handraldentistry.com
#                                    if it already points at this droplet, else a free
#                                    <ip>.sslip.io address that works with no DNS changes.
#   BOOKS_PASSWORD=...               choose the password (default: keep the current one,
#                                    or generate a strong one the first time)
#   BOOKS_NEW_PASSWORD=1             generate a new password
#
# The address and password are kept in /etc/handral-books/login.txt (root only).
set -euo pipefail

main() {
  local conf=/etc/handral-books login=/etc/handral-books/login.txt user=handral
  local port domain password ip
  port=$( (. /etc/default/handral-books 2>/dev/null; echo "${BOOKS_PORT:-3020}") )
  install -d -m 755 "$conf"

  ip=$(public_ip)
  [ -n "$ip" ] || { echo "Could not work out this droplet's public IP."; return 1; }

  domain=${BOOKS_DOMAIN:-$(cat "$conf/domain" 2>/dev/null || true)}
  if [ -z "$domain" ] || [ "$domain" = "${ip//./-}.sslip.io" ]; then
    if [ "$(getent ahostsv4 books.handraldentistry.com | awk 'NR==1{print $1}')" = "$ip" ]; then
      domain=books.handraldentistry.com
    else
      domain="${ip//./-}.sslip.io"
    fi
  fi
  if [ -n "${BOOKS_DOMAIN:-}" ] && \
     [ "$(getent ahostsv4 "$domain" | awk 'NR==1{print $1}')" != "$ip" ]; then
    echo "    Note: $domain does not point at $ip yet. Add a DNS A record for it;"
    echo "    the secure certificate is issued automatically once it does."
  fi
  echo "$domain" > "$conf/domain"

  password=${BOOKS_PASSWORD:-}
  if [ -z "$password" ] && [ -z "${BOOKS_NEW_PASSWORD:-}" ] && [ -f "$login" ]; then
    password=$(sed -n 's/^Password: //p' "$login")
  fi
  [ -n "$password" ] || password=$(new_password)

  install_caddy
  check_ports || return 1

  local hash
  hash=$(caddy hash-password --plaintext "$password")
  cat > /etc/caddy/handral-books.caddy <<EOF
# Managed by handral-books-web; changes here are overwritten.
$domain {
	encode gzip
	basicauth {
		$user $hash
	}
	request_body {
		max_size 20MB
	}
	header {
		Strict-Transport-Security "max-age=31536000"
		X-Content-Type-Options nosniff
		X-Frame-Options DENY
		Referrer-Policy no-referrer
		-Server
	}
	reverse_proxy 127.0.0.1:$port
}
EOF
  chmod 640 /etc/caddy/handral-books.caddy
  chown root:caddy /etc/caddy/handral-books.caddy 2>/dev/null || true

  # Keep any other sites already in the Caddyfile; replace only the stock example.
  local main_cf=/etc/caddy/Caddyfile
  if [ ! -f "$main_cf" ] || grep -q 'The Caddyfile is an easy way to configure' "$main_cf"; then
    echo "import /etc/caddy/handral-books.caddy" > "$main_cf"
  elif ! grep -q 'import /etc/caddy/handral-books.caddy' "$main_cf"; then
    printf '\nimport /etc/caddy/handral-books.caddy\n' >> "$main_cf"
  fi
  caddy validate --config "$main_cf" --adapter caddyfile >/dev/null 2>&1 || {
    echo "Caddy configuration is invalid:"; caddy validate --config "$main_cf" --adapter caddyfile
    return 1
  }

  if command -v ufw >/dev/null && ufw status | grep -q 'Status: active'; then
    ufw allow 80/tcp >/dev/null; ufw allow 443/tcp >/dev/null
  fi

  systemctl enable caddy >/dev/null 2>&1
  systemctl reload caddy 2>/dev/null || systemctl restart caddy

  umask 077
  printf 'Address: https://%s\nUsername: %s\nPassword: %s\n' "$domain" "$user" "$password" > "$login"
  chmod 600 "$login"

  wait_for_https "$domain" "$user" "$password"
}

public_ip() {
  curl -fsS -m 3 http://169.254.169.254/metadata/v1/interfaces/public/0/ipv4/address 2>/dev/null \
    || curl -fsS -m 5 https://api.ipify.org 2>/dev/null \
    || hostname -I | awk '{print $1}'
}

new_password() {
  # 16 characters without look-alikes (no 0/O, 1/l/I): about 80 bits.
  local p
  p=$(LC_ALL=C tr -dc 'abcdefghjkmnpqrstuvwxyz23456789' < /dev/urandom | head -c 16)
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
    echo "The app itself is running; send this output to get the web address set up."
    return 1
  fi
}

wait_for_https() {
  local i code
  echo "    Getting the security certificate for $1 (can take a minute)"
  for i in $(seq 1 24); do
    code=$(curl -s -o /dev/null -w '%{http_code}' -m 5 -u "$2:$3" "https://$1/healthz" || true)
    if [ "$code" = 200 ]; then
      echo "    https://$1 is working."
      return 0
    fi
    sleep 5
  done
  echo "    Not reachable yet. If the droplet has a DigitalOcean cloud firewall, allow"
  echo "    inbound ports 80 and 443. Caddy keeps retrying on its own."
}

main "$@"; exit $?
