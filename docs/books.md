# Audiobooks and ebooks: Listenarr and Chaptarr

Added 2026-10-22 on the media VM (192.168.1.51), as the `storage` user like every other app there.

## Why Chaptarr and not Readarr

Readarr was retired by the Servarr team (repository archived 2025-06-27) and its metadata service is
gone, so a fresh Readarr can neither search nor match a book. Chaptarr is a maintained fork of
Readarr with its own metadata pipeline (`api2.chaptarr.com`); it keeps the Readarr API shape, so
Prowlarr syncs to it through the **Readarr** application type and SABnzbd/qBittorrent work as they
do for Sonarr/Radarr. It can manage audiobooks too, but here it only does ebooks: Goobs asked for
Listenarr for audiobooks, and Listenarr matches against Audible, which fits Audiobookshelf's own
Audible-based matching.

Both are beta software. Image tags are pinned (`chaptarr/chaptarr:0.9.965`,
`therobbiedavis/listenarr:canary-1.3.11`, the Docker Hub mirror of `ghcr.io/listenarrs/listenarr`;
Listenarr publishes canary builds only, several a week). Bump the tag deliberately, after reading the
release notes.

## Where things live

| | Listenarr (audiobooks) | Chaptarr (ebooks) |
| --- | --- | --- |
| Port / address | 4545, https://listenarr.tail9af84.ts.net (Authentik) | 8789, https://chaptarr.tail9af84.ts.net (Authentik) |
| Config | `/opt/appdata/listenarr` (`/app/config`) | `/opt/appdata/chaptarr` (`/config`) |
| Root folder | `/mnt/nfs/Media/Audiobooks` | `/mnt/nfs/Media/Books` |
| Folder pattern | `{Author}/{Title}` (what Audiobookshelf expects; no series level, so existing folders are not reorganised) | Readarr default `{Author Name}/{Book Title}` |
| Same files seen by | Audiobookshelf (`/audiobooks`, read-write), Storyteller (`/audiobooks`, read-only) | Storyteller (`/books`, read-write) |
| Shares `/mnt/nfs` with | SABnzbd and qBittorrent, so completed downloads are hardlinked into the library (same NFS export), no remote path mappings |

Existing files are never moved: both apps are pointed at the folders as they are and import what they
can match. Unmatched folders stay where they are.

## Downloads

Usenet first, torrents as the fallback, like the rest of the stack (`docs/torrent-fallback.md`).

| | SABnzbd | qBittorrent (via PIA) |
| --- | --- | --- |
| Category, audiobooks | `audiobooks` → `/mnt/nfs/sabnzb/complete/audiobooks` | `audiobooks` → `/mnt/nfs/sabnzb/torrents/audiobooks` |
| Category, ebooks | `books` → `/mnt/nfs/sabnzb/complete/books` | `books` → `/mnt/nfs/sabnzb/torrents/books` |
| Client priority | 1 | 2 (Chaptarr); Listenarr has no client priority, see below |
| Completed handling | remove from client after import | remove after import; seeding keeps running under qBittorrent's global 1.0 / 3 days rule |

Chaptarr has Readarr's delay profile: Usenet preferred, torrents only after 1440 minutes, same as
Sonarr/Radarr. Listenarr has no delay profiles and no RSS worker; it ranks indexers by a per-indexer
priority (Usenet 1, torrents 50) and searches when a monitored book is added or a "search all" is
run, so for audiobooks the torrent fallback is "lower priority", not "24 hours later".

Allowed file types (Listenarr → Settings, `AllowedFileExtensions`): `.mp3 .flac .m4a .m4b .ogg .mp4`.
`.mp4` was added 2026-10-26: some Usenet audiobook releases are AAC in an `.mp4` container (one
even named "(m4b)"), and without it Listenarr logs `reported no source files and no content path`,
never imports, keeps the book missing and grabs the same release again on every automatic search
(about every 6 hours; The Martian and Match Game were each downloaded 15 times). SABnzbd's duplicate
check is off, so it does not stop this. If a book shows the same grab repeatedly, look for that
warning in `/opt/appdata/listenarr/logs` (the container's `docker logs` is empty).

Quality: Listenarr profile prefers M4B (AAC), then MP3; Chaptarr keeps Readarr's ebook profile
(EPUB preferred, MOBI/AZW3/PDF allowed). The cam/480p rules of the video stack do not apply.

## Indexers (Prowlarr)

- Chaptarr: Prowlarr → Settings → Apps → **Readarr** entry named "Chaptarr", full sync. Prowlarr
  sends the indexers that carry book categories (7000s) and audiobooks (3030).
- Listenarr: Prowlarr has no Listenarr app type (feature request open). Listenarr → Settings →
  Indexers → **Import from Prowlarr** (Prowlarr URL `http://192.168.1.51:9696`, its API key). The
  import keeps only indexers with categories 3000/3030 and is a one-time copy: after adding an indexer
  in Prowlarr, run the import again.
- The Usenet indexers (NZBgeek, NzbPlanet) carry both categories. Of the public torrent trackers only
  those with an ebook/audiobook category show up (TPB, 1337x via FlareSolverr, LimeTorrents).

## Monitoring and dashboard

Uptime Kuma: "Listenarr" (`GET /` 2xx/3xx) and "Chaptarr" (`GET /ping`), both in the daily digest,
added by re-running `docs/kuma_setup.py`. Homepage: both under **Downloads**. The containers have no
Docker healthcheck (neither image ships curl/wget), so autoheal does not watch them; Kuma does.

## Deploy and first run

```sh
ansible-playbook playbooks/site.yml --limit media --tags stacks   # the two containers
ansible-playbook playbooks/site.yml --limit edge  --tags stacks   # routes; then restart traefik + tsdproxy
ansible-playbook playbooks/site.yml --limit apps  --tags stacks   # Homepage; then restart homepage
```

First run (done over SSH/API from the Mac on 2026-10-22, see the change log):

1. Listenarr: login screen off (Authentik and lanfw in front), API key in
   `~/.config/homelab/listenarr-api` on the Mac; root folder, SABnzbd + qBittorrent clients
   (category `audiobooks`, removed after import), Prowlarr import (8 indexers, Usenet priority 1,
   torrents 50, GingaDADDY disabled to match Prowlarr), default "Audiobook" profile (M4B, AAC, MP3
   from 320 kbps down; FLAC/OPUS off), folder pattern `{Author}/{Title}`, Hardlink/Copy, failed
   download handling with auto-search, metadata processing on. Its built-in `/app` root folder is
   still listed (no longer the default); delete it in the UI.
2. Chaptarr: authentication External with no login for local addresses (same reason; set a forms
   login under Settings → General if wanted; the pre-change config is `config.xml.bak-20261022`),
   API key in `~/.config/homelab/chaptarr-api`; root folder with the built-in E-Book profile and
   "Ebook Default" metadata profile, SABnzbd (priority 1) and qBittorrent (priority 2) with category
   `books`, delay profile 1440 min for torrents, Prowlarr app "Chaptarr" (Readarr type, full sync,
   7000-7060) which brought 1337x, Knaben, NZBgeek and NzbPlanet. A first sync raced and duplicated
   two indexers; they were deleted and the sync re-run.
3. SABnzbd's `complete/books` and `complete/audiobooks` were created as root over NFS (mapped to
   `nobody`) and set to 777 rather than recreated; SABnzbd's write test passed.
4. Audiobookshelf rescans its library on its own schedule; a Listenarr → Audiobookshelf
   notification is on Listenarr's roadmap, not there yet.

## Not done / open

- Readarr-era databases: none existed in this lab, so nothing to migrate.
- Storyteller sees the same folders; it needs its own import step per book (its UI), unchanged.
- MyAnonamouse or other private trackers: not configured (Goobs has not asked for private trackers).
