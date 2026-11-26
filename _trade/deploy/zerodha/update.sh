#!/usr/bin/env bash
# Updater and watchdog of the stand-alone Zerodha desk. Installed as /usr/local/sbin/handral-zerodha-update and
# run every 5 minutes by handral-zerodha-update.timer. It has nothing to do with the Dhan desk's updater.
#
# Each run: fetch GitHub; if _trade/ changed, check the new Python files parse, switch, restart, and roll back
# if the new version doesn't answer. Then make sure the server answers on /healthz.
set -uo pipefail

REPO=/opt/handral-zerodha/repo
STATE=/var/lib/handral-zerodha
BRANCH=$(cat /etc/handral-zerodha/branch 2>/dev/null || echo main)
PORT=3051
UNIT=handral-zerodha.service

log() { echo "$*"; }

healthy() {
  local i
  for i in $(seq 1 20); do
    [ "$(curl -fsS -m 3 "http://127.0.0.1:$PORT/healthz" 2>/dev/null)" = "ok" ] && return 0
    sleep 1
  done
  return 1
}

sync_files() {
  local changed=0 f d="$REPO/_trade/deploy/zerodha"
  for f in "$d"/*.service "$d"/*.timer; do
    if ! cmp -s "$f" "/etc/systemd/system/$(basename "$f")"; then
      install -m 644 "$f" /etc/systemd/system/
      changed=1
    fi
  done
  install -m 755 "$d/update.sh" /usr/local/sbin/handral-zerodha-update
  [ "$changed" = 1 ] && systemctl daemon-reload
  return 0
}

new_code_ok() {
  local f
  for f in $(git -C "$REPO" ls-tree -r --name-only "$1" -- _trade | grep '\.py$'); do
    if ! git -C "$REPO" show "$1:$f" | python3 -c "import ast, sys; ast.parse(sys.stdin.read())" 2>/dev/null; then
      log "New $f (${1:0:7}) has a syntax error; not installing it."
      return 1
    fi
  done
}

update_code() {
  local i ok=0
  for i in 1 2 3; do git -C "$REPO" fetch --quiet origin "$BRANCH" && { ok=1; break; }; sleep $((i * 5)); done
  [ "$ok" = 1 ] || { log "Could not reach GitHub; keeping the current version."; return; }
  local old new
  old=$(git -C "$REPO" rev-parse HEAD)
  new=$(git -C "$REPO" rev-parse "origin/$BRANCH")
  [ "$old" = "$new" ] && return
  if git -C "$REPO" diff --quiet "$old" "$new" -- _trade; then
    git -C "$REPO" reset --quiet --hard "$new"
    return
  fi
  [ "$(cat "$STATE/.bad-version" 2>/dev/null)" = "$new" ] && return
  if ! new_code_ok "$new"; then echo "$new" > "$STATE/.bad-version"; return; fi
  log "Updating the Zerodha desk ${old:0:7} -> ${new:0:7}"
  git -C "$REPO" reset --quiet --hard "$new"
  sync_files
  systemctl restart "$UNIT"
  if healthy; then rm -f "$STATE/.bad-version"; log "Update to ${new:0:7} is running."; return; fi
  log "Version ${new:0:7} did not start; rolling back to ${old:0:7}."
  echo "$new" > "$STATE/.bad-version"
  git -C "$REPO" reset --quiet --hard "$old"
  sync_files
  systemctl restart "$UNIT"
  healthy && log "Rolled back; ${old:0:7} is running again."
}

watchdog() {
  systemctl is-active --quiet "$UNIT" && healthy && return
  log "Zerodha desk not answering on port $PORT; restarting it."
  systemctl restart "$UNIT"
  healthy || log "Still not answering. See: journalctl -u handral-zerodha -n 50"
}

if [ "${1:-}" = "--install" ]; then
  sync_files
  systemctl enable "$UNIT" handral-zerodha-update.timer >/dev/null
  systemctl restart "$UNIT"
  systemctl start handral-zerodha-update.timer
  healthy && exit 0
  log "The Zerodha desk did not answer on port $PORT. See: journalctl -u handral-zerodha -n 50"
  exit 1
fi
update_code
watchdog
