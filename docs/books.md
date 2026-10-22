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
`therobbiedavis/listenarr:canary-1.3.19`, the Docker Hub mirror of `ghcr.io/listenarrs/listenarr`;
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

1. Listenarr: admin login on, API key noted into `~/.config/homelab/listenarr-api` on the Mac; root
   folder, SABnzbd + qBittorrent clients (category `audiobooks`), Prowlarr import, quality profile,
   folder pattern, hardlink file action, failed-download handling on.
2. Chaptarr: Authentication "forms" (Goobs's login) with API key noted into
   `~/.config/homelab/chaptarr-api`; root folder, download clients (category `books`), delay
   profile 1440 min for torrents, Prowlarr app entry.
3. Both: Audiobookshelf rescans its library on its own schedule; a Listenarr notification can trigger
   a rescan once Listenarr adds an Audiobookshelf connection (on its roadmap, not there yet).

## Not done / open

- Readarr-era databases: none existed in this lab, so nothing to migrate.
- Storyteller sees the same folders; it needs its own import step per book (its UI), unchanged.
- MyAnonamouse or other private trackers: not configured (Goobs has not asked for private trackers).
