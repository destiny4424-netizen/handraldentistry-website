#!/usr/bin/env bash
# One-time setup of OrderBlock Scanner on an Ubuntu/Debian droplet. Safe to run again.
#
#   curl -fsSL https://raw.githubusercontent.com/destiny4424-netizen/handraldentistry-website/main/_scanner/deploy/install.sh | sudo bash
#
# What it does:
#   - installs python3, git and curl (the scanner needs nothing else)
#   - checks out this repository to /opt/orderblock-scanner/repo
#   - runs the scanner as a systemd service that starts on boot and restarts on crash
#   - checks GitHub every 5 minutes and installs new versions of _scanner/ by itself
#   - serves it at a secure https:// address with a password (see web.sh), next to
#     any other sites Caddy already serves (such as Handral Books)
# Dhan keys entered in the app are kept in /var/lib/orderblock-scanner.
set -euo pipefail

main() {
  [ "$(id -u)" = 0 ] || { echo "Please run with sudo."; exit 1; }

  REPO_URL=${SCANNER_REPO_URL:-https://github.com/destiny4424-netizen/handraldentistry-website.git}
  BRANCH=${SCANNER_BRANCH:-main}
  REPO=/opt/orderblock-scanner/repo
  DATA=/var/lib/orderblock-scanner

  step "Installing packages"
  export DEBIAN_FRONTEND=noninteractive
  apt-get update -qq
  apt-get install -y -qq git python3 curl ca-certificates >/dev/null

  step "Creating the scanner user and data folder"
  id scanner >/dev/null 2>&1 || useradd --system --home-dir "$DATA" --shell /usr/sbin/nologin scanner
  install -d -o scanner -g scanner -m 750 "$DATA"
  install -d -m 755 /etc/orderblock-scanner
  echo "$BRANCH" > /etc/orderblock-scanner/branch

  step "Getting the code from GitHub ($BRANCH)"
  if [ -d "$REPO/.git" ]; then
    git -C "$REPO" remote set-url origin "$REPO_URL"
    git -C "$REPO" fetch --quiet origin "$BRANCH"
    git -C "$REPO" checkout --quiet -B "$BRANCH" "origin/$BRANCH"
    git -C "$REPO" reset --quiet --hard "origin/$BRANCH"
  else
    install -d -m 755 /opt/orderblock-scanner
    git clone --quiet --branch "$BRANCH" "$REPO_URL" "$REPO"
  fi

  step "Choosing the web address and password"
  bash "$REPO/_scanner/deploy/web.sh" --prepare

  step "Installing and starting the service"
  bash "$REPO/_scanner/deploy/update.sh" --install

  step "Setting up the secure web address"
  local web_ok=1
  bash "$REPO/_scanner/deploy/web.sh" || web_ok=0

  cat <<EOF

Done. OrderBlock Scanner now runs on this server around the clock:
  - starts on boot and restarts itself if it ever stops
  - scans every 5 minutes during market hours (9:15-15:30 IST, Mon-Fri)
  - installs new versions pushed to GitHub ($BRANCH, _scanner/) within 5 minutes,
    and rolls back by itself if a new version fails to start

Useful only if something looks wrong:
  systemctl status orderblock-scanner
  journalctl -u orderblock-scanner -u orderblock-scanner-update -n 50
EOF
  echo
  echo "Open this on your iPhone (Safari) or PC and sign in with the password:"
  echo "------------------------------------------------------------"
  cat /etc/orderblock-scanner/login.txt
  echo "------------------------------------------------------------"
  echo "Then tap Connect Dhan in the app and paste your Dhan Client ID and access token."
  echo "To see the address and password again: sudo cat /etc/orderblock-scanner/login.txt"
  [ "$web_ok" = 1 ] || echo "(The https address is not answering yet; see the message above.)"
}

step() { echo "==> $*"; }

main "$@"; exit $?
