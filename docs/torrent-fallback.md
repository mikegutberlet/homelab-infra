# Torrents as a fallback to Usenet, and the quality floor

Set up 2026-10-22, mostly through the apps' own settings (Sonarr, Radarr, Prowlarr, qBittorrent
APIs); the compose/Recyclarr side is in this repo and this page records the rest. Before this,
qBittorrent ran behind gluetun but no app pointed at it and Prowlarr had only Usenet indexers.

Goal (Goobs): Usenet stays primary. Torrents are only for movies and shows Usenet is unlikely
to have. Public trackers only. Never a cam, telesync or screener; nothing below 480p.

## How downloads behave

- Sonarr/Radarr grab Usenet releases immediately (delay profile: preferred protocol usenet,
  usenet delay 0).
- A torrent is grabbed only when nothing on Usenet has turned up for **24 hours** (torrent
  delay 1440 min). "Bypass if highest quality" and "bypass above custom format score" are off,
  so a top-quality torrent still waits. (Interactive searches ignore delay profiles, by design.)
  Raise the delay before disabling indexers if torrents grab too eagerly.
- Prowlarr indexer priority: Usenet 25, torrent 40 (lower wins on equal releases). The app sync
  profile requires at least 3 seeders, so dead public torrents are not grabbed.
- Download clients: SABnzbd priority 1, qBittorrent priority 2 (host `gluetun`, port 8085,
  categories `tv-sonarr` / `radarr`). Both remove completed and failed downloads.
- Seeding: ratio 1.0 or 3 days (4320 min), whichever first. Set twice on purpose: in Prowlarr's
  per-indexer seed criteria (synced to the apps, which then remove the torrent) and as
  qBittorrent's global limits (pause) in case the apps are down.
- Paths: downloads under `/mnt/nfs/sabnzb/torrents/{tv,movies,incomplete}` (auto torrent
  management on, Original content layout). Same NFS export as `/mnt/nfs/Media/*` (checked:
  same device number), so imports are hardlinks and seeding never doubles storage.
  qBittorrent's previous default `/downloads` was not a mounted path.
- qBittorrent: port fixed to gluetun's forwarded PIA port (58253 at setup; gluetun re-pushes
  it on reconnect), re-announce on address change, UPnP off, encryption *preferred* (value 0;
  1 would force it and shrink public swarms), anonymous mode off, DHT/PEX/LSD on, queue
  5 downloads / 10 uploads / 15 active with slow torrents not counted, POSIX disk IO (the
  default memory-mapped IO misbehaves on NFS).
- VPN: all qBittorrent traffic leaves through PIA (CA Vancouver, port-forwarding servers);
  gluetun's firewall is the kill switch. Checked: qBittorrent's public IP differs from the VM's.
  qBittorrent's healthcheck reaches the internet through the tunnel, so if gluetun restarts
  and leaves it in a dead network namespace, autoheal restarts it (compose comment).
  Prowlarr itself (indexer pages, .torrent fetches) still goes out from the home IP; routing it
  through gluetun's HTTP proxy is a possible later step.
- WebUI login for qBittorrent is on the Mac at `~/.config/homelab/qbittorrent-webui` (reused;
  never in the repo).

## Indexers (Prowlarr, public)

| Indexer        | Status                                   |
|----------------|------------------------------------------|
| The Pirate Bay | on, Sonarr + Radarr                      |
| Knaben         | on, Sonarr + Radarr                      |
| TorrentDownload| on, Sonarr + Radarr                      |
| LimeTorrents   | on, Sonarr only (Radarr rejects it: "no results in configured categories") |
| 1337x          | on, Sonarr + Radarr, through FlareSolverr (indexer proxy, tag `flaresolverr`) |
| EZTV           | on, Sonarr only (TV tracker), through FlareSolverr |
| BitSearch, TheRARBG | not in this Prowlarr version        |

## Quality floor (both apps)

- Quality lists are set by Recyclarr (`stacks/media/recyclarr/recyclarr.yml`, `qualities`):
  the guide's top tiers and cutoff stay (Radarr: Bluray-1080p; Sonarr: WEB 1080p), then 720p,
  then the SD tiers down to 480p (Bluray-576p/480p, WEB 480p, DVD, SDTV). Disabled: 4K,
  remuxes, BR-DISK, DVD-R, Raw-HD, and in Radarr DVDSCR, REGIONAL, TELECINE, TELESYNC, CAM,
  WORKPRINT, Unknown. A disabled quality is never grabbed, whatever the source.
- Backstop custom format **No CAM/TS/Screener** in Sonarr and Radarr (release-title regex for
  CAM/HDCAM/TS/HDTS/TELESYNC/TC/HDTC/TELECINE/SCR/SCREENER/DVDSCR/WORKPRINT/R5/PDVD/PreDVD),
  scored -10000 in both profiles; listed in Recyclarr's `reset_unmatched_scores.except` so
  the daily sync keeps the score.
- Jellyseerr's default profiles are the Recyclarr-managed ones ("Anything Not terrible" / "Anything
  Not Terrible ") since 2026-10-22, so requests follow the same rules; the "No CAM/TS/Screener"
  format is scored -10000 in every other profile too.
- Radarr minimum availability **Released** on every movie and as Jellyseerr's default: Radarr
  only searches once a movie has a digital/physical release, so the cam-era window is skipped;
  a good WEB-DL at digital release still comes in early.

## Undo

Every change with its previous value is in `~/HomeLab/changes-2026-10-22.md` on the Mac.
Quick version: delete the qBittorrent download client in Sonarr/Radarr, delete the torrent
indexers in Prowlarr, set the delay profiles' torrent delay back to 0, drop the `qualities`
blocks from recyclarr.yml and run `docker exec recyclarr recyclarr sync`.
