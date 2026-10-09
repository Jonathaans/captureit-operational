#!/usr/bin/env bash
# Consistent offline copy of the entire /data volume, including SQLite WAL and
# private uploads. Briefly stops ONLY this Compose service, then restarts the
# same container even if copying or verification fails. Run as root on the VPS.
set -euo pipefail
umask 077

cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.."
test "$(id -u)" -eq 0 || { echo 'Run this backup as root.' >&2; exit 1; }
command -v python3 >/dev/null
ops_project=${OPS_COMPOSE_PROJECT:-captureit-operational}
ops_backup_root=${OPS_BACKUP_ROOT:-/root/captureit-ops-backups}
ops_container=$(docker compose -p "$ops_project" ps --all --quiet captureit-ops)
test -n "$ops_container" || { echo 'Ops container not found.' >&2; exit 1; }
test "$(docker inspect --format '{{.State.Running}}' "$ops_container")" = true || {
    echo 'Ops must be running before this backup; inspect its state first.' >&2
    exit 1
}
ops_stamp=$(date -u +%Y%m%dT%H%M%SZ)
ops_backup="$ops_backup_root/data-$ops_stamp"
mkdir -p -- "$ops_backup_root"
mkdir -m 700 -- "$ops_backup"
cp -- .env docker-compose.yml Dockerfile "$ops_backup/"
git rev-parse HEAD > "$ops_backup/source-commit.txt"
ops_image=$(docker inspect --format '{{.Image}}' "$ops_container")
printf '%s\n' "$ops_image" > "$ops_backup/running-image.txt"
# Keep a reference to the working image when the usual image tag is rebuilt.
docker image tag "$ops_image" "captureit-ops-rollback:$ops_stamp"

ops_restart=0
ops_resume() {
    if test "$ops_restart" = 1; then
        docker start "$ops_container" >/dev/null || {
            echo "Ops restart failed; run: docker start $ops_container" >&2
            return 1
        }
    fi
}
trap ops_resume EXIT
trap 'exit 130' INT
trap 'exit 143' TERM
ops_restart=1
docker stop --time 30 "$ops_container" >/dev/null
mkdir -- "$ops_backup/data"
docker cp --archive "$ops_container:/data/." "$ops_backup/data/"
# Test only the COPY. Opening it also recovers any committed WAL pages.
python3 - "$ops_backup/data/ops.sqlite3" <<'PY'
import sqlite3
import sys
from pathlib import Path
path = Path(sys.argv[1]).resolve()
if not path.is_file():
    raise SystemExit('Backup missing ops.sqlite3; live data was not modified.')
connection = sqlite3.connect(path.as_uri() + '?mode=rw', uri=True)
try:
    if connection.execute('PRAGMA integrity_check').fetchall() != [('ok',)]:
        raise SystemExit('Backup integrity check failed; keep the original volume.')
    connection.execute('PRAGMA wal_checkpoint(TRUNCATE)')
finally:
    connection.close()
PY
docker start "$ops_container" >/dev/null
ops_restart=0
printf 'Complete offline backup, verified %s\n' "$ops_stamp" > "$ops_backup/BACKUP_COMPLETE"
printf 'Backup OK: %s\nRollback image: captureit-ops-rollback:%s\n' "$ops_backup" "$ops_stamp"
