# Backups and restore

## What runs

`ansible/roles/backup` installs `/usr/local/sbin/homelab-backup` and a systemd timer on every VM:
nightly at **03:30 plus up to 45 minutes of random delay** (`Persistent=true`, so a missed run
happens at the next boot). It mounts only `NAS/Backups/homelab` at `/mnt/backups` and writes to
`/mnt/backups/<hostname>/<YYYY-MM-DD>/`:

| Content | Method | File |
| --- | --- | --- |
| Postgres (any `postgres`/`pgvector` image: apps VM shared DB, Authentik, Immich) | `pg_dumpall` as the container's superuser, gzipped | `db/<container>.pgdumpall.sql.gz` |
| MariaDB (RomM) | `mariadb-dump --all-databases --single-transaction` | `db/<container>.mariadb.sql.gz` |
| SQLite under `/opt/appdata` (Sonarr, Radarr, Prowlarr, Bazarr, Jellyseerr, Kuma, Tautulli, Plex, Emby, ...) | `sqlite3 .backup` online copy, gzipped, path preserved | `db/sqlite/opt/appdata/...` |
| Everything else in `/opt/appdata` and `/opt/stacks` | `tar -czf`, excluding caches, logs, transcodes, Plex Metadata/Media, Emby metadata, `MediaCover`, raw DB files, `nextcloud/html`, `immich`, `scrutiny/influxdb` | `appdata.tar.gz` |
| Integrity | every SQLite copy passes `PRAGMA quick_check` before it is gzipped; every `.gz` is read back with `gzip -t` from the NAS (since 2026-10-28); `SHA256SUMS` over the folder; `chmod -R go-rwx` | |

Retention: folders older than **14 days** matching `YYYY-MM-DD` under the host's own directory are
removed by the script. Nothing else on the NAS is ever touched by it. On any error it POSTs a line to
`http://192.168.1.53:5678/webhook/homelab/backup`, which lands in the next morning's digest.

First results (2026-10-21): media VM 443 MB; all five VMs tested.

## Not backed up (know this)

- Media, Photos, Nextcloud user files, Paperless documents: on the NAS by design, no second copy.
- Immich's local data (`/opt/appdata/immich`, excluded for size) apart from its database dump.
- The TrueNAS configuration itself (export it from the TrueNAS UI now and then, by hand).
- Anything off-site. The NAS is the single copy of the backups too.

## Check last night's backup

```sh
ssh goobs@192.168.1.51 'sudo systemctl status homelab-backup.timer; sudo journalctl -u homelab-backup -n 30'
ssh goobs@192.168.1.51 'sudo ls -la /mnt/backups/media/ | tail -5; sudo du -sh /mnt/backups/media/$(date +%F)'
```

Or just read the digest: a failure shows under "Backups".

## Restore one app's config

Example: Sonarr on the media VM, from yesterday.

```sh
ssh goobs@192.168.1.51
D=/mnt/backups/media/$(date -d yesterday +%F)
cd /opt/stacks/media && sudo docker compose stop sonarr
sudo mv /opt/appdata/sonarr /opt/appdata/sonarr.broken-$(date +%F)
sudo tar -C / -xzf $D/appdata.tar.gz opt/appdata/sonarr          # config files
sudo zcat "$D/db/sqlite/opt/appdata/sonarr/sonarr.db.gz" | sudo tee /opt/appdata/sonarr/sonarr.db >/dev/null
sudo chown -R 3000:3000 /opt/appdata/sonarr
sudo docker compose up -d sonarr
```

Apps with their own backup feature (Sonarr, Radarr, Prowlarr) can also be restored from their own
zip through the UI; that is how the migration was done.

## Restore a Postgres database (apps VM)

```sh
ssh goobs@192.168.1.53
D=/mnt/backups/apps/<date>
cd /opt/stacks/apps && sudo docker compose stop nextcloud nextcloud-web nextcloud-cron paperless n8n huginn
zcat $D/db/postgres.pgdumpall.sql.gz | sudo docker compose exec -T postgres psql -U postgres
sudo docker compose up -d
```

`pg_dumpall` output recreates roles and databases; drop the damaged database first if it exists.

## Rebuild a whole VM

1. `tofu apply` recreates the VM from the cloud image (destroy it first with
   `tofu destroy -target='proxmox_virtual_environment_vm.vm["media"]'`; the gpu VM also needs
   `qm set 203 --hostpci0 0000:00:02.0,pcie=1` again).
2. `ansible-playbook playbooks/site.yml --limit media --skip-tags stacks`.
3. Restore `/opt/appdata` from the newest `appdata.tar.gz` and the database dumps as above, fix
   ownership (3000:3000; n8n 1000; Nextcloud 33).
4. `ansible-playbook playbooks/site.yml --limit media --tags stacks`.
5. Re-run the Kuma script if monitors were lost; re-register Plex/Emby only if their appdata is gone.

The design's next steps (Proxmox Backup Server for whole-VM images, a monthly restore test, and an
off-site copy of `Backups/homelab` and `Photos`) are not implemented.
