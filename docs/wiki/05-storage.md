# Storage

## The pool

| | |
| --- | --- |
| System | TrueNAS 25.10.7 (upgraded from 23.10.2 on 2026-10-26) in VM 110 on RicksGarage |
| Pool | `DeepData`, RAIDZ1 over five 20 TB Seagate Exos drives presented as virtual disks from a USB DAS |
| Space | 54.2 TB used / 18.3 TB free on 2026-10-21 (about 75 to 80% full) |
| Snapshots | **None, by Goobs's choice** (2026-10-21): the pool is too full to hold them, and accidental loss is an accepted risk. The firm rule in exchange is that nothing is ever deleted on purpose |
| Scrub | Weekly; the one running during the build finished at 230 to 308 MB/s after the TrueNAS RAM fix |
| Users on TrueNAS | `storage` 3000 (owns most of the library), `plex` 3001, `ubuntu` 3002, `docker` 3003, `proxmox` 3004, `sonarr` 3006, `radarr` 3007; group `storage` is gid 3000 |
| Export | `/mnt/DeepData/NAS` over NFS to 192.168.1.0/24, mode 777, no user mapping (a second export `ProxMox` exists for the hosts). SMB is also on |

## Folder layout under `DeepData/NAS`

Everything the lab uses lives in one dataset, `NAS`. This is on purpose: Sonarr, Radarr and the
download clients see one filesystem, so an import is a hardlink or rename, instant and free.

| Folder | Used by | Owner | Notes |
| --- | --- | --- | --- |
| `Media/Movies` | Radarr (rw), Bazarr, Plex, Emby, ErsatzTV (ro), Tdarr | storage, mixed 1000/3000 on old files | 3,889 movie folders / 18.2 TB at the survey; includes 76 4K files and 1,194 empty folders |
| `Media/TVShows` | Sonarr (rw), Bazarr, Plex, Emby (ro) | storage | 541 series |
| `Media/Music`, `Media/Books`, `Media/Audiobooks` | Storyteller (Books rw, Audiobooks ro), Audiobookshelf (Audiobooks rw) | storage | |
| `Media/Podcasts` | Audiobookshelf | storage 3000:3000 | Created 2026-10-21 with Goobs's OK |
| `Media/Games` | RomM | storage | |
| `Media/meTube` | MeTube download dir | storage | |
| `Media/NextCloud` | Nextcloud data directory | www-data 33:33, 0770 | Was empty; re-permissioned once by hand with Goobs's OK on 2026-10-21. Only readable through Nextcloud |
| `Media/Immich` | nothing | | Empty leftover skeleton, left alone |
| `Media/XRated`, `Media/Other Videos`, Courses | Plex libraries only | | Never synced into ErsatzTV channels |
| `Photos` | Immich as a **read-only** external library | existing | Existing photo collection; Immich indexes but cannot change it |
| `Photos/Immich-uploads` | Immich uploads (phone backups) | storage 3000:3000 | Created 2026-10-21 |
| `Documents/Paperless/{consume,media,export}` | Paperless-ngx | storage 3000:3000 | Created 2026-10-21, add-only |
| `Documents/TackleBox` | Tackle Box archive (fishing app corpus, books, logs) | storage 3000:3000 | Created 2026-10-29, add-only |
| `sabnzb/complete`, `sabnzb/incomplete` | SABnzbd, qBittorrent, then the Arr apps import from here | storage | 9 leftover folders (about 28 GB) from the old failed-import backlog must not be deleted without Goobs's OK |
| `Backups/homelab/<vm>/<YYYY-MM-DD>/` | `homelab-backup` on every VM | root only (go-rwx) | 14-day retention, pruned only by the script that wrote them |
| `to-delete/2026-10-21/` | Tdarr project holding folder | storage | Junk files, weaker duplicate copies, Radarr's recycling bin (`radarr-recycle`), first-day Tdarr originals. Emptied only after Goobs confirms, about 2026-11-04 |
| `OldData` | nothing in the new stack | | Untouched |

## How the VMs mount it

`ansible/inventory/group_vars/docker_vms.yml` defines one share:

```
192.168.1.209:/mnt/DeepData/NAS  ->  /mnt/nfs
nfsvers=4.2,hard,noatime,_netdev,x-systemd.automount,x-systemd.mount-timeout=30
```

- `media`, `gpu` and `apps` mount it (`nfs_mounts: [nas]`); `edge` and `ai` do not.
- The path `/mnt/nfs` is the same one the old Servarr VM used, so Sonarr's and Radarr's root folders
  and SABnzbd's paths survived the migration unchanged.
- The backup role mounts only the `Backups/homelab` subfolder at `/mnt/backups` on every VM.
- `hard` mounts mean an app blocks rather than errors when the NAS is away; with `x-systemd.automount`
  the mount comes up on first use.
- **wait-nas** (role `nfs`, added after the power cut): a one-shot service that loops until every
  share is a real `nfs4` mount *and* has content, with no timeout, and a `docker.service` drop-in
  that requires it. Without this, a VM that boots before TrueNAS binds an empty autofs directory into
  its containers: Radarr and Sonarr would see every file as missing and start re-downloading, and
  Plex could empty its trash. The containers stay down until the NAS is back, on purpose.

## Ownership and the single app user

Every container runs as `storage` (uid/gid 3000), the user that already owns most of the library on
TrueNAS, so no bulk permission change was needed and files written by the new stack look like the old
ones. The old setup had SABnzbd as uid 1000 and the Arr apps as 3000, which silently broke every import
from August until 2026-10-21 ([D07](07-decisions.md#d07-one-app-user-uid-3000)).

Exceptions, each for a reason the image forces:

- n8n runs as its image's `node` user (1000) and keeps its data locally (`appdata_dir_owners`).
- AnythingLLM runs as uid 1000.
- Nextcloud runs as `www-data` (33) and its NAS folder is 33:33.
- Immich runs as 3000:3000 with the VM's `video` (44) and `render` (104) groups added so it can use
  `/dev/dri`.
- Plex and Emby (LinuxServer images) use `PUID/PGID` 3000 and mount media read-only.

Because the NFS export squashes nothing and is mode 777, uid matching matters for what files look
like, not for whether writes succeed. Tightening the export is a later step that needs Goobs's OK.

## Local disk on each VM

| Path | Content | Backed up |
| --- | --- | --- |
| `/opt/stacks/<vm>/` | The Compose project synced from the repo, plus the decrypted `.env` | yes, in the tar (the `.env` with plaintext secrets is included, which is why the backup folder is root-only) |
| `/opt/appdata/<app>/` | Each app's config and database | yes: SQLite via online copy, Postgres/MariaDB via dump, the rest via tar |
| `/opt/appdata/transcode/{plex,emby,tdarr,unmanic}` | Transcode scratch | no |
| `/opt/appdata/postgres`, `/opt/appdata/*/db`, `/opt/appdata/immich` | Raw database files (dumped separately) and Immich's large data | raw files no; Immich no (see open items) |
| Plex Metadata/Media, Emby metadata, `MediaCover`, caches, logs | Regenerable bloat | no |
| `/srv/usenet-incomplete` (media only) | SABnzbd's temp downloads, a 200 GB disk of its own (scsi1 on the host's `xfstest` SSD, label `usenet-tmp`, Ansible role `scratch`) | no (`backup=0` on the host too) |

Databases are never placed on NFS: SQLite and Postgres corrupt under NFS locking semantics, and the
old setup's local disks proved the apps' state is small enough to live on the VM.

VM disks: edge 32 GB, media 64 GB (+200 GB scratch), gpu 96 GB, apps 96 GB, ai 32 GB, all thin with `discard=on`.
SABnzbd's temp folder lived on the media system disk until 2026-10-22; with its 15 GB free-space
floor, any queue over about 22 GB paused downloads. Now: temp on the scratch disk, pause at 20 GB
free there and at 500 GB free on the NAS, Direct Unpack on (set up by a one-off playbook, removed 2026-10-27; it is in git history).
The gpu VM's 96 GB holds about 40 GB of Plex library data copied from the old server plus Tdarr's
5.8 GB image and transcode space.

## Rules for touching the NAS

These come from Goobs directly and from the design doc's "Protecting your existing data" section:

1. **Never delete, wipe, reformat or recreate** existing data or the pool. Automation does not delete
   on the NAS at all. Removals go to a dated holding folder with a manifest and an undo script.
2. **Add, don't change.** New folders go beside the existing ones. No existing folder is renamed,
   moved or re-permissioned without Goobs's explicit OK (the two exceptions so far, `Media/NextCloud`
   and the Paperless folder, had it).
3. **Match ownership.** New files are written as uid 3000; no recursive chown is ever run on the
   library.
4. **Point at data in place.** The new apps use the existing folder layout; nothing was migrated
   between datasets.
5. **One app owns the files at a time.** Old and new Sonarr/Radarr never ran together.
6. **No snapshots**, accepted risk. Irreplaceable data (Photos) still has no second copy anywhere;
   an off-site copy of `Photos` and `Backups/homelab` is a recommended future step.
