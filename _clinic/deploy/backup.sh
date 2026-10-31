#!/usr/bin/env bash
# Handral Clinic database backup. Installed as /usr/local/sbin/handral-clinic-backup.
# Runs 4 times a day from handral-clinic-backup.timer, and before every update.
# Keeps 60 regular copies (about 15 days) and 20 pre-update copies.
#
#   handral-clinic-backup              regular backup
#   handral-clinic-backup pre-update   backup taken just before an update
set -euo pipefail

main() {
  local data=/var/lib/handral-clinic
  local src="$data/clinic.db" dir="$data/backups" tag="${1:-daily}" keep=60
  [ "$tag" = "pre-update" ] && keep=20
  [ -f "$src" ] || { echo "No database yet; nothing to back up."; return 0; }
  mkdir -p "$dir"
  local dest="$dir/clinic-$tag-$(date +%Y-%m-%d_%H%M%S).db"
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
  chown clinic:clinic "$dest" 2>/dev/null || true
  chmod 640 "$dest"
  ls -1t "$dir"/clinic-"$tag"-*.db 2>/dev/null | tail -n +$((keep + 1)) | xargs -r rm -f
  echo "Backed up to $dest"
}

main "$@"; exit $?
