# homelab-infra

Infrastructure as code for Goobs's home lab: two Intel NUC6i7KY Proxmox 8.4 hosts
(RicksGarage, RicksShip, 31 GiB RAM each, 2-node cluster with `two_node`) and a TrueNAS VM
serving a five-disk USB DAS (RAIDZ1, 5 x 20 TB).

Design and decisions: see the "Home lab app stack: infrastructure design" doc in the
Claude project. Step-by-step change log with undo notes: `~/HomeLab/changes-2026-10-21.md`
on Goobs's Mac.

## What runs where (2026-10-21)

| VM | Host | IP | Running | Held back |
| --- | --- | --- | --- | --- |
| truenas (110) | RicksGarage | 192.168.1.209 | not managed here (16 GB RAM) | |
| edge (201) | RicksGarage | 192.168.1.50 | Traefik, Authentik, TSDProxy (`<app>.tail9af84.ts.net`), Tailscale subnet router | wg-easy (admin not set up) |
| media (202) | RicksGarage | 192.168.1.51 | Sonarr, Radarr, Prowlarr, Bazarr, SABnzbd (migrated from the old Servarr VM), qBittorrent via PIA (gluetun, port forwarding), Recyclarr, Jellyseerr (migrated Overseerr), MeTube, Storyteller, Audiobookshelf, RomM | |
| gpu (203) | RicksShip | 192.168.1.52 | Plex (migrated, Quick Sync), Emby (Premiere, Quick Sync) | ErsatzTV, Immich, Tdarr, Unmanic |
| apps (204) | RicksShip | 192.168.1.53 | Postgres, Nextcloud, Paperless, Stirling-PDF, n8n, Huginn, Tautulli, Homepage, Uptime Kuma, Scrutiny, Diun, OliveTin (report-only), Dockge | |
| ai (205) | RicksShip | 192.168.1.54 | Piper | Open WebUI (no LLM key), AnythingLLM + worker |

Old VMs, stopped and kept for rollback: Servarr (113, RicksGarage), Plex (117, RicksShip).
Deferred: Kasm, local Ollama models (until the eGPU), Frigate (no cameras).

```
tofu/       VMs on Proxmox (bpg/proxmox provider; tofu@pve token)
ansible/    OS hardening, Docker, NFS, backups, LAN firewall, iGPU, stack deploys
stacks/     one Compose project per VM, secrets in secrets.sops.env
docs/       gen_routes.py regenerates Traefik and TSDProxy routes; torrent-fallback.md (Usenet first, torrents after 24 h)
```

## Rules this repo follows

- Automation never deletes or moves anything on TrueNAS. NAS folders it uses were created
  add-only (`Documents/Paperless`, `Backups/homelab`) or re-permissioned once by hand with
  Goobs's OK (`Media/NextCloud`, empty, now `www-data` 33:33 0770).
- Apps run as `storage` (uid/gid 3000) and see the NAS at `/mnt/nfs`, the same path the old
  Servarr VM used, so library paths and imports (renames in one dataset) keep working.
  Exceptions: n8n (image's own uid 1000, local data), Nextcloud (www-data).
- App config and databases live on each VM's local disk under `/opt/appdata`, never on NFS.
- Secrets are SOPS + age encrypted (`stacks/*/secrets.sops.env`). The age key is on the Mac
  (`~/.config/sops/age/keys.txt`, backed up next to the change log) and on each VM for root.
- Self-healing: `restart: unless-stopped`, healthchecks, autoheal in every stack, and a QEMU
  watchdog on every VM.
- Nightly config backups (role `backup`): DB dumps + SQLite online copies + appdata tar to
  `NAS/Backups/homelab/<vm>/<date>` (root-only), 14 days kept. Never media.
- LAN firewall (role `lanfw`): published container ports are reachable only from the five
  VMs (which includes the Traefik/Tailscale path); open to the whole LAN: Plex 32400, Emby
  8096, Jellyseerr 5055 (and SMB/NFS on TrueNAS). Open WebUI 3010 only from the edge VM.
- Disk health: Scrutiny collectors on both Proxmox hosts (`playbooks/proxmox-scrutiny.yml`).
- Downloads: Usenet first; torrents (qBittorrent via PIA) only after 24 h with nothing on Usenet,
  public trackers, seed to 1.0 or 3 days, hardlinked imports. Details in `docs/torrent-fallback.md`.

## Running it

```sh
# 1. VMs (token in ~/.config/homelab/proxmox-tofu.env on the Mac)
cd tofu
set -a; . ~/.config/homelab/proxmox-tofu.env; set +a
tofu plan && tofu apply

# 2. OS + stacks (ansible-core 2.15 venv: ~/.local/share/homelab-ansible)
cd ../ansible
ansible-playbook playbooks/site.yml --limit media              # one VM at a time
ansible-playbook playbooks/site.yml --limit media --tags stacks
```

Things learned the hard way:
- Debian's `generic` cloud image (not `genericcloud`) and a serial port are both required.
- The gpu VM's iGPU is attached by root (`qm set 203 --hostpci0 0000:00:02.0,pcie=1`); tofu
  ignores `hostpci` because the tofu@pve token can't set raw PCI devices.
- Compose doesn't restart a container when only a bind-mounted config file changes:
  restart TSDProxy/Traefik/etc. after route changes. Mount config folders, not single files.
- The NUC6 hosts hang on warm reboot: power off, then someone presses the power button.
- Power cuts happen (first one 2026-10-21). Cold start order: TrueNAS first on RicksGarage, then the
  VMs; the media/gpu/apps VMs hold Docker until the NAS share is mounted and non-empty
  (`wait-nas.service`, role `nfs`), so an early boot never shows the apps an empty library.
  Each Proxmox host pings healthchecks.io every 5 min (`playbooks/proxmox-heartbeat.yml`), which is
  the only alert that works while the lab is dark. Set the NUC BIOS to "After Power Failure: Power On".
  A UPS with NUT for a clean shutdown is the plan; see the "Power outage plan" doc.
- RicksGarage is tight on RAM (TrueNAS 16 GB + edge 3 + media 6): media's balloon floor
  is 5 GB because 3 GB starved it.
