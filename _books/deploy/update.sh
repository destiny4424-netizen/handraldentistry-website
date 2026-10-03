#!/usr/bin/env bash
# Handral Books updater and watchdog. Installed as /usr/local/sbin/handral-books-update
# and run every 5 minutes by handral-books-update.timer.
#
# Each run:
#   1. Fetches the GitHub branch. If _books/ changed, checks the new books.py,
#      backs up the database, switches to the new code and restarts the server.
#      If the new version does not come up healthy, it rolls back automatically.
#   2. Checks the server answers on /healthz and restarts it if it does not.
#
#   handral-books-update            normal run (what the timer does)
#   handral-books-update --install  (re)install units and scripts, enable, start
set -uo pipefail

main() {
  REPO=/opt/handral-books/repo
  DEPLOY="$REPO/_books/deploy"
  STATE=/var/lib/handral-books
  BRANCH=$(cat /etc/handral-books/branch 2>/dev/null || echo main)
  PORT=$( (. /etc/default/handral-books 2>/dev/null; echo "${BOOKS_PORT:-3020}") )

  if [ "${1:-}" = "--install" ]; then
    sync_files
    systemctl enable handral-books.service handral-books-update.timer \
      handral-books-backup.timer >/dev/null
    systemctl restart handral-books.service
    systemctl start handral-books-update.timer handral-books-backup.timer
    if healthy; then
      echo "Handral Books is running on 127.0.0.1:$PORT"
      return 0
    fi
    echo "Server did not answer on port $PORT. See: journalctl -u handral-books -n 50"
    return 1
  fi

  update_code
  watchdog
}

log() { echo "$*"; }

healthy() {
  local i
  for i in $(seq 1 20); do
    if curl -fsS -m 3 "http://127.0.0.1:$PORT/healthz" >/dev/null 2>&1; then
      return 0
    fi
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
  install -m 755 "$DEPLOY/update.sh" /usr/local/sbin/handral-books-update
  install -m 755 "$DEPLOY/backup.sh" /usr/local/sbin/handral-books-backup
  install -m 755 "$DEPLOY/web.sh" /usr/local/sbin/handral-books-web
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

update_code() {
  if ! fetch; then
    log "Could not reach GitHub; keeping the current version."
    return
  fi
  local old new
  old=$(git -C "$REPO" rev-parse HEAD)
  new=$(git -C "$REPO" rev-parse "origin/$BRANCH")
  [ "$old" = "$new" ] && return

  if git -C "$REPO" diff --quiet "$old" "$new" -- _books; then
    # Only the website changed; keep the checkout in step, no restart needed.
    git -C "$REPO" reset --quiet --hard "$new"
    return
  fi

  if [ "$(cat "$STATE/.bad-version" 2>/dev/null)" = "$new" ]; then
    return  # Already tried this version and it failed; wait for a newer one.
  fi

  if ! git -C "$REPO" show "$new:_books/books.py" | python3 -c \
      "import ast, sys; ast.parse(sys.stdin.read())" 2>/dev/null; then
    log "New books.py (${new:0:7}) has a syntax error; not installing it."
    echo "$new" > "$STATE/.bad-version"
    return
  fi

  log "Updating Handral Books ${old:0:7} -> ${new:0:7}"
  /usr/local/sbin/handral-books-backup pre-update || {
    log "Backup failed; not updating."
    return
  }
  git -C "$REPO" reset --quiet --hard "$new"
  sync_files
  systemctl restart handral-books.service
  if healthy; then
    log "Update to ${new:0:7} is running."
    rm -f "$STATE/.bad-version"
    return
  fi

  log "Version ${new:0:7} did not start; rolling back to ${old:0:7}."
  echo "$new" > "$STATE/.bad-version"
  git -C "$REPO" reset --quiet --hard "$old"
  sync_files
  systemctl restart handral-books.service
  healthy && log "Rolled back; ${old:0:7} is running again."
}

watchdog() {
  if systemctl is-active --quiet handral-books.service && healthy; then
    return
  fi
  log "Server not answering on port $PORT; restarting it."
  systemctl restart handral-books.service
  healthy || log "Still not answering. See: journalctl -u handral-books -n 50"
}

main "$@"; exit $?
