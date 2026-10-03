#!/usr/bin/env bash
# One-time setup of Handral Books on an Ubuntu/Debian droplet. Safe to run again.
#
#   curl -fsSL https://raw.githubusercontent.com/destiny4424-netizen/handraldentistry-website/main/_books/deploy/install.sh | sudo bash
#
# What it does:
#   - installs python3, git, curl and pdfplumber
#   - checks out this repository to /opt/handral-books/repo
#   - finds your existing books.db (or use BOOKS_OLD_DB=/path/to/books.db) and
#     copies it to /var/lib/handral-books/books.db; the original is left untouched
#   - stops any copy of books.py you started by hand (nohup, screen, tmux, old units)
#   - runs the app as a systemd service that starts on boot and restarts on crash
#   - checks GitHub every 5 minutes and installs new versions of _books/ by itself
#   - backs up the database every night
#   - serves it at a secure https:// address with a password (see web.sh)
set -euo pipefail

main() {
  [ "$(id -u)" = 0 ] || { echo "Please run with sudo."; exit 1; }

  REPO_URL=${BOOKS_REPO_URL:-https://github.com/destiny4424-netizen/handraldentistry-website.git}
  BRANCH=${BOOKS_BRANCH:-main}
  REPO=/opt/handral-books/repo
  DATA=/var/lib/handral-books
  OLD_DB=${BOOKS_OLD_DB:-${1:-}}

  step "Installing packages"
  export DEBIAN_FRONTEND=noninteractive
  apt-get update -qq
  apt-get install -y -qq git python3 curl ca-certificates >/dev/null
  apt-get install -y -qq python3-xlrd >/dev/null 2>&1 || true
  if ! python3 -c "import pdfplumber" 2>/dev/null; then
    apt-get install -y -qq python3-pdfplumber >/dev/null 2>&1 || {
      apt-get install -y -qq python3-pip >/dev/null
      pip3 install -q --break-system-packages pdfplumber 2>/dev/null || pip3 install -q pdfplumber
    }
  fi

  step "Creating the books user and data folder"
  id books >/dev/null 2>&1 || useradd --system --home-dir "$DATA" --shell /usr/sbin/nologin books
  install -d -o books -g books -m 750 "$DATA" "$DATA/backups"
  install -d -m 755 /etc/handral-books
  echo "$BRANCH" > /etc/handral-books/branch

  step "Getting the code from GitHub ($BRANCH)"
  if [ -d "$REPO/.git" ]; then
    git -C "$REPO" remote set-url origin "$REPO_URL"
    git -C "$REPO" fetch --quiet origin "$BRANCH"
    git -C "$REPO" checkout --quiet -B "$BRANCH" "origin/$BRANCH"
    git -C "$REPO" reset --quiet --hard "origin/$BRANCH"
  else
    install -d -m 755 /opt/handral-books
    git clone --quiet --branch "$BRANCH" "$REPO_URL" "$REPO"
  fi

  step "Stopping any copy started by hand"
  systemctl stop handral-books.service 2>/dev/null || true
  stop_old_copies

  if [ ! -f "$DATA/books.db" ]; then
    [ -n "$OLD_DB" ] || OLD_DB=$(find_old_db)
    if [ -n "$OLD_DB" ]; then
      step "Copying your existing data from $OLD_DB"
      copy_db "$OLD_DB" "$DATA/books.db"
      echo "    The original file is left where it was."
    else
      echo "    No existing books.db found; starting with an empty one."
    fi
  else
    echo "    Data already in $DATA/books.db; leaving it as is."
  fi
  chown -R books:books "$DATA"

  step "Installing and starting the service"
  bash "$REPO/_books/deploy/update.sh" --install

  step "Setting up the secure web address and password"
  local web_ok=1
  bash "$REPO/_books/deploy/web.sh" || web_ok=0

  cat <<EOF

Done. Handral Books now:
  - starts on boot and restarts itself if it ever stops
  - installs new versions pushed to GitHub ($BRANCH, _books/) within 5 minutes,
    and rolls back by itself if a new version fails to start
  - backs up the database every night to $DATA/backups

You do not need to run anything else. Useful only if something looks wrong:
  systemctl status handral-books        is it running?
  journalctl -u handral-books -n 50     recent server messages
  journalctl -u handral-books-update -n 50   recent update messages
EOF
  if [ "$web_ok" = 1 ]; then
    echo
    echo "Open this on your laptop and let the browser save the password:"
    echo "------------------------------------------------------------"
    cat /etc/handral-books/login.txt
    echo "------------------------------------------------------------"
    echo "To see it again later: sudo cat /etc/handral-books/login.txt"
  fi
}

step() { echo "==> $*"; }

stop_old_copies() {
  local f
  # Old systemd units that run books.py (other than ours).
  for f in /etc/systemd/system/*.service /lib/systemd/system/*.service; do
    [ -f "$f" ] || continue
    case "$(basename "$f")" in handral-books*) continue ;; esac
    if grep -q 'books\.py' "$f"; then
      echo "    Disabling old service $(basename "$f")"
      systemctl disable --now "$(basename "$f")" >/dev/null 2>&1 || true
    fi
  done
  # @reboot cron lines that start books.py would fight the service for the port.
  local u tab
  for u in $(ls /var/spool/cron/crontabs 2>/dev/null); do
    tab=$(crontab -l -u "$u" 2>/dev/null) || continue
    if echo "$tab" | grep -v '^#' | grep -q 'books\.py'; then
      echo "    Commenting out books.py line(s) in $u's crontab"
      echo "$tab" | sed '/^[^#].*books\.py/s/^/# disabled by handral-books installer: /' \
        | crontab -u "$u" -
    fi
  done
  # Processes started by hand (nohup, screen, tmux).
  local pids
  pids=$(pgrep -f 'python[0-9.]* .*books\.py' || true)
  if [ -n "$pids" ]; then
    echo "    Stopping running books.py (pid $(echo $pids))"
    kill $pids 2>/dev/null || true
    sleep 3
    pids=$(pgrep -f 'python[0-9.]* .*books\.py' || true)
    [ -z "$pids" ] || kill -9 $pids 2>/dev/null || true
  fi
}

# Most recently modified books.db on the machine, outside our own folders.
find_old_db() {
  find / -xdev \( -path /proc -o -path "$DATA" -o -path /opt/handral-books \) -prune \
    -o -type f -name books.db -size +0 -printf '%T@ %p\n' 2>/dev/null \
    | sort -rn | head -n 1 | cut -d' ' -f2- || true
}

copy_db() {
  python3 - "$1" "$2" <<'PY'
import sqlite3, sys
src = sqlite3.connect(sys.argv[1], timeout=60)
dst = sqlite3.connect(sys.argv[2])
src.backup(dst)
ok = dst.execute("PRAGMA integrity_check").fetchone()[0]
n = dst.execute("SELECT COUNT(*) FROM txns").fetchone()[0]
dst.close(); src.close()
if ok != "ok":
    sys.exit("Copied database failed its integrity check: " + ok)
print(f"    Copied {n} entries.")
PY
}

main "$@"; exit $?
