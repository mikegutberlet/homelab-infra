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
`therobbiedavis/listenarr:canary-1.5.12`, the Docker Hub mirror of `ghcr.io/listenarrs/listenarr`;
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
priority (Usenet 1, torrents 50) and searches when a monitored book is added, on "search all", and
on its own sweep of every monitored book without a file (every 6 hours up to 1.3.x; since 1.5.0 an
hourly sweep with a 24 h per-book cooldown, Settings → `AutomaticSearchIntervalHours` /
`AutomaticSearchCooldownHours`), so for audiobooks the torrent fallback is "lower priority", not
"24 hours later". No separate backlog search is needed for it. Since 1.5.1 a search that finds
nothing for "title author" is retried with the title alone (Audible titles carry suffixes and
release names rarely carry the author); that is why the tag was moved from 1.3.11 on 2026-11-04.

Allowed file types (Listenarr → Settings, `AllowedFileExtensions`): `.mp3 .flac .m4a .m4b .ogg .mp4`.
`.mp4` was added 2026-10-26: some Usenet audiobook releases are AAC in an `.mp4` container (one
even named "(m4b)"), and without it Listenarr logs `reported no source files and no content path`,
never imports, keeps the book missing and grabs the same release again on every automatic search
(about every 6 hours; The Martian and Match Game were each downloaded 15 times). SABnzbd's duplicate
check is off, so it does not stop this. If a book shows the same grab repeatedly, look for that
warning in `/opt/appdata/listenarr/logs` (the container's `docker logs` is empty).

Allowing `.mp4` is not enough on its own: Listenarr (canary 1.3.11) also has a built-in rule that skips
`.mp4` when it registers files ("Skipping non-audio audiobook file registration"), so a manual import
places the files but the book stays "no-file". The fix used for The Martian and Match Game: manual
import (hardlink/copy), rename the library files `.mp4` → `.m4b` (same MP4/AAC container, only the
name changes), then rescan the book. The download copies keep their old names until cleaned up.

Upgrades are off: the "Audiobook" profile's cutoff was lowered from M4B to **MP3 64kbps** (the lowest
allowed format) on 2026-10-26, so any accepted file is final. Before that, every MP3 book (e.g.
Trouble on Paradise) stayed "quality-mismatch" and was re-searched for an M4B every 6 hours. New
grabs still rank M4B first. Listenarr's profile has no separate "upgrades allowed" switch; to turn
upgrades back on, set the cutoff to M4B again.

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
- MyAnonamouse (private, audiobooks and ebooks; the source most audiobooks are only on). Added in
  Prowlarr, not in each app: Prowlarr's torrent indexers already leave through the gluetun proxy
  (tag `vpn`), so searches, `.torrent` fetches and qBittorrent's announces all show PIA's IP and one
  MAM session covers them. Chaptarr gets it from the app sync; Listenarr needs "Import from
  Prowlarr" run once more. See "MyAnonamouse" below.

## MyAnonamouse (2026-11-04)

Goobs chose to join MAM because the Usenet sites and public trackers find few audiobooks. MAM locks
every session (`mam_id` cookie) to one IP, and PIA hands out a new IP on each reconnect, so the
session must follow the VPN: `mam-seedbox` on media (`python:3.12-alpine`, `network_mode:
service:gluetun`, script `stacks/media/mam-seedbox/mam_seedbox.py`, standard library only) calls
`https://t.myanonamouse.net/json/dynamicSeedbox.php` once an hour with the session cookie; MAM moves
the session to the caller's IP ("Completed") or answers "No change". The cookie jar lives in
`/opt/appdata/mam-seedbox/cookies.txt` (MAM may rotate the id; the jar keeps the current one, the
`MAM_ID` secret only seeds it while the jar holds none). `ONCE=true` makes one pass and exits.

Seeding (2026-11-05): MAM counts a torrent as satisfied after 72 h of seeding, ratio aside, and the
stack's global qBittorrent rule (ratio 1.0 or 3 days, or 12 h without an upload, see
`docs/torrent-fallback.md`) would stop most MAM torrents earlier. So the same hourly pass gives every
torrent whose tracker is MAM its own share limits in qBittorrent (localhost inside gluetun's
namespace, no login): no ratio cap, no inactivity cap, `SEED_HOURS` (72) of seeding; qBittorrent
then pauses it and Chaptarr/Listenarr remove it as they do for any finished torrent (both wait for
the seed limit). Other torrents keep the global rule. `DRY_RUN=true` lists what it would set.

qBittorrent is pinned to 5.2.4 (`stacks/media/compose.yaml`) and off the sysadmin's auto-update list:
MAM only allows listed client versions and 5.3 was not on it (2026-11-05). Move the pin when MAM's
client list catches up. The 5.3 -> 5.2 step bit once: 5.3 had moved the WebUI keys into a `[WebUI]`
section 5.2 does not read, so 5.2.4 came up with a temporary password and locked every arr app out;
`playbooks/qbittorrent-webui-keys.yml` (`--check` first) copies them back under `[Preferences]`.

Setup, in this order (the session must be created for the VPN's IP, not the home IP):

1. Join: https://www.myanonamouse.net/inviteapp.php (interview over their IRC, a few minutes).
2. Read the VPN's current IP: `sudo docker exec media-gluetun-1 wget -qO- https://ipinfo.io/ip`.
3. MAM → Preferences → Security → create a session for that IP with "Allow session to set dynamic
   seedbox IP" ticked (IP-locked is fine, the updater moves it). Copy the `mam_id`.
4. `sops stacks/media/secrets.sops.env`, add `MAM_ID=<the cookie>`; deploy media
   (`playbooks/site.yml --limit media --tags stacks`); check
   `sudo docker logs media-mam-seedbox-1` says Completed or No change.
5. Prowlarr → Indexers → add **MyAnonamouse** (the same `mam_id`), tag `vpn`, sync to Chaptarr
   (Readarr app) on; seed criteria Seed Time 4320, Seed Ratio empty (belt and braces: the hourly
   pass above sets the same per torrent).
6. Listenarr → Settings → Indexers → Import from Prowlarr again (adds MAM with priority 50, like
   the other torrent indexers; raise it above Usenet only if MAM should win ties).

Listenarr 1.3.11+ also has a native MAM indexer type, but it would search from the home IP and
need a second session; not used.

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

## Want to Read sync (hardcover-sync)

Added 2026-11-02. Every book on Goobs's Hardcover **Want to Read** list goes to both apps: Chaptarr
for the ebook, Listenarr for the audiobook, each added monitored with a search so it is grabbed.
Goodreads was the list first, but its shelf RSS feeds answer 404 and its API closed in 2020, so the
Goodreads library was imported into Hardcover once and books are now marked there.

| | |
| --- | --- |
| Container | `hardcover-sync` on media, `python:3.12-alpine` as 3000, script `stacks/media/hardcover-sync/sync_hardcover.py` (standard library only) |
| Secrets | `HARDCOVER_TOKEN` (Hardcover → Settings → API, read-only scope), `CHAPTARR_API_KEY`, `LISTENARR_API_KEY` in `stacks/media/secrets.sops.env` |
| Schedule | One pass at start, then every 60 min (`INTERVAL_MIN`) |
| Chaptarr | `GET /api/v1/search?term=<title author>&provider=goodreads` (what its Add New page uses here; `provider=hardcover` needs a Hardcover token in Chaptarr's settings and otherwise answers 503), then `POST /api/v1/book` with `mediaType: ebook`, the `/mnt/nfs/Media/Books` root folder's ebook quality and metadata profiles, `searchForNewBook`, and the author on "only this book" (`monitor: specificBook`, new items none), so the author's other books stay unwanted. Only ebook rows count as "already there" |
| Listenarr | `GET /api/v1/search/audible`, `GET /api/v1/library/by-asin/<asin>` (already there?), then `POST /api/v1/library/add` with `monitored` and `autoSearch` |
| State | None: the apps are the state. A book already in an app is skipped, so the first pass takes the whole list and later passes only books added since. A book deleted from an app while still on Want to Read comes back; take it off the list in Hardcover |
| Matching | Title before any subtitle must match (case, punctuation and a leading "The/A/An" ignored) and one author's last name must appear. Anything else logs `no match` and is retried after 24 h (`RETRY_HOURS`); nothing is guessed. Add those by hand in the app |
| Pacing | Before each Chaptarr book the sync waits until Chaptarr's command queue is empty (`GET /api/v1/command`, at most `IDLE_WAIT_MIN`, 30 min) and gives the add 600 s: each add loads the author's whole catalogue, and back-to-back adds locked its SQLite database on the first live pass (2026-11-03) |
| Switches | `DRY_RUN=true` logs `would add` and adds nothing (the first three deploys ran this way; live since 2026-11-02); `LISTENARR_SKIP_ASINS` lists Audible editions never added (now the BBC Foundation Trilogy dramatization and the Theo Solomon 1984; add another reading by hand); `ONCE=true` runs one pass and exits |

Check it with `sudo docker logs media-hardcover-sync-1`. Not `book/lookup?mediaType=ebook`: in 0.9.965
Chaptarr still tags every remote lookup result as audiobook, so that filter returns nothing (issue #67;
the first dry run on 2026-11-02 matched 0 of 20). Titles match with "(…)" parts removed, so "Neuromancer
(Sprawl, #1)" or "(Unabridged)" still match; the app's title must be the whole wanted title or its part
before a colon, never longer (a bare "The Lord of the Rings" once matched "The Return of the King").
Listenarr skips Audible "Virtual Voice" (synthetic) narrations.

## Duplicate library rows (2026-11-03)

Chaptarr 0.9.965 adds one book row per edition and metadata source (`hc:`, `gr:`, `az:` ids) when
it refreshes an author, so the library showed one work many times ("1984" x42, 13,327 rows in all);
upstream bugs [#111](https://github.com/Chaptarr/chaptarr/issues/111) and
[#323](https://github.com/Chaptarr/chaptarr/issues/323), no fixed release yet. Only rows: one file
per book on disk, one grab per book in history. `playbooks/chaptarr-dedupe.yml` (`--check` first)
keeps every row with files, else one row per author and title, and deletes the rest from the
database only. Run it again if a later author refresh brings them back. The sync also matches
"already there" by title now, after Snow Crash and Starship Troopers were re-added every hour
because Chaptarr stored them under another work id than its search returns.

## Not done / open

- 2026-11-05 leftovers: `playbooks/audiobook-fixups.yml` (`--check` first) adds the four Hardcover titles the sync
  never matched (Hitchhiker's Guide, 20,000 Leagues, Martian Chronicles, Moon Is a Harsh Mistress) through
  Listenarr's Audible search and imports Bad Blood by hand from `sabnzb/complete/audiobooks/John Carreyrou - Bad Blood*.1`
  (hardlink/copy, source folder kept). Run once; a second run reports everything as present.
- Readarr-era databases: none existed in this lab, so nothing to migrate.
- Storyteller sees the same folders; it needs its own import step per book (its UI), unchanged.
- Other private trackers: none.
