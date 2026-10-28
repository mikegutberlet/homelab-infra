# Decision records

Why the lab is the way it is. Each record gives the context, the decision, who made it and when, and
what it costs or rules out. "Goobs" means Goobs chose it in the thread or on a decision card;
"design" means it came from the design doc and Goobs approved the design.

## Hardware and platform

### D01: VMs, not LXC

**Context.** Goobs asked for "a new VM or set of VMs that orchestrate Linux containers or containers
running natively within Proxmox". **Decision (design, 2026-10-21).** Full VMs running Docker
Compose. **Why.** A VM is a real kernel boundary between internet-facing download clients and
personal data; NFS mounts behave normally (NFS inside unprivileged LXC is awkward); whole-VM backups
and watchdog resets are simple; iGPU passthrough is cleaner. **Cost.** Some RAM per VM for the
guest kernel, which matters on 31 GiB hosts.

### D02: Everything from one Git repo with OpenTofu, Ansible and SOPS

**Decision (design).** VMs by OpenTofu (`bpg/proxmox`), OS and deploys by Ansible, apps as Compose
files, secrets encrypted with age. **Why.** A dead VM is one command from coming back identical;
every change is a reviewed PR; secrets never sit in plain text in Git or chat. **Consequences.** Goobs
runs the tools from the Mac; Dockge is view-only; the age key must be backed up (it is, next to the
change log).

### D03: Five VMs split by trust and resource profile

**Decision (design, resized after the inventory).** edge, media, gpu, apps, ai; a sixth sandbox VM
for Kasm was dropped. **Why.** A compromised torrent client cannot see photos; a runaway transcode
cannot starve Nextcloud; the media VM sits beside TrueNAS so library I/O stays on the virtual bridge;
monitoring sits on the other host so it survives a storage-host failure. **Sizing.** From the
inventory: RicksGarage 16+3+6 GB, RicksShip 10+10+4 GB, each leaving 4 to 5 GiB for the host.

### D04: Defer Kasm, local Ollama and Frigate

**Decision (Goobs, card 2026-10-21 05:43 UTC).** Leave Kasm and local Ollama out; Frigate on hold.
**Why.** The NUCs are at their maximum RAM; Kasm needs 4 to 8 GB; Ollama on this CPU without the
eGPU runs only tiny models slowly; there are no cameras. **Revisit when** the eGPU or another
machine arrives.

### D05: Remote access only through Tailscale

**Decision (Goobs, 2026-10-21).** No open router ports; the Tailnet is the way in. **Former
exception (Goobs, 2026-10-21 18:31 UTC).** The existing Plex forward (6996 to 32400) was re-pointed
to the new Plex so clients without Tailscale keep working. The internet is Starlink (carrier NAT),
so it never could, and Goobs had it **removed on 2026-10-26**; remote Plex apps use the plex.tv
relay. Plex's own Remote Access switch must still stay on, or plex.tv stops listing the server and
even home devices lose it; the sysadmin turns it back on when it finds it off (2026-10-25, after it
went off in a power cut). **Fallback.** wg-easy stays in the edge stack for a
Tailscale outage, not started yet.

### D06: Authentik forward auth, with exceptions

**Decision (design, implemented 2026-10-21).** Traefik sends admin UIs through Authentik; apps with
their own mobile clients (Plex, Emby, Jellyseerr, Immich, Nextcloud, Audiobookshelf, Storyteller)
skip it and rely on their own logins. **Why.** One login with the option of 2FA for the whole lab;
mobile apps cannot complete a browser SSO redirect. **Consequence.** Most apps keep their own login
as a second lock because their ports are reachable from the lab VMs; Open WebUI's trusted-header
login forces the edge-only firewall rule.

### D07: One app user, uid 3000

**Decision (design, confirmed by the inventory).** Every container runs as `storage` 3000:3000, the
user that already owns the library. **Why.** The old split (SABnzbd 1000, Arr apps 3000) broke every
import from August to October; matching the existing owner avoids any bulk chown on 50 TB. **Exceptions**
only where an image forces a uid (n8n, AnythingLLM 1000; Nextcloud 33).

### D08: App data on local disk, bulk data on NFS

**Decision (design).** Configs and databases in `/opt/appdata` on each VM; media, photos, documents
and backups on the NAS. **Why.** SQLite and Postgres corrupt on NFS under load; app state is small;
a VM's disk is then a config backup by nature. **Consequence.** The gpu VM carries 40 GB of Plex
metadata locally; Immich's local data is too big for the nightly tar and is excluded.

### D09: Plex and Emby side by side

**Decision (Goobs, 2026-10-21 04:28 UTC).** Run both on the gpu VM over the same read-only library.
**Why.** "Sometimes Plex is better than Emby, and I would like to use Emby, but Plex is a necessary
evil for now." **Cost.** Two metadata databases, two transcoders sharing one iGPU; Premiere was
needed for Emby hardware transcoding (Goobs has it).

### D10: Jellyseerr replaces Overseerr

**Decision (design, migrated 2026-10-21).** **Why.** Works with Plex, Emby and Jellyfin, so the
request portal does not lock in the media-server choice. Overseerr's data converted in place (2
users, 327 requests) after a small `settings.json` fix.

### D11: Recyclarr replaces the scores in Goobs's existing profiles

**Decision (Goobs, card 2026-10-21 17:19 UTC: "Replace with TRaSH").** TRaSH templates are applied by
name to "Anything Not Terrible " (Sonarr, WEB-1080p) and "Anything Not terrible" (Radarr, HD Bluray +
WEB). **Guardrails.** Backup before syncing; upgrades through RSS only, no mass search; the x265 (HD)
penalty zeroed for Radarr so Tdarr's output is not "upgraded" back to H.264.

### D12: No 4K, 1080p cap everywhere

**Decision (Goobs, 2026-10-21 17:27 UTC).** "I do not want 4k, they take up too much storage and my
network/devices have a hard time playing 4k." Both profiles top out at 1080p; existing 4K files are
candidates for down-conversion.

### D13: Gemini instead of local Ollama (for now)

**Decision (Goobs, 2026-10-21 22:00 UTC).** Open WebUI and AnythingLLM use Goobs's Google Gemini key
(`gemini-flash-latest`). **Why.** No eGPU yet (D04). The ai VM keeps the shape that lets Ollama slot
in later without changing the apps.

### D14: Pass the whole iGPU to the gpu VM

**Decision (design; applied 2026-10-21 17:33 UTC with Goobs at the power button).** Skylake has no
SR-IOV, so the Iris Pro 580 goes entirely to VM 203 via vfio. **Consequence.** RicksShip's console is
dark after boot; every GPU consumer must live in that VM; RicksGarage keeps its own iGPU unused.

### D15: TrueNAS VM RAM cut to a fixed 16 GB

**Decision (Goobs, 2026-10-21 05:18 UTC, in their own words as the Mac's safety rule requires).**
**Why.** 32 GB on a 31 GiB host made the host swap and the scrub crawl at 33 MB/s. **Result.** 15 GiB
free on the host; scrub 10x faster. Old config saved on the Mac.

### D16: Two-node cluster with `two_node`, no HA

**Decision (Goobs approved the host phase, 2026-10-21 05:40 UTC).** Remove the stale third node;
set `two_node: 1`. No Proxmox HA. **Why.** HA needs a third vote and could not move the storage VMs
anyway because TrueNAS is tied to the DAS. **Consequence.** `wait_for_all` after a cold start of both
hosts.

**Update 2026-10-26.** A third NUC (RicksPortalGun, 192.168.1.38) joined and `two_node` was
removed: 3 votes, quorum 2, no `wait_for_all`. Still no HA.

### D17: Debian 12 `generic` cloud image, serial console, guest agent via vendor data

**Decision (learned overnight 2026-10-21).** The `genericcloud` image's kernel has no AHCI driver
(never saw the cloud-init drive) and no i915; without a serial port the first boot panicked. The
provider waits for the guest agent, so a cloud-init snippet installs it. These are repo facts, kept
here because they cost a rebuild.

## Data and safety

### D18: Never delete on TrueNAS; no snapshots

**Decision (Goobs, 2026-10-21 04:33 UTC).** No ZFS snapshots (pool too full, accidental loss
accepted). In return, nothing is deleted on purpose: automation never deletes on the NAS, removals go
to a dated holding folder with a manifest, old VMs are powered off not deleted, and every NAS folder
change needs Goobs's explicit OK. **Why this is a decision record.** It overrides the design doc's
"snapshot first" rule and shapes every runbook.

### D19: Config-only backups, nightly, 14 days, on the NAS

**Decision (Goobs: "back up all my configs, not my media"; implemented 2026-10-21 evening).**
`homelab-backup` on every VM dumps databases and tars appdata to `Backups/homelab/<vm>/<date>`,
root-only, pruning only its own folders. **Not covered.** Media, Nextcloud user files, Photos,
Immich's local data, and anything off-site. The design's Proxmox Backup Server and off-site copy
(B2) remain future options.

### D20: LAN firewall in `DOCKER-USER`, allow-list by VM

**Decision (design; applied 2026-10-21 21:17 UTC).** Published ports accept the five lab VMs; Plex,
Emby and Requests are open to the LAN; Open WebUI is edge-only. **Why.** A flat network with no VLANs
means the only enforcement point is each VM. **Consequence.** Health checks must come from a lab VM
(Homepage and Kuma on the apps VM), and admin UIs are unreachable from the couch without Tailscale,
which is the intent.

### D21: Docker waits for the NAS, with no timeout

**Decision (after the 2026-10-21 power cut, PR #7).** `wait-nas.service` blocks Docker on media, gpu
and apps until every share is a real mount with content. **Why.** An early boot bound an empty autofs
directory into the containers; Radarr and Sonarr would treat the library as deleted. **Consequence.**
If the NAS never returns, those VMs' apps stay down on purpose; the heartbeat covers alerting.

## Operations

### D22: Monitoring on the compute host, external heartbeat for the rest

**Decision (design).** Uptime Kuma, n8n and Scrutiny on the apps VM (RicksShip); healthchecks.io
pings from both hosts. **Why.** If RicksGarage (TrueNAS, edge) dies, alerting survives; if the whole
lab is dark, only something outside the house can notice.

### D23: Email alerts, instant only for critical media apps

**Decision (Goobs, 2026-10-21 21:27 UTC).** Instant email for Plex, Emby, Sonarr, Radarr, Prowlarr,
SABnzbd, qBittorrent/VPN and Requests; one digest at 8:00 Pacific for everything else. Mail is sent
from a separate Gmail account Goobs owns to their main inbox. The Gmail app password is typed by Goobs
into n8n and the Kuma script and is never written to a file or handled in chat.

### D24: Goobs's own passwords are never stored for automation

**Decision (correction made 2026-10-21 21:24 UTC).** An earlier step that asked Goobs to save their
Kuma login to a file was withdrawn; scripts prompt instead. Generated service passwords (Huginn,
Paperless, qBittorrent) may live in `~/.config/homelab/` on the Mac, mode 600.

### D25: Host restarts are always a cold power-off with Goobs present

**Decision (after both NUCs hung on 2026-10-21).** Guests down cleanly, host powered off, button
pressed by a person. NAS restarts additionally need Goobs's approval in their own words (a rule
enforced by the Mac-side safety filter).

### D26: UPS with NUT, 1500 VA class

**Recommendation (outage plan, 2026-10-22), awaiting Goobs's purchase.** USB to RicksGarage as NUT
server, RicksShip and TrueNAS as clients; shut down after 2 minutes on battery or at 50%, VMs first,
TrueNAS last, hosts off, UPS cuts output so the NUCs see a power-on edge. Router, modem and switch on
the UPS too.

### D27: Tdarr defaults and no pilot

**Decision (Goobs, 2026-10-21 22:48 and 23:32 UTC).** Quick Sync 8-bit HEVC q22, keep English plus
original-language tracks, junk to the holding folder, Radarr penalty off; skip the device test and the
5-movie pilot. The one safety net kept: first-day originals go to the holding folder.

### D28: ts.net names instead of a domain

**Decision (Goobs, card 2026-10-21 04:26 UTC).** Free Tailscale certificates and
`<app>.tail9af84.ts.net` names rather than a $10/year domain. **Cost.** Longer names; devices need the
Tailscale client even at home; names are tied to Tailscale. **Consequence.** TSDProxy exists only for
this.

### D29: Both media servers and Requests stay open on the LAN

**Decision (Goobs, 2026-10-21 19:12 UTC, "Plex, Emby and Requests stay open on your network as they
are now").** The household uses them from TVs that do not run Tailscale.

### D30: TrueNAS data preserved in place; migrate app by app

**Decision (Goobs, 2026-10-21 04:28 to 04:33 UTC).** Existing pool, folders and layout are kept;
apps move one at a time with the old one stopped first; the old VMs stay off, not deleted, for at
least a week. The sabnzb/complete leftovers are never deleted without an explicit OK.

### D31: Media apps update themselves; database apps stay manual

**Decision (Goobs, card 2026-10-26 19:46 UTC, "Alerts + auto-update safe apps"; then "Can the alerts
and updates following the alert come directly from and to my Sys Admin").** Diun runs on every VM and
reports to the sysadmin. Internet-facing media apps (Plex, Emby, Jellyseerr, the *arr apps, SABnzbd,
qBittorrent, FlareSolverr; since 2026-10-28 also MeTube and the python helper scripts, which keep no
database) are updated by the sysadmin between 03:00 and 05:00 Pacific. **Cost.** A bad
upstream release can break one of them overnight until it is rolled back. **Consequence.** Authentik,
Nextcloud, Immich, Paperless, databases and pinned images (ErsatzTV, Kometa) stay manual; they show as
"update available (by hand)".

### D32: The sysadmin gets an investigator (hybrid, Sonnet), read-only, fixes only on Confirm

**Decision (Goobs, card 2026-10-27 04:01 UTC, "C. Hybrid with Claude Code engine", after "I want him
to be just as useful as claude code is").** The chat stays on Claude Haiku 5.5; hard questions and new
critical alerts go to a Claude Agent SDK agent on Claude Sonnet 5.5 (container on the apps stack)
that reads logs, configs, the Proxmox/TrueNAS APIs and the repo through a read-only `inspect` user and
may propose one fix, including a short script, which runs only after Goobs presses Confirm and passes
a never-list on both n8n and the VM. **Cost.** About $5 to $20 a month, capped at $2 a day; answers
take 1 to 4 minutes. **Consequence.** No unattended custom fixes: alert investigations only email
their findings. The design doc first put it on its own VM on RicksPortalGun; it runs on the apps
VM instead (now on RicksPortalGun since the 2026-10-27 move), which saves a VM and keeps it next
to n8n.
