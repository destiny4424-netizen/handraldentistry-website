#!/usr/bin/env bash
# OrderBlock Scanner updater and watchdog. Installed as /usr/local/sbin/orderblock-scanner-update
# and run every 5 minutes by orderblock-scanner-update.timer.
#
# Each run:
#   1. Fetches the GitHub branch. If _scanner/ changed, checks the new Python files,
#      switches to the new code and restarts the server. If the new version does not
#      come up healthy, it rolls back automatically.
#   2. Checks the server answers on /healthz and restarts it if it does not.
#
#   orderblock-scanner-update            normal run (what the timer does)
#   orderblock-scanner-update --install  (re)install units and scripts, enable, start
set -uo pipefail

main() {
  REPO=/opt/orderblock-scanner/repo
  DEPLOY="$REPO/_scanner/deploy"
  STATE=/var/lib/orderblock-scanner
  BRANCH=$(cat /etc/orderblock-scanner/branch 2>/dev/null || echo main)
  PORT=3030

  if [ "${1:-}" = "--install" ]; then
    sync_files
    systemctl enable orderblock-scanner.service orderblock-scanner-update.timer >/dev/null
    systemctl restart orderblock-scanner.service
    systemctl start orderblock-scanner-update.timer
    if healthy; then
      echo "OrderBlock Scanner is running on 127.0.0.1:$PORT"
      return 0
    fi
    echo "Server did not answer on port $PORT. See: journalctl -u orderblock-scanner -n 50"
    return 1
  fi

  update_code
  watchdog
}

log() { echo "$*"; }

healthy() {
  local i
  for i in $(seq 1 20); do
    curl -fsS -m 3 "http://127.0.0.1:$PORT/healthz" >/dev/null 2>&1 && return 0
    sleep 1
  done
  return 1
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
  install -m 755 "$DEPLOY/update.sh" /usr/local/sbin/orderblock-scanner-update
  install -m 755 "$DEPLOY/web.sh" /usr/local/sbin/orderblock-scanner-web
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
  for f in $(git -C "$REPO" ls-tree -r --name-only "$1" -- _scanner | grep '\.py$'); do
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

  if git -C "$REPO" diff --quiet "$old" "$new" -- _scanner; then
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

  log "Updating OrderBlock Scanner ${old:0:7} -> ${new:0:7}"
  git -C "$REPO" reset --quiet --hard "$new"
  sync_files
  systemctl restart orderblock-scanner.service
  if healthy; then
    log "Update to ${new:0:7} is running."
    rm -f "$STATE/.bad-version"
    return
  fi

  log "Version ${new:0:7} did not start; rolling back to ${old:0:7}."
  echo "$new" > "$STATE/.bad-version"
  git -C "$REPO" reset --quiet --hard "$old"
  sync_files
  systemctl restart orderblock-scanner.service
  healthy && log "Rolled back; ${old:0:7} is running again."
}

watchdog() {
  if systemctl is-active --quiet orderblock-scanner.service && healthy; then
    return
  fi
  log "Server not answering on port $PORT; restarting it."
  systemctl restart orderblock-scanner.service
  healthy || log "Still not answering. See: journalctl -u orderblock-scanner -n 50"
}

main "$@"; exit $?
