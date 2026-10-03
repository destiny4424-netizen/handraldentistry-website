#!/usr/bin/env bash
# Handral Books database backup. Installed as /usr/local/sbin/handral-books-backup.
# Runs daily from handral-books-backup.timer, and before every update.
# Keeps 45 daily copies and 20 pre-update copies in /var/lib/handral-books/backups.
#
#   handral-books-backup              daily backup
#   handral-books-backup pre-update   backup taken just before an update
set -euo pipefail

main() {
  local data=/var/lib/handral-books
  local src="$data/books.db" dir="$data/backups" tag="${1:-daily}" keep=45
  [ "$tag" = "pre-update" ] && keep=20
  [ -f "$src" ] || { echo "No database yet; nothing to back up."; return 0; }
  mkdir -p "$dir"
  local dest="$dir/books-$tag-$(date +%Y-%m-%d_%H%M%S).db"
  # SQLite's own backup API gives a consistent copy even while the app is writing.
  python3 - "$src" "$dest" <<'PY'
import sqlite3, sys
src = sqlite3.connect(sys.argv[1], timeout=60)
dst = sqlite3.connect(sys.argv[2])
src.backup(dst)
ok = dst.execute("PRAGMA integrity_check").fetchone()[0]
dst.close(); src.close()
if ok != "ok":
    sys.exit("Backup integrity check failed: " + ok)
PY
  chown books:books "$dest" 2>/dev/null || true
  chmod 640 "$dest"
  # Drop the oldest copies of this kind beyond the limit.
  ls -1t "$dir"/books-"$tag"-*.db 2>/dev/null | tail -n +$((keep + 1)) | xargs -r rm -f
  echo "Backed up to $dest"
}

main "$@"; exit $?
