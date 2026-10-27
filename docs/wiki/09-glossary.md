# Glossary

Terms as they are used in this lab. Names of apps are in the [service pages](services/README.md).

| Term | Meaning here |
| --- | --- |
| **Apps VM** | VM 204, 192.168.1.53: personal data, databases and operations tools (Nextcloud, Paperless, n8n, Kuma, Postgres, ...). |
| **appdata** | `/opt/appdata/<app>` on each VM's local disk: an app's configuration and database. Backed up nightly. Media is never appdata. |
| **Arr apps / Servarr** | Sonarr (TV), Radarr (movies), Prowlarr (indexers), Bazarr (subtitles). They share one API style and one config convention. |
| **Authentik** | The single sign-on server on the edge VM. Traefik asks it, by forward-auth, whether a request is logged in before passing it to an admin app. |
| **autofs / wait-nas** | Two ways the NAS mount is handled: autofs mounts on first access; `wait-nas.service` makes Docker wait until the real NFS export is mounted so containers never see an empty folder. |
| **autoheal** | A small container that restarts any container whose healthcheck reports unhealthy. Runs in every stack. |
| **balloon** | Proxmox memory ballooning: a VM is given a maximum and a floor; the host can reclaim memory between the two. The media VM's floor was raised to 5 GB. |
| **cloud-init** | The first-boot configuration of a VM (user `goobs`, SSH key, static IP) written by OpenTofu into the Debian cloud image. |
| **Compose stack** | One `compose.yaml` per VM under `stacks/<vm>/`, deployed to `/opt/stacks/<vm>/` and run with `docker compose`. |
| **critical monitors** | The 8 Uptime Kuma monitors that send instant email: Plex, Emby, Sonarr, Radarr, Prowlarr, SABnzbd, qBittorrent, Requests. |
| **DAS** | The USB disk enclosure holding the five 20 TB drives, attached to RicksGarage and passed to the TrueNAS VM as virtual disks. |
| **DeepData** | The TrueNAS ZFS pool (RAIDZ1, 5 × 20 TB). `DeepData/NAS` is the dataset exported over NFS. |
| **digest** | The 08:00 Pacific email built by n8n from the previous day's non-critical events. |
| **direct play** | Plex/Emby sending a file to the TV or phone unchanged. The Tdarr settings exist to keep every file direct-playable (8-bit H.265, AAC/AC3, no 4K). |
| **DOCKER-USER** | The iptables chain Docker leaves for the administrator. The `lanfw` role fills it so published ports answer only to lab VMs unless listed as LAN-open. |
| **edge VM** | VM 201, 192.168.1.50: Tailscale subnet router, TSDProxy, Traefik, Authentik. The only VM that terminates tailnet traffic. |
| **Edge-only port** | A port that answers only to the edge VM (for example Open WebUI 3010), so it is reachable only through Authentik. |
| **gluetun** | The VPN container (Private Internet Access over OpenVPN, with port forwarding) whose network namespace qBittorrent shares; a kill switch by construction. |
| **gpu VM** | VM 203, 192.168.1.52: owns the Intel iGPU by PCI passthrough; runs Plex, Emby, Tdarr, Unmanic, Immich, ErsatzTV. |
| **hardlink** | A second directory entry for the same file. Download clients and Arr apps live on one filesystem so imports are instant and use no extra space. |
| **holding folder** | `to-delete/<date>` on the NAS: where removed duplicates and junk sit for 14 days before Goobs empties it. |
| **iGPU** | The Intel Iris Pro 580 inside the NUC's CPU, used for Quick Sync hardware transcoding and encoding. |
| **IaC** | Infrastructure as code: the repo is the source of truth; OpenTofu makes VMs, Ansible configures them and deploys the stacks. |
| **LAN-open** | A port deliberately reachable from every device on 192.168.1.0/24: Plex 32400, Emby 8096, Jellyseerr 5055. |
| **lanfw** | The Ansible role and script that write the DOCKER-USER firewall rules. |
| **media VM** | VM 202, 192.168.1.51: the download and library pipeline (Arr apps, SABnzbd, qBittorrent, Recyclarr, Jellyseerr, ...). |
| **NUC** | Intel mini PC. RicksGarage (storage host) and RicksShip (compute host) are NUC6i7KY "Skull Canyon"; RicksPortalGun is a NUC8i7BEH "Bean Canyon". |
| **NUT** | Network UPS Tools: the planned software that shuts the hosts down cleanly when a UPS runs low. |
| **PUID / PGID** | The user and group id a container runs as. Everything here is 3000:3000 (`storage`) unless the image cannot. |
| **quorum** | Proxmox cluster votes: three nodes, quorum two (since 2026-10-26; `two_node`/`wait_for_all` are gone). VMs start only on a host that is part of a quorate cluster. |
| **Recyclarr** | Syncs TRaSH Guides quality settings and custom-format scores into Sonarr and Radarr. Runs on a timer. |
| **Requests** | The household name for Jellyseerr (`requests.tail9af84.ts.net`), where anyone asks for a movie or show. |
| **RicksGarage** | Proxmox host 192.168.1.137: holds the DAS, runs TrueNAS and the media VM. |
| **RicksShip** | Proxmox host 192.168.1.37: runs edge, gpu, apps and ai. |
| **RicksPortalGun** | Proxmox host 192.168.1.38, the third NUC (64 GB), joined 2026-10-26; no VMs yet. |
| **SOPS / age** | How secrets are stored in git: `secrets.sops.env` is encrypted with an age key held on the Mac and on each VM. |
| **storage (user)** | uid/gid 3000 on every VM and on the NAS; owns the media and all appdata. |
| **subnet router** | The edge VM's Tailscale node advertising 192.168.1.0/24, so tailnet devices can reach LAN addresses. |
| **tailnet** | The lab's Tailscale network, `tail9af84.ts.net`. Every admin page is a `<app>.tail9af84.ts.net` name on it. |
| **TRaSH Guides** | Community quality and naming recommendations for the Arr apps; applied by Recyclarr, capped at 1080p here. |
| **TSDProxy** | Registers one Tailscale node per app so each gets its own `ts.net` name and certificate, then forwards to Traefik. |
| **Traefik** | The reverse proxy on the edge VM; routes each name to the right VM and port, after Authentik where required. |
| **vfio** | The Linux mechanism that hands a PCI device (the iGPU) to a VM instead of the host. |
| **warm-reboot hang** | The NUC6 quirk: `reboot` leaves the machine stuck; only a power-off and power button press works. |
| **ZVTAM9MF** | Serial of the 20 TB drive with a few USB command timeouts, watched in Scrutiny. |
