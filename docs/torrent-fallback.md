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
  1 would force it and shrink public swarms), DHT/PEX on, queue 10 downloads / 15 uploads /
  25 active with slow torrents not counted (5/10/15 until 2026-10-27), POSIX disk IO (the default memory-mapped IO
  misbehaves on NFS). Since 2026-10-26 (see *Safety*): bound to `tun0`, anonymous mode on,
  LSD off, executable file types skipped.
- Dead torrents (2026-10-27): the 3-seeder minimum is a tracker scrape and often stale, so many
  public grabs never get a single peer. 62 of 80 unfinished torrents were dead (some a week old)
  and held every download slot. The `stalled-cleaner` service (`stacks/media/stalled-cleaner/`)
  now removes, every 6 hours, any torrent in the Radarr/Sonarr queue older than 48 h with zero
  bytes downloaded: deleted from qBittorrent, that release blocklisted, and the app searches again
  (Usenet or a torrent with seeders). Finished torrents also stop seeding after 12 h without an
  upload (most public torrents here upload nothing, and queued seeders never ran the 3-day
  limit), so Radarr/Sonarr can remove them. Check after a deploy that the apps do remove them.
- VPN: all qBittorrent traffic leaves through PIA (CA Vancouver, port-forwarding servers);
  gluetun's firewall is the kill switch. Checked: qBittorrent's public IP differs from the VM's.
  qBittorrent's healthcheck reaches the internet through the tunnel, so if gluetun restarts
  and leaves it in a dead network namespace, autoheal restarts it (compose comment).
  Since 2026-10-26 Prowlarr's torrent indexers and FlareSolverr go through PIA as well (see
  *Safety*); the Usenet indexers and SABnzbd stay on the home IP on purpose.
- WebUI login for qBittorrent is on the Mac at `~/.config/homelab/qbittorrent-webui` (reused;
  never in the repo).

## Indexers (Prowlarr, public)

| Indexer        | Status                                   |
|----------------|------------------------------------------|
| The Pirate Bay | on, Sonarr + Radarr                      |
| Knaben         | on, Sonarr + Radarr                      |
| TorrentDownload| on, Sonarr + Radarr, priority 50 since 2026-10-26 (it served the fake Resident Evil) |
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
  Caveat (found 2026-10-26): Radarr applies minimum availability to RSS and automatic searches
  only. A search someone starts (the Search button, an API `MoviesSearch`, a Jellyseerr request)
  skips it, so a manual search on an in-cinemas film takes the best-scoring fake. That is how a
  cam renamed "1080p WEB-DL" came in for Resident Evil (2026). Don't search a movie before its
  digital release; **Homelab: Fake or Watermarked** (see docs/wiki/services/media.md) is the
  backstop for known fake groups.

## Safety: malware, privacy, fakes (2026-10-26)

Audit of the live media VM on 2026-10-26 (`torrent-audit.sh`, read-only): qBittorrent's exit IP
was PIA Vancouver while the VM's was Starlink; gluetun's firewall had OUTPUT policy DROP with
only `tun0` and the PIA endpoint allowed, IPv6 blocked, DNS over TLS to Cloudflare with
`BLOCK_MALICIOUS=on`; port 8085 unreachable from the LAN (role lanfw); zero executable-type files
in the torrent, Usenet and Media folders (Media/Applications aside). The gaps and what closed them:

| Gap | Fix | Where |
|-----|-----|-------|
| Nothing stopped an executable disguised as a video from landing on the NAS (Sonarr/Radarr refuse to import it, but the file was written first) | qBittorrent *Excluded file names* `*.exe *.scr *.bat *.cmd *.com *.pif *.lnk *.msi *.msp *.vbs *.vbe *.js *.jse *.wsf *.wsh *.ps1 *.psm1 *.hta *.jar *.apk *.reg *.zipx *.arj`; SABnzbd *Unwanted extensions* with the same list, action **Abort** | `playbooks/torrent-hardening.yml` |
| Tracker searches and .torrent fetches (Prowlarr, FlareSolverr) left from the home IP | gluetun HTTP proxy on (`HTTPPROXY=on`, `gluetun:8888`, compose network only, stealth); Prowlarr indexer proxy **PIA (gluetun)** (type HTTP) on tag `vpn`, every torrent indexer tagged; FlareSolverr in gluetun's namespace, Prowlarr's FlareSolverr proxy at `http://gluetun:8191` | compose + playbook |
| qBittorrent trusted gluetun's firewall alone | Bound to interface `tun0` (a second belt: with the tunnel down it has no interface to use), anonymous mode on (generic peer id, no client fingerprint), LSD off (nothing to discover inside a container) | playbook |
| TorrentDownload at the same priority as the better public sites | Priority 50 (Usenet 25, other torrent sites 40) | playbook |

Kept as they were, on purpose: encryption *preferred*, not *required* (public swarms are mostly
unencrypted; the tunnel already hides the traffic from the ISP); DHT and PEX on (public trackers
need them); archives are not excluded (books arrive zipped at times, and Sonarr/Radarr handle
rars); no ClamAV (it adds little for video files, and the executable blocks above do more).
Caveat: qBittorrent's exclusion list applies to every torrent, so a software torrent added by hand
would lose its installers. Usenet indexers deliberately stay off the proxy so a PIA outage never
stops Usenet; if the tunnel is down the proxy fails closed (no leak), torrent searches error in
Prowlarr until it is back, and the sysadmin digest reports the indexer errors.

`ansible-playbook playbooks/torrent-hardening.yml --check` shows what would change; without
`--check` it applies and then tests every torrent indexer through the proxy.

## Undo

Dead-torrent cleanup (2026-10-27): remove the `stalled-cleaner` service from compose (or set
`DRY_RUN=true`); qBittorrent queue back to 5/10/15 and *inactive seeding time limit* off. The
blocklisted releases are in each app's Activity > Blocklist.

Safety settings (2026-10-26): qBittorrent *Excluded file names* off, interface *Any*, anonymous
mode off, LSD on; SABnzbd unwanted extensions empty, action Pause; Prowlarr: delete the **PIA
(gluetun)** indexer proxy and the `vpn` tag, FlareSolverr proxy back to `http://flaresolverr:8191`
after removing `network_mode: service:gluetun` from the flaresolverr service, TorrentDownload
priority 40; compose: drop `HTTPPROXY`.


Every change with its previous value is in `~/HomeLab/changes-2026-10-22.md` on the Mac.
Quick version: delete the qBittorrent download client in Sonarr/Radarr, delete the torrent
indexers in Prowlarr, set the delay profiles' torrent delay back to 0, drop the `qualities`
blocks from recyclarr.yml and run `docker exec recyclarr recyclarr sync`.
