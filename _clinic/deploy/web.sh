#!/usr/bin/env bash
# Puts the Handral Clinic server on a secure https:// address using Caddy (automatic
# certificates). Run by install.sh; safe to run again. Installed as
# /usr/local/sbin/handral-clinic-web.
#
#   CLINIC_DOMAIN=clinicapi.example.com   address to use. Default:
#       clinicapi.handraldentistry.com if it already points at this droplet,
#       else clinic.<ip>.sslip.io, which works with no DNS change.
#
# Unlike Handral Books there is no browser password here: the clinic app has its
# own logins (doctor and staff), checked by the server on every request.
set -euo pipefail

main() {
  local conf=/etc/handral-clinic port domain ip
  port=$( (. /etc/default/handral-clinic 2>/dev/null; echo "${CLINIC_PORT:-3030}") )
  install -d -m 755 "$conf"
  ip=$(public_ip)
  [ -n "$ip" ] || { echo "Could not work out this droplet's public IP."; return 1; }

  domain=${CLINIC_DOMAIN:-$(cat "$conf/domain" 2>/dev/null || true)}
  if [ -z "$domain" ] || [ "$domain" = "clinic.${ip//./-}.sslip.io" ]; then
    if [ "$(getent ahostsv4 clinicapi.handraldentistry.com | awk 'NR==1{print $1}')" = "$ip" ]; then
      domain=clinicapi.handraldentistry.com
    else
      domain="clinic.${ip//./-}.sslip.io"
    fi
  fi
  echo "$domain" > "$conf/domain"

  install_caddy
  check_ports || return 1

  cat > /etc/caddy/handral-clinic.caddy <<CADDY
# Managed by handral-clinic-web; changes here are overwritten.
$domain {
	encode gzip
	request_body {
		max_size 26MB
	}
	header {
		Strict-Transport-Security "max-age=31536000"
		X-Content-Type-Options nosniff
		Referrer-Policy no-referrer
		-Server
	}
	reverse_proxy 127.0.0.1:$port
}
CADDY
  chmod 644 /etc/caddy/handral-clinic.caddy

  local main_cf=/etc/caddy/Caddyfile
  if [ ! -f "$main_cf" ] || grep -q 'The Caddyfile is an easy way to configure' "$main_cf"; then
    echo "import /etc/caddy/handral-clinic.caddy" > "$main_cf"
  elif ! grep -q 'import /etc/caddy/handral-clinic.caddy' "$main_cf"; then
    printf '\nimport /etc/caddy/handral-clinic.caddy\n' >> "$main_cf"
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

  echo "    Getting the security certificate for $domain (can take a minute)"
  local i
  for i in $(seq 1 24); do
    if curl -fsS -m 5 "https://$domain/healthz" >/dev/null 2>&1; then
      echo "    https://$domain is working."
      return 0
    fi
    sleep 5
  done
  echo "    Not reachable yet. If the droplet has a DigitalOcean cloud firewall, allow"
  echo "    inbound ports 80 and 443. Caddy keeps retrying on its own."
}

public_ip() {
  curl -fsS -m 3 http://169.254.169.254/metadata/v1/interfaces/public/0/ipv4/address 2>/dev/null \
    || curl -fsS -m 5 https://api.ipify.org 2>/dev/null \
    || hostname -I | awk '{print $1}'
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

check_ports() {
  local busy
  busy=$(ss -ltnpH '( sport = :80 or sport = :443 )' 2>/dev/null | grep -v '"caddy"' || true)
  if [ -n "$busy" ]; then
    echo "Another web server is using port 80 or 443:"
    echo "$busy"
    echo "The clinic server itself is running; send this output to get the web address set up."
    return 1
  fi
}

main "$@"; exit $?
