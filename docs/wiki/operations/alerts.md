# Alerts and monitoring

## What you receive

| Email | From | Means | Do |
| --- | --- | --- | --- |
| "[Uptime Kuma] <monitor> DOWN/UP" immediately | the lab's alert Gmail account via Kuma | One of the 8 critical media monitors failed a check (Plex, Emby, Sonarr, Radarr, Prowlarr, SABnzbd, qBittorrent via PIA, Requests) | Open `status.tail9af84.ts.net`; if the whole VM is down, check Proxmox; autoheal usually fixes single containers within a minute |
| "[CRITICAL] <app>: login changed / API key changed / holds a stale key / failed web login(s)" at once | n8n sysadmin | The 15-minute reporter saw a credential move or 3+ failed form logins in a quarter hour; the text says whether the stored login changed | [Locked out of an app](locked-out.md) |
| "Homelab digest <date>" at 8:00 Pacific | n8n | Yesterday's non-critical outages, Diun image updates, Scrutiny disk warnings, backup errors; or "nothing to report" | Skim; act on disk warnings and backup errors; schedule image updates as PRs |
| healthchecks.io "RicksGarage is down" (once configured) | healthchecks.io | That host has not pinged for 10 minutes: power cut, network down, or host frozen | Physically check; see [Power outage](power-outage.md) |

Instant alerts and the digest both use `smtp.gmail.com:465` with SSL, sending from the separate Gmail
account to Goobs's main inbox. The app password lives only inside n8n's credential store and Kuma's
notification; if it is rotated, re-enter it in both (Kuma: Settings > Notifications > "Email now
(critical)"; n8n: Credentials > "Gmail SMTP").

## The 27 Kuma monitors

Created by `docs/kuma_setup.py`. HTTP checks every 60 s with 2 retries unless noted.

| Group | Monitors |
| --- | --- |
| Critical (instant email) | Plex `/identity`, Emby `/emby/System/Ping`, Sonarr `/ping`, Radarr `/ping`, Prowlarr `/ping`, SABnzbd `/`, qBittorrent `/api/v2/app/version` (any 2xx to 4xx counts as up, since 403 means both gluetun and qBittorrent answered), Requests `/api/v1/status` |
| Digest | Bazarr, Audiobookshelf `/healthcheck`, Storyteller, RomM, MeTube, Tautulli `/status`, Nextcloud `/status.php`, Paperless, Stirling PDF, n8n `/healthz`, Huginn `/users/sign_in`, Homepage, Scrutiny `/api/health`, Edge (TCP 192.168.1.50:80), Piper (TCP 10200), AnythingLLM `/api/ping`, TrueNAS (HTTPS UI), Proxmox RicksGarage and RicksShip (HTTPS 8006) |
| Missing | ErsatzTV, Immich, Tdarr, Unmanic, wg-easy, Open WebUI (unreachable from the apps VM by design), the NFS mount itself, pool health |

To add monitors: append to `MONITORS` in `docs/kuma_setup.py` and have Goobs run
`~/.local/share/homelab-ansible/bin/python docs/kuma_setup.py` on the Mac. It tunnels over SSH to the
apps VM (Kuma's port is not reachable from the LAN), prompts for the Kuma login and optionally the
Gmail app password, skips existing names, and stores nothing. Kuma 2 needs `conditions="[]"` on every
monitor (the script handles it).

## The digest workflow

Since 2026-10-22 the digest is built by the "Homelab sysadmin" workflow, which also runs the
15-minute checks and the safe fixes: see [The junior sysadmin](sysadmin.md). The webhook paths below
are unchanged.

n8n workflow "Homelab digest" (`stacks/apps/n8n/homelab-digest.workflow.json`), active. Webhook
paths, all POST on `http://n8n:5678` inside the apps stack or `http://192.168.1.53:5678` from other VMs:

| Path | Sender | Payload shape handled |
| --- | --- | --- |
| `/webhook/homelab/kuma` | Uptime Kuma notification "digest" | Kuma's JSON (`monitor.name`, `heartbeat.status`, `msg`) |
| `/webhook/homelab/diun` | Diun on the apps VM | Diun JSON (`image`, `status`, `hostname`) |
| `/webhook/homelab/scrutiny` | Scrutiny shoutrrr generic webhook | `message`/`title` |
| `/webhook/homelab/backup` | `homelab-backup` on any VM | `{"message": ...}` |
| `/webhook/homelab/other` | anything else | first 300 chars of the body |

Events are kept in the workflow's static data (max 500) and cleared when the 08:00 email is built.
To test: open the workflow, click the "Every day 08:00" node, "Execute step"; expect a "nothing to
report" or a grouped email within a minute.

## Dashboards

- **Homepage** `home.tail9af84.ts.net`: every app with a live dot (LAN siteMonitor). No API widgets
  yet; adding Sonarr/Radarr/SABnzbd/Plex widgets needs their API keys in the Homepage config, which
  would have to move into the secrets.
- **Uptime Kuma** `status.tail9af84.ts.net`: history, response times, the notification settings.
- **Scrutiny** `disks.tail9af84.ts.net`: SMART for 9 disks, refreshed daily at 04:15 by the host
  collectors. Watch `ZVTAM9MF`.
- **Tautulli** `tautulli.tail9af84.ts.net`: Plex streams and history.
- **Proxmox** UIs for host CPU, RAM, swap (RicksGarage should stay out of swap) and VM state.

## Quiet gaps to be aware of

- Only the apps VM runs Diun; image updates on the other VMs are not reported.
- No alert on pool capacity (about 80% full) or on a DEGRADED pool beyond what Scrutiny implies.
- No alert if TSDProxy's names lose their certificate or the Tailscale key expires.
- The heartbeat is the only out-of-band signal, and it is not live yet.
