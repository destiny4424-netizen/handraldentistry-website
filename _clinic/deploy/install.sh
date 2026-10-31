#!/usr/bin/env bash
# One-time setup of the Handral Clinic cloud server on the droplet. Safe to run again.
#
#   curl -fsSL https://raw.githubusercontent.com/destiny4424-netizen/handraldentistry-website/main/_clinic/deploy/install.sh | sudo bash
#
# What it does:
#   - installs python3, git and curl (the server needs nothing else)
#   - checks out this repository to /opt/handral-clinic/repo
#   - creates the database in /var/lib/handral-clinic and the doctor's login
#   - runs the server as a service that starts on boot and restarts on crash
#   - checks GitHub every 5 minutes and installs new versions of _clinic/ by itself
#   - backs up the database 4 times a day
#   - serves it at a secure https:// address (see web.sh)
# Handral Books, if installed, is left exactly as it is.
set -euo pipefail

main() {
  [ "$(id -u)" = 0 ] || { echo "Please run with sudo."; exit 1; }
  REPO_URL=${CLINIC_REPO_URL:-https://github.com/destiny4424-netizen/handraldentistry-website.git}
  BRANCH=${CLINIC_BRANCH:-main}
  REPO=/opt/handral-clinic/repo
  DATA=/var/lib/handral-clinic
  LOGIN=/etc/handral-clinic/login.txt

  step "Installing packages"
  export DEBIAN_FRONTEND=noninteractive
  apt-get update -qq
  apt-get install -y -qq git python3 curl ca-certificates >/dev/null

  step "Creating the clinic user and data folder"
  id clinic >/dev/null 2>&1 || useradd --system --home-dir "$DATA" --shell /usr/sbin/nologin clinic
  install -d -o clinic -g clinic -m 750 "$DATA" "$DATA/backups"
  install -d -m 755 /etc/handral-clinic
  echo "$BRANCH" > /etc/handral-clinic/branch

  step "Getting the code from GitHub ($BRANCH)"
  if [ -d "$REPO/.git" ]; then
    git -C "$REPO" remote set-url origin "$REPO_URL"
    git -C "$REPO" fetch --quiet origin "$BRANCH"
    git -C "$REPO" checkout --quiet -B "$BRANCH" "origin/$BRANCH"
    git -C "$REPO" reset --quiet --hard "origin/$BRANCH"
  else
    install -d -m 755 /opt/handral-clinic
    git clone --quiet --branch "$BRANCH" "$REPO_URL" "$REPO"
  fi

  if [ ! -f "$LOGIN" ] || [ -n "${CLINIC_NEW_PASSWORD:-}" ]; then
    step "Creating the doctor's login"
    local pw
    pw=$(LC_ALL=C tr -dc 'abcdefghjkmnpqrstuvwxyz23456789' < /dev/urandom | head -c 12)
    pw="${pw:0:4}-${pw:4:4}-${pw:8:4}"
    echo "$pw" | runuser -u clinic -- env CLINIC_DB="$DATA/clinic.db" \
      python3 "$REPO/_clinic/server.py" set-password doctor >/dev/null
    umask 077
    printf 'Username: doctor\nPassword: %s\n' "$pw" > "$LOGIN"
    chmod 600 "$LOGIN"
  fi
  chown -R clinic:clinic "$DATA"

  step "Installing and starting the service"
  bash "$REPO/_clinic/deploy/update.sh" --install

  step "Setting up the secure web address"
  local web_ok=1
  bash "$REPO/_clinic/deploy/web.sh" || web_ok=0
  local domain
  domain=$(cat /etc/handral-clinic/domain 2>/dev/null || echo "?")
  sed -i '/^Server:/d' "$LOGIN"
  echo "Server: https://$domain" >> "$LOGIN"

  cat <<DONE

Done. The clinic cloud server now starts on boot, restarts itself if it stops,
installs new versions from GitHub within 5 minutes and backs up 4 times a day
to $DATA/backups.

Open https://handraldentistry.com/clinic/ and sign in with:
------------------------------------------------------------
$(cat "$LOGIN")
------------------------------------------------------------
DONE
  if [ "$domain" != clinicapi.handraldentistry.com ]; then
    cat <<NOTE
The app looks for the server at https://clinicapi.handraldentistry.com.
Either add a DNS record (type A, name clinicapi, value $(curl -fsS -m 5 https://api.ipify.org 2>/dev/null || echo 'this droplet IP'))
and run:  sudo handral-clinic-web
or, on the sign-in screen, tap "Server address" and type https://$domain
NOTE
  fi
  [ "$web_ok" = 1 ] || echo "(The web address step reported a problem; see above.)"
  echo "To see the login again: sudo cat $LOGIN"
}

step() { echo "==> $*"; }

main "$@"; exit $?
