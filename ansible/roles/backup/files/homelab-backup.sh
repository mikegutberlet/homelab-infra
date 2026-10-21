#!/bin/bash
# Nightly config backup for one app VM. Stops nothing, never touches media.
#   - Postgres / MariaDB containers: logical dumps (pg_dumpall / mariadb-dump)
#   - SQLite files under /opt/appdata: online copies with sqlite3 .backup
#   - /opt/appdata and /opt/stacks: tar, minus caches, transcodes, metadata, logs
#     and the raw database files already dumped above
# Writes to /mnt/backups/<host>/<date>/ and prunes only its own dated folders.
set -uo pipefail

KEEP_DAYS=${KEEP_DAYS:-14}
BASE=/mnt/backups/$(hostname)
DEST=$BASE/$(date +%F)
WORK=$(mktemp -d /var/tmp/homelab-backup.XXXXXX)
trap 'rm -rf "$WORK"' EXIT
rc=0
log() { echo "$(date '+%F %T') $*"; }

mountpoint -q /mnt/backups || { log "ERROR: /mnt/backups is not mounted"; exit 1; }
mkdir -p "$DEST/db"

# Postgres (any image named postgres/pgvector), dumped as the postgres superuser.
for c in $(docker ps --format '{{.Names}} {{.Image}}' | awk '$2 ~ /postgres|pgvector/ {print $1}'); do
  user=$(docker exec "$c" printenv POSTGRES_USER 2>/dev/null || true)
  if docker exec "$c" pg_dumpall -U "${user:-postgres}" | gzip > "$DEST/db/$c.pgdumpall.sql.gz"; then
    log "dumped postgres $c"
  else log "ERROR: pg_dumpall $c"; rc=1; fi
done

# MariaDB / MySQL, using the container's own root password env.
for c in $(docker ps --format '{{.Names}} {{.Image}}' | awk '$2 ~ /mariadb|mysql/ {print $1}'); do
  if docker exec "$c" sh -c 'mariadb-dump -uroot -p"$MARIADB_ROOT_PASSWORD" --all-databases --single-transaction' \
       | gzip > "$DEST/db/$c.mariadb.sql.gz"; then
    log "dumped mariadb $c"
  else log "ERROR: mariadb-dump $c"; rc=1; fi
done

# SQLite: consistent online copies, path preserved under db/sqlite/.
while IFS= read -r -d '' f; do
  head -c 16 "$f" 2>/dev/null | grep -q "SQLite format 3" || continue
  out="$DEST/db/sqlite${f}"
  mkdir -p "$(dirname "$out")"
  if sqlite3 "$f" ".backup '$out'" 2>/dev/null; then gzip -f "$out"
  else log "WARN: sqlite backup failed for $f"; rc=1; fi
done < <(find /opt/appdata -xdev -type f \( -name '*.db' -o -name '*.sqlite' -o -name '*.sqlite3' \) \
           -not -path '*/cache/*' -not -path '*/Cache/*' -not -name '*.db-20*' -size -4G -print0)

# Files: everything else that is configuration.
tar -C / --warning=no-file-changed --exclude-caches-all \
  --exclude='*.db' --exclude='*.db-wal' --exclude='*.db-shm' --exclude='*.db-20*' \
  --exclude='*.sqlite' --exclude='*.sqlite-wal' --exclude='*.sqlite-shm' --exclude='*.sqlite3' \
  --exclude='opt/appdata/postgres' --exclude='opt/appdata/*/db' --exclude='opt/appdata/romm/db' \
  --exclude='opt/appdata/scrutiny/influxdb' \
  --exclude='opt/appdata/transcode' --exclude='*/[Cc]ache' --exclude='*/[Ll]ogs' --exclude='*/log' \
  --exclude='opt/appdata/plex/Library/Application Support/Plex Media Server/Metadata' \
  --exclude='opt/appdata/plex/Library/Application Support/Plex Media Server/Media' \
  --exclude='opt/appdata/plex/Library/Application Support/Plex Media Server/Crash Reports' \
  --exclude='opt/appdata/emby/metadata' --exclude='opt/appdata/*/MediaCover' \
  --exclude='opt/appdata/nextcloud/html' --exclude='opt/appdata/immich' \
  -czf "$DEST/appdata.tar.gz" opt/appdata opt/stacks
tar_rc=$?
[ $tar_rc -le 1 ] || { log "ERROR: tar exited $tar_rc"; rc=1; }

( cd "$DEST" && find . -type f ! -name SHA256SUMS -exec sha256sum {} + > SHA256SUMS )
chmod -R go-rwx "$DEST"
log "backup $DEST: $(du -sh "$DEST" | cut -f1)"

# Retention: only this script's own YYYY-MM-DD folders, older than KEEP_DAYS.
find "$BASE" -mindepth 1 -maxdepth 1 -type d -regex '.*/[0-9]\{4\}-[0-9]\{2\}-[0-9]\{2\}' \
  -mtime +"$KEEP_DAYS" -print -exec rm -rf {} + | sed 's/^/pruned /'
exit $rc
