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

  ensure_packages
  ensure_node_patched
  update_code
  watchdog
}

# Node.js is not used by these apps, but if something on the droplet installed it,
# keep it on a release with the October 2026 fixes (CVE-2026-111497, Brotli).
# Patches within the same major version only; an end-of-life major is reported.
# Tries at most once a day.
ensure_node_patched() {
  local node ver major min real
  node=$(command -v node 2>/dev/null) || return 0
  ver=$("$node" -v 2>/dev/null | sed 's/^v//') || return 0
  major=${ver%%.*}
  case "$major" in
    22) min=22.23.4 ;;
    24) min=24.22.1 ;;
    26) min=26.12.1 ;;
    *)
      [ "$major" -gt 26 ] 2>/dev/null && return 0
      node_once_today "$ver" || return 0
      log "Node.js $ver ($node) is end of life and has no fix; move it to 24.x."
      return 0 ;;
  esac
  [ "$(printf '%s\n' "$min" "$ver" | sort -V | head -n1)" = "$min" ] && return 0
  node_once_today "$ver" || return 0

  log "Node.js $ver ($node) needs the security fix in $min; updating."
  real=$(readlink -f "$node")
  if dpkg -S "$real" >/dev/null 2>&1; then
    DEBIAN_FRONTEND=noninteractive apt-get update -qq >/dev/null 2>&1
    DEBIAN_FRONTEND=noninteractive apt-get install -y -qq --only-upgrade nodejs >/dev/null 2>&1
  elif [ "${real#/snap/}" != "$real" ]; then
    snap refresh node >/dev/null 2>&1
  elif [ "$real" = /usr/local/bin/node ]; then
    node_tarball "$min"
  else
    log "Node.js at $real was not installed by apt, snap or the nodejs.org tarball; update it by hand."
    return 0
  fi

  ver=$("$node" -v 2>/dev/null | sed 's/^v//')
  if [ "$(printf '%s\n' "$min" "$ver" | sort -V | head -n1)" = "$min" ]; then
    log "Node.js is now $ver."
    node_restart_users
  else
    log "Node.js is still $ver after updating; the package source may not have the fix yet."
  fi
}

node_once_today() {
  local mark="$STATE/.node-checked" today
  today="$(date +%F) $1"
  [ "$(cat "$mark" 2>/dev/null)" = "$today" ] && return 1
  echo "$today" > "$mark"
}

# Install the official nodejs.org build of version $1 over /usr/local, checksum verified.
node_tarball() {
  local v="v$1" arch tmp file
  case "$(uname -m)" in
    x86_64) arch=x64 ;;
    aarch64) arch=arm64 ;;
    *) log "Unknown CPU $(uname -m); update Node.js by hand."; return 1 ;;
  esac
  file="node-$v-linux-$arch.tar.xz"
  tmp=$(mktemp -d)
  if curl -fsSL -m 300 -o "$tmp/$file" "https://nodejs.org/dist/$v/$file" \
     && curl -fsSL -m 60 -o "$tmp/SHASUMS256.txt" "https://nodejs.org/dist/$v/SHASUMS256.txt" \
     && (cd "$tmp" && grep " $file\$" SHASUMS256.txt | sha256sum -c --status); then
    tar -xJf "$tmp/$file" -C /usr/local --strip-components=1 --no-same-owner
  else
    log "Could not download or verify $file."
  fi
  rm -rf "$tmp"
}

# Restart the services running node so they load the fixed runtime.
node_restart_users() {
  local pid unit units=""
  for pid in $(pgrep -x node); do
    unit=$(ps -o unit= -p "$pid" 2>/dev/null | tr -d ' ')
    case "$unit" in *.service) units="$units $unit" ;; esac
  done
  for unit in $(printf '%s\n' $units | sort -u); do
    log "Restarting $unit for the new Node.js."
    systemctl restart "$unit"
  done
}

# Python packages the app imports lazily; installed here so no one has to log in.
ensure_packages() {
  need xlrd python3-xlrd xlrd "old .xls files"
  need cryptography python3-cryptography cryptography "statements from Gmail"
}

# need <module> <apt package> <pip package> <what it is for>
need() {
  python3 -c "import $1" 2>/dev/null && return
  DEBIAN_FRONTEND=noninteractive apt-get install -y -qq "$2" >/dev/null 2>&1 \
    || pip3 install -q --break-system-packages "$3" >/dev/null 2>&1 \
    || log "Could not install $3 (needed for $4)."
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
