#!/usr/bin/env bash
# Meridian Trade updater and watchdog. Installed as /usr/local/sbin/meridian-trade-update
# and run every 5 minutes by meridian-trade-update.timer.
#
# Each run:
#   1. Fetches the GitHub branch. If _trade/ changed, checks the new Python files,
#      switches to the new code and restarts the server. If the new version does not
#      come up healthy, it rolls back automatically.
#   2. Checks the server answers on /healthz and restarts it if it does not.
#
#   meridian-trade-update            normal run (what the timer does)
#   meridian-trade-update --install  (re)install units and scripts, enable, start
set -uo pipefail

main() {
  REPO=/opt/meridian-trade/repo
  DEPLOY="$REPO/_trade/deploy"
  STATE=/var/lib/meridian-trade
  BRANCH=$(cat /etc/meridian-trade/branch 2>/dev/null || echo main)
  PORT=3050                         # clinic 3030, Handral Books 3020, scanner 3040
  ZPORT=3051                        # the Zerodha copy of the desk (install-zerodha.sh), if installed
  SITE=/etc/caddy/meridian-trade.caddy

  if [ "${1:-}" = "--install" ]; then
    ensure_packages
    sync_files
    fix_caddy
    systemctl enable meridian-trade.service meridian-trade-update.timer >/dev/null
    systemctl restart meridian-trade.service
    systemctl start meridian-trade-update.timer
    if healthy; then
      echo "Meridian Trade is running on 127.0.0.1:$PORT"
      return 0
    fi
    echo "Server did not answer on port $PORT. See: journalctl -u meridian-trade -n 50"
    return 1
  fi

  ensure_packages
  update_code
  fix_caddy
  watchdog
}

# Phone notifications need python3-cryptography (added 22 Nov 2026). The app picks it up without a restart.
ensure_packages() {
  python3 -c 'import cryptography' 2>/dev/null && return 0
  log "Installing python3-cryptography (phone notifications)"
  DEBIAN_FRONTEND=noninteractive apt-get install -y -qq python3-cryptography >/dev/null 2>&1 \
    || log "Could not install python3-cryptography yet; will try again in 5 minutes."
}

log() { echo "$*"; }

# /healthz answers exactly "ok" (the clinic server answers JSON), so another
# program on the port never counts as Meridian Trade being up.
healthy() {
  local i port=${1:-$PORT}
  for i in $(seq 1 20); do
    [ "$(curl -fsS -m 3 "http://127.0.0.1:$port/healthz" 2>/dev/null)" = "ok" ] && return 0
    sleep 1
  done
  return 1
}

# The Zerodha desk (handral-zerodha) runs the same code; it is looked after only if it was installed.
zerodha_on() { systemctl is-enabled --quiet handral-zerodha.service 2>/dev/null; }

restart_all() {
  systemctl restart meridian-trade.service
  zerodha_on && systemctl restart handral-zerodha.service
  return 0
}

healthy_all() {
  healthy "$PORT" || return 1
  if zerodha_on && ! healthy "$ZPORT"; then
    log "The Zerodha desk did not answer on port $ZPORT."
    return 1
  fi
  return 0
}

# The web address must point at this app's port.
fix_caddy() {
  [ -f "$SITE" ] || return 0
  grep -q "reverse_proxy 127.0.0.1:$PORT\$" "$SITE" && return 0
  cp "$SITE" "$SITE.bak"
  sed -i -E "s#reverse_proxy 127\.0\.0\.1:[0-9]+#reverse_proxy 127.0.0.1:$PORT#" "$SITE"
  if caddy validate --config /etc/caddy/Caddyfile --adapter caddyfile >/dev/null 2>&1; then
    systemctl reload caddy && log "Web address now points at Meridian Trade on port $PORT."
  else
    mv "$SITE.bak" "$SITE"
    log "Could not switch the web address to port $PORT (Caddy config invalid); left it as it was."
  fi
}

# Copy units and helper scripts from the checkout into place if they changed.
sync_files() {
  local changed=0 f
  for f in "$DEPLOY"/*.service "$DEPLOY"/*.timer; do
    if ! cmp -s "$f" "/etc/systemd/system/$(basename "$f")"; then
      install -m 644 "$f" /etc/systemd/system/
      changed=1
    fi
  done
  install -m 755 "$DEPLOY/update.sh" /usr/local/sbin/meridian-trade-update
  install -m 755 "$DEPLOY/web.sh" /usr/local/sbin/meridian-trade-web
  if [ "$changed" = 1 ]; then
    systemctl daemon-reload
  fi
}

fetch() {
  local i
  for i in 1 2 3; do
    git -C "$REPO" fetch --quiet origin "$BRANCH" && return 0
    sleep $((i * 5))
  done
  return 1
}

# Every Python file of the new version must at least parse.
new_code_ok() {
  local f
  for f in $(git -C "$REPO" ls-tree -r --name-only "$1" -- _trade | grep '\.py$'); do
    if ! git -C "$REPO" show "$1:$f" | python3 -c "import ast, sys; ast.parse(sys.stdin.read())" 2>/dev/null; then
      log "New $f (${1:0:7}) has a syntax error; not installing it."
      return 1
    fi
  done
  return 0
}

update_code() {
  if ! fetch; then
    log "Could not reach GitHub; keeping the current version."
    return
  fi
  local old new
  old=$(git -C "$REPO" rev-parse HEAD)
  new=$(git -C "$REPO" rev-parse "origin/$BRANCH")
  [ "$old" = "$new" ] && return

  if git -C "$REPO" diff --quiet "$old" "$new" -- _trade; then
    # Only other parts of the repository changed; keep the checkout in step, no restart.
    git -C "$REPO" reset --quiet --hard "$new"
    return
  fi
  if [ "$(cat "$STATE/.bad-version" 2>/dev/null)" = "$new" ]; then
    return  # Already tried this version and it failed; wait for a newer one.
  fi
  if ! new_code_ok "$new"; then
    echo "$new" > "$STATE/.bad-version"
    return
  fi

  log "Updating Meridian Trade ${old:0:7} -> ${new:0:7}"
  git -C "$REPO" reset --quiet --hard "$new"
  sync_files
  restart_all
  if healthy_all; then
    log "Update to ${new:0:7} is running."
    rm -f "$STATE/.bad-version"
    return
  fi

  log "Version ${new:0:7} did not start; rolling back to ${old:0:7}."
  echo "$new" > "$STATE/.bad-version"
  git -C "$REPO" reset --quiet --hard "$old"
  sync_files
  restart_all
  healthy_all && log "Rolled back; ${old:0:7} is running again."
}

watchdog() {
  if zerodha_on && ! { systemctl is-active --quiet handral-zerodha.service && healthy "$ZPORT"; }; then
    log "Zerodha desk not answering on port $ZPORT; restarting it."
    systemctl restart handral-zerodha.service
    healthy "$ZPORT" || log "Still not answering. See: journalctl -u handral-zerodha -n 50"
  fi
  if systemctl is-active --quiet meridian-trade.service && healthy; then
    return
  fi
  log "Server not answering on port $PORT; restarting it."
  systemctl restart meridian-trade.service
  healthy || log "Still not answering. See: journalctl -u meridian-trade -n 50"
}

main "$@"; exit $?
