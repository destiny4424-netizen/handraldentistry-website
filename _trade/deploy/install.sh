#!/usr/bin/env bash
# One-time setup of Meridian Trade on the Ubuntu/Debian droplet. Safe to run again.
#
#   curl -fsSL https://raw.githubusercontent.com/destiny4424-netizen/handraldentistry-website/main/_trade/deploy/install.sh | sudo bash
#
# What it does:
#   - installs python3, git and curl (the app needs nothing else)
#   - checks out this repository to /opt/meridian-trade/repo
#   - runs the app as a systemd service that starts on boot and restarts on crash
#   - checks GitHub every 5 minutes and installs new versions of _trade/ by itself
#   - serves it at a secure https:// address with a password (see web.sh), next to the
#     other sites Caddy already serves (the scanner, Handral Books)
# Your trades, settings and order log are kept in /var/lib/meridian-trade.
set -euo pipefail

main() {
  [ "$(id -u)" = 0 ] || { echo "Please run with sudo."; exit 1; }

  REPO_URL=${TRADE_REPO_URL:-https://github.com/destiny4424-netizen/handraldentistry-website.git}
  BRANCH=${TRADE_BRANCH:-main}
  REPO=/opt/meridian-trade/repo
  DATA=/var/lib/meridian-trade

  step "Installing packages"
  export DEBIAN_FRONTEND=noninteractive
  apt-get update -qq
  apt-get install -y -qq git python3 curl ca-certificates >/dev/null

  step "Creating the app user and data folder"
  id trade >/dev/null 2>&1 || useradd --system --home-dir "$DATA" --shell /usr/sbin/nologin trade
  install -d -o trade -g trade -m 750 "$DATA"
  install -d -m 755 /etc/meridian-trade
  echo "$BRANCH" > /etc/meridian-trade/branch

  step "Getting the code from GitHub ($BRANCH)"
  if [ -d "$REPO/.git" ]; then
    git -C "$REPO" remote set-url origin "$REPO_URL"
    git -C "$REPO" fetch --quiet origin "$BRANCH"
    git -C "$REPO" checkout --quiet -B "$BRANCH" "origin/$BRANCH"
    git -C "$REPO" reset --quiet --hard "origin/$BRANCH"
  else
    install -d -m 755 /opt/meridian-trade
    git clone --quiet --branch "$BRANCH" "$REPO_URL" "$REPO"
  fi

  step "Choosing the web address and password"
  bash "$REPO/_trade/deploy/web.sh" --prepare

  step "Installing and starting the service"
  bash "$REPO/_trade/deploy/update.sh" --install

  step "Setting up the secure web address"
  local web_ok=1
  bash "$REPO/_trade/deploy/web.sh" || web_ok=0

  local ip
  ip=$(sed -n 's/^MT_PUBLIC_IP=//p' /etc/default/meridian-trade)
  cat <<EOF

Done. Meridian Trade now runs on this server around the clock:
  - starts on boot and restarts itself if it ever stops
  - installs new versions pushed to GitHub ($BRANCH, _trade/) within 5 minutes,
    and rolls back by itself if a new version fails to start

ONE STEP ON DHAN (needed for placing orders):
  web.dhan.co -> My Profile -> Access DhanHQ APIs -> set your static IP to:  $ip

Useful only if something looks wrong:
  systemctl status meridian-trade
  journalctl -u meridian-trade -u meridian-trade-update -n 50
EOF
  echo
  echo "Open this on your iPhone (Safari) or PC and sign in with the password:"
  echo "------------------------------------------------------------"
  cat /etc/meridian-trade/login.txt
  echo "------------------------------------------------------------"
  echo "To see the address and password again: sudo cat /etc/meridian-trade/login.txt"
  [ "$web_ok" = 1 ] || echo "(The https address is not answering yet; see the message above.)"
}

step() { echo "==> $*"; }

main "$@"; exit $?
