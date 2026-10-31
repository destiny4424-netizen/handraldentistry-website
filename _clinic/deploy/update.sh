#!/usr/bin/env bash
# Handral Clinic updater and watchdog. Installed as /usr/local/sbin/handral-clinic-update
# and run every 5 minutes by handral-clinic-update.timer.
#
# Each run fetches GitHub; if _clinic/ changed it checks the new server.py, backs up
# the database, restarts on the new code and rolls back if it does not come up.
# Then it checks the server answers on /healthz and restarts it if not.
#
#   handral-clinic-update            normal run (what the timer does)
#   handral-clinic-update --install  (re)install units and scripts, enable, start
set -uo pipefail

main() {
  REPO=/opt/handral-clinic/repo
  DEPLOY="$REPO/_clinic/deploy"
  STATE=/var/lib/handral-clinic
  BRANCH=$(cat /etc/handral-clinic/branch 2>/dev/null || echo main)
  PORT=$( (. /etc/default/handral-clinic 2>/dev/null; echo "${CLINIC_PORT:-3030}") )

  if [ "${1:-}" = "--install" ]; then
    sync_files
    systemctl enable handral-clinic.service handral-clinic-update.timer \
      handral-clinic-backup.timer >/dev/null
    systemctl restart handral-clinic.service
    systemctl start handral-clinic-update.timer handral-clinic-backup.timer
    if healthy; then
      echo "Handral Clinic server is running on 127.0.0.1:$PORT"
      return 0
    fi
    echo "Server did not answer on port $PORT. See: journalctl -u handral-clinic -n 50"
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

sync_files() {
  local changed=0 f
  for f in "$DEPLOY"/*.service "$DEPLOY"/*.timer; do
    if ! cmp -s "$f" "/etc/systemd/system/$(basename "$f")"; then
      install -m 644 "$f" /etc/systemd/system/
      changed=1
    fi
  done
  install -m 755 "$DEPLOY/update.sh" /usr/local/sbin/handral-clinic-update
  install -m 755 "$DEPLOY/backup.sh" /usr/local/sbin/handral-clinic-backup
  install -m 755 "$DEPLOY/web.sh" /usr/local/sbin/handral-clinic-web
  [ "$changed" = 1 ] && systemctl daemon-reload
  return 0
}

fetch() {
  local i
  for i in 1 2 3; do
    git -C "$REPO" fetch --quiet origin "$BRANCH" && return 0
    sleep $((i * 5))
  done
  return 1
}

update_code() {
  fetch || { log "Could not reach GitHub; keeping the current version."; return; }
  local old new
  old=$(git -C "$REPO" rev-parse HEAD)
  new=$(git -C "$REPO" rev-parse "origin/$BRANCH")
  [ "$old" = "$new" ] && return

  if git -C "$REPO" diff --quiet "$old" "$new" -- _clinic; then
    git -C "$REPO" reset --quiet --hard "$new"
    return
  fi
  [ "$(cat "$STATE/.bad-version" 2>/dev/null)" = "$new" ] && return

  if ! git -C "$REPO" show "$new:_clinic/server.py" | python3 -c \
      "import ast, sys; ast.parse(sys.stdin.read())" 2>/dev/null; then
    log "New server.py (${new:0:7}) has a syntax error; not installing it."
    echo "$new" > "$STATE/.bad-version"
    return
  fi

  log "Updating Handral Clinic ${old:0:7} -> ${new:0:7}"
  /usr/local/sbin/handral-clinic-backup pre-update || { log "Backup failed; not updating."; return; }
  git -C "$REPO" reset --quiet --hard "$new"
  sync_files
  systemctl restart handral-clinic.service
  if healthy; then
    log "Update to ${new:0:7} is running."
    rm -f "$STATE/.bad-version"
    return
  fi
  log "Version ${new:0:7} did not start; rolling back to ${old:0:7}."
  echo "$new" > "$STATE/.bad-version"
  git -C "$REPO" reset --quiet --hard "$old"
  sync_files
  systemctl restart handral-clinic.service
  healthy && log "Rolled back; ${old:0:7} is running again."
}

watchdog() {
  systemctl is-active --quiet handral-clinic.service && healthy && return
  log "Server not answering on port $PORT; restarting it."
  systemctl restart handral-clinic.service
  healthy || log "Still not answering. See: journalctl -u handral-clinic -n 50"
}

main "$@"; exit $?
