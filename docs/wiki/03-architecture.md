# Architecture

The lab is built in layers, and each layer has one job:

1. **Hardware**: two NUCs, one DAS. See [Hardware](02-hardware.md).
2. **Proxmox**: a two-node cluster that runs VMs. VMs, not LXC containers, because a VM gives a real
   kernel boundary between the internet-facing download clients and personal data, cleaner NFS
   mounts, and simple whole-VM backups ([D01](07-decisions.md#d01-vms-not-lxc)).
3. **TrueNAS VM**: owns the pool and serves one NFS export. It is the only stateful thing that is not
   rebuilt from code.
4. **Five Debian 12 VMs**, each created by OpenTofu from a cloud image, prepared by Ansible, and
   running exactly one Docker Compose stack. Apps are grouped by trust level and resource profile
   ([D03](07-decisions.md#d03-five-vms-split-by-trust-and-resource-profile)).
5. **Edge**: the single way in. Tailscale names, TLS, Traefik, Authentik.
6. **Operations**: backups, firewall, monitoring, alerting and self-healing, all defined in the repo.

## Physical and virtual layout

```mermaid
flowchart LR
    subgraph LAN["Home LAN 192.168.1.0/24 (flat, no VLANs)"]
        RTR["Router 192.168.1.1<br/>DHCP, DNS, no port forwards"]
        subgraph RG["RicksGarage 192.168.1.137"]
            direction TB
            DAS[("DAS 5 x 20 TB")] --> TN["truenas 110<br/>.209"]
            E["edge 201<br/>.50"]
            M["media 202<br/>.51"]
        end
        subgraph RS["RicksShip 192.168.1.37"]
            direction TB
            G["gpu 203<br/>.52<br/>+ Iris Pro 580"]
            A["apps 204<br/>.53"]
            I["ai 205<br/>.54"]
        end
    end
    INET((Internet)) --- RTR
    TSN((Tailnet<br/>tail9af84.ts.net)) -. "WireGuard" .- E
    PIA((PIA VPN)) -. "OpenVPN from gluetun" .- M
```

## How a request reaches an app

Every web UI is published under its own Tailscale name. TSDProxy on the edge VM registers each name
as a device on the tailnet and gets a trusted certificate for it, terminates TLS, and hands the
request to Traefik over plain HTTP inside the VM. Traefik matches the `Host` header to a route, sends
the request through the Authentik forward-auth middleware when the app is behind SSO, and proxies it
to the app's LAN address and port. The LAN firewall on every VM only lets the five lab VMs reach
those ports, so the edge VM is the only path for a phone or laptop.

```mermaid
sequenceDiagram
    autonumber
    participant D as Device on Tailscale
    participant T as TSDProxy (edge)<br/>sonarr.tail9af84.ts.net
    participant K as Traefik (edge) :80
    participant AK as Authentik (edge)
    participant S as Sonarr (media) 192.168.1.51:8989
    D->>T: HTTPS (Tailscale cert)
    T->>K: HTTP + X-Forwarded-Proto https
    K->>AK: forwardAuth /outpost.goauthentik.io/auth/traefik
    alt no session
        AK-->>K: 302 to auth.tail9af84.ts.net
        K-->>D: redirect, user logs in (akadmin)
        D->>T: retry with session cookie
    end
    AK-->>K: 200 + X-authentik-username/email/groups
    K->>S: HTTP, LAN (allowed by lanfw: source is the edge VM)
    S-->>D: page (Sonarr's own login may follow)
```

Two details that caused real bugs on build day: Traefik must trust `X-Forwarded-*` headers from
TSDProxy (configured as the Docker range `172.16.0.0/12`), otherwise Authentik builds `http://` URLs
and the browser blocks them as mixed content; and apps with their own mobile clients (Plex, Emby,
Immich, Nextcloud, Jellyseerr, Audiobookshelf, Storyteller) bypass the Authentik middleware, because
their apps cannot complete a browser SSO redirect
([D06](07-decisions.md#d06-authentik-forward-auth-with-exceptions)).

Route definitions are generated: `docs/gen_routes.py` holds the single list of apps (host, port,
behind SSO or not) and writes both `stacks/edge/traefik/dynamic/routes.yml` and
`stacks/edge/tsdproxy/apps.yaml`.

## Access boundaries

```mermaid
flowchart TB
    subgraph away["Away from home"]
        PH["Phone / laptop<br/>with Tailscale"]
        PLX["Plex app, no Tailscale"]
    end
    subgraph home["Home LAN, no Tailscale"]
        TV["Roku TVs, Pixel"]
    end
    PH -- "any app name via SSO" --> EDGE["edge VM<br/>TSDProxy + Traefik + Authentik"]
    PH -- "subnet route 192.168.1.0/24" --> LANIP["Proxmox UIs, TrueNAS UI,<br/>LAN-open ports"]
    PLX -- "plex.tv relay" --> PLEX["Plex (gpu)"]
    TV -- "LAN" --> PLEX
    TV -- "LAN :8096" --> EMBY["Emby (gpu)"]
    TV -- "LAN :5055" --> REQ["Requests (media)"]
    TV -. "blocked by lanfw" .-> ADMIN["Every admin UI<br/>(Sonarr, Nextcloud, n8n, ...)"]
    EDGE --> ADMIN
```

The firewall rules (`ansible/roles/lanfw`) rebuild the `DOCKER-USER` iptables chain on every Docker
start: established traffic passes; the five lab VMs (.50 to .54) reach everything; a short list of
ports is open to the whole LAN (Plex is host-networked so it is open by nature; Emby 8096; Jellyseerr
5055; wg-easy 51820/udp once enabled); the Scrutiny port 8087 on the apps VM also accepts the two
Proxmox hosts; Open WebUI's port 3010 accepts only the edge VM; everything else new from the LAN is
dropped. Details in [Networking and access](04-networking-and-access.md).

## Storage and data flow

```mermaid
flowchart LR
    subgraph NAS["TrueNAS pool DeepData, dataset NAS (NFS 4.2, export to 192.168.1.0/24)"]
        MOV["Media/Movies"]
        TV["Media/TVShows"]
        OTH["Media/{Music, Books, Audiobooks,<br/>Podcasts, Games, meTube}"]
        SAB["sabnzb/complete, incomplete"]
        PH["Photos (read-only to apps)<br/>Photos/Immich-uploads (new)"]
        NC["Media/NextCloud (www-data)"]
        DOC["Documents/Paperless/{consume,media,export}"]
        BK["Backups/homelab/<vm>/<date> (root only)"]
        HOLD["to-delete/2026-10-21 (14-day holding folder)"]
    end
    subgraph media["media VM /mnt/nfs (rw, uid 3000)"]
        SABN[SABnzbd] --> SAB
        QB[qBittorrent] --> SAB
        SAB -- "hardlink / rename" --> RAD[Radarr] --> MOV
        SAB -- "hardlink / rename" --> SON[Sonarr] --> TV
        BAZ[Bazarr] --> MOV
        BAZ --> TV
        MT[MeTube] --> OTH
        ABS[Audiobookshelf] --> OTH
        ROM[RomM] --> OTH
    end
    subgraph gpu["gpu VM"]
        PLEX[Plex] -. "read-only" .-> MOV
        EMBY[Emby] -. "read-only" .-> TV
        ETV[ErsatzTV] -. "read-only" .-> MOV
        TD[Tdarr] -- "rw when the run starts" --> MOV
        TD --> HOLD
        IMM[Immich] -. "read-only external library" .-> PH
        IMM --> PH
    end
    subgraph apps["apps VM"]
        NCA[Nextcloud] --> NC
        PPL[Paperless] --> DOC
    end
    ALLVM["every VM: homelab-backup.timer 03:30"] --> BK
```

Rules that shape this picture ([Storage](05-storage.md) has the detail):

- One dataset holds downloads and library, so an import is a hardlink or rename, never a copy.
- Every VM mounts the share at the same path, `/mnt/nfs`, that the old Servarr VM used, so
  migrated database paths kept working.
- App configs and databases live on each VM's local disk under `/opt/appdata`; SQLite and Postgres
  are never put on NFS.
- Media servers and re-encoders mount media read-only until a project needs otherwise.
- Nothing on the NAS is deleted by automation; removals go to a dated holding folder first.

## The media pipeline and its APIs

```mermaid
flowchart LR
    U["Family member"] --> JS["Jellyseerr<br/>(Plex sign-in)"]
    JS -- "API: add movie" --> RAD[Radarr]
    JS -- "API: add series" --> SON[Sonarr]
    PRW[Prowlarr] -- "syncs indexers + API keys" --> RAD
    PRW -- "syncs indexers" --> SON
    IDX["Usenet indexers<br/>NZBgeek, NzbPlanet<br/>(GingaDADDY disabled, 100/day cap)"] --> PRW
    RAD -- "NZB" --> SAB[SABnzbd<br/>8 Usenet servers]
    SON -- "NZB" --> SAB
    RAD -- "torrent" --> QB[qBittorrent]
    SON -- "torrent" --> QB
    GL["gluetun<br/>PIA OpenVPN, kill switch,<br/>forwarded port -> qBittorrent API"] --- QB
    SAB -- "completed" --> RAD
    SAB -- "completed" --> SON
    QB -- "completed" --> RAD
    RAD -- "hardlink into Media/Movies" --> LIB[("NAS library")]
    SON -- "hardlink into Media/TVShows" --> LIB
    RCY["Recyclarr<br/>TRaSH profiles, daily"] -- "API" --> RAD
    RCY -- "API" --> SON
    BAZ[Bazarr] -- "API" --> RAD
    BAZ -- "API" --> SON
    BAZ -- "subtitles" --> LIB
    LIB --> PLEX[Plex]
    LIB --> EMBY[Emby]
    PLEX -- "library updates" --> JS
    TAU[Tautulli] -- "API" --> PLEX
    ETV[ErsatzTV] -- "Plex media source" --> PLEX
    ETV -- "HDHomeRun tuner :8409" --> PLEX
    TD[Tdarr] -- "re-encode in place,<br/>then Radarr rescan" --> LIB
```

## Operations flows

### Monitoring and alerting

All alerting runs on the apps VM, deliberately on the *other* host from TrueNAS and the edge VM, so a
RicksGarage failure still gets reported. The only alert that works while the whole lab is dark is the
external heartbeat.

```mermaid
flowchart LR
    subgraph sources["Event sources"]
        KUMA["Uptime Kuma<br/>27 HTTP/TCP monitors, 60 s"]
        DIUN["Diun<br/>image updates every 6 h"]
        SCR["Scrutiny hub<br/>SMART from host collectors (04:15 daily)"]
        BKP["homelab-backup on each VM"]
    end
    KUMA -- "8 critical monitors:<br/>Plex, Emby, Sonarr, Radarr, Prowlarr,<br/>SABnzbd, qBittorrent, Requests" --> MAILNOW["SMTP smtp.gmail.com:465<br/>from the alerts Gmail account"]
    KUMA -- "other 19 monitors<br/>webhook /webhook/homelab/kuma" --> N8N["n8n workflow 'Homelab digest'"]
    DIUN -- "/webhook/homelab/diun" --> N8N
    SCR -- "/webhook/homelab/scrutiny" --> N8N
    BKP -- "/webhook/homelab/backup (on error)" --> N8N
    N8N -- "08:00 Pacific" --> DIGEST["Daily digest email"]
    MAILNOW --> GOOBS["Goobs's inbox"]
    DIGEST --> GOOBS
    HOSTS["Proxmox hosts cron */5 min"] -- "ping" --> HC["healthchecks.io<br/>(URLs not yet set)"] -- "no ping for 10 min" --> GOOBS
    HP["Homepage"] -. "LAN siteMonitor dots" .-> sources
```

### Self-healing layers

| What breaks | How it is caught | Automatic fix |
| --- | --- | --- |
| A container exits | Docker | `restart: unless-stopped` on every service |
| A container hangs but stays up | Compose healthcheck (HTTP or command) on most services | `autoheal` restarts a container labelled `autoheal=true` after failed checks |
| The VPN tunnel drops | gluetun healthcheck | qBittorrent shares gluetun's network namespace, so it has no network at all until gluetun reconnects (kill switch) |
| The NAS share is gone at boot | `wait-nas.service` | Docker does not start on media, gpu and apps until every NFS share is mounted and non-empty, with no timeout |
| A whole VM freezes | QEMU `i6300esb` watchdog fed by the `watchdog` daemon | Proxmox resets the VM |
| A host freezes or the power fails | External heartbeat (once URLs are set) | None; a person presses the power button. See [Power outage](operations/power-outage.md) |
| A bad image update | Nothing updates itself; Diun only notifies | Pin or revert in Git and redeploy |
| Lost config or database | Nightly backups, 14 days | Restore from `Backups/homelab/<vm>/<date>` ([Backups and restore](operations/backups-and-restore.md)) |

### Boot sequence after a cold start

```mermaid
flowchart TB
    PWR["Power returns"] --> BIOS{"NUC BIOS<br/>After Power Failure = Power On?"}
    BIOS -- "not set yet" --> BTN["Goobs presses both power buttons"]
    BIOS -- set --> BOOT
    BTN --> BOOT["Both hosts boot Proxmox"]
    BOOT --> Q{"Both nodes seen?<br/>(wait_for_all)"}
    Q -- no --> HOLD["Neither host starts VMs"]
    Q -- yes --> RG["RicksGarage: truenas 110 (order 1, 120 s)<br/>then edge 201 (order 3), media 202 (order 4)"]
    Q -- yes --> RS["RicksShip: gpu 203, apps 204, ai 205<br/>(order 10, 30 s apart; no dependency on TrueNAS)"]
    RG --> POOL["TrueNAS imports DeepData, NFS up"]
    RS --> WAIT["wait-nas.service blocks Docker<br/>until /mnt/nfs is mounted and non-empty"]
    POOL --> WAIT
    WAIT --> DOCKER["Docker starts, lanfw applied,<br/>stacks come up with healthchecks"]
    DOCKER --> OK["Kuma green, Tailscale lab-router online,<br/>heartbeat pings resume"]
```

## Infrastructure as code

```mermaid
flowchart LR
    GIT["GitHub mikegutberlet/homelab-infra<br/>(private, PRs reviewed by Goobs)"]
    MAC["Goobs's Mac ~/HomeLab/homelab-infra<br/>tofu, ansible venv, sops, age key, API token"]
    GIT <--> MAC
    MAC -- "tofu apply (tofu@pve token)" --> PVE["Proxmox API<br/>VMs 201-205, cloud-init, image download"]
    MAC -- "ansible-playbook site.yml --limit <vm>" --> VMS["VM: base, docker, nfs, backup, lanfw, (gpu), stacks"]
    MAC -- "ansible-playbook proxmox-*.yml" --> HOSTS["Hosts: iGPU vfio, Scrutiny collector, heartbeat"]
    VMS -- "rsync stacks/<vm>/ to /opt/stacks<br/>sops decrypt secrets.sops.env -> .env<br/>docker compose up -d --wait" --> STACK["Running stack"]
```

| Layer | Tool | Where | Notes |
| --- | --- | --- | --- |
| VMs | OpenTofu, provider `bpg/proxmox` (~> 0.117) | `tofu/` | Token `tofu@pve!homelab`, secret in `~/.config/homelab/proxmox-tofu.env` on the Mac. `hostpci` and `initialization` are ignored after creation; the iGPU is attached by root with `qm set 203 --hostpci0 0000:00:02.0,pcie=1` because the token cannot set raw PCI devices |
| OS and services | ansible-core 2.15 in `~/.local/share/homelab-ansible` on the Mac | `ansible/` | Inventory `hosts.yml` lists VMs with per-host firewall and stack options; `site.yml` runs roles base, docker, nfs, backup, lanfw, gpu, stacks |
| Apps | Docker Compose, one project per VM | `stacks/<vm>/compose.yaml` | Deployed to `/opt/stacks/<vm>` on the VM; `stack_services` in the inventory limits which services start |
| Secrets | SOPS + age | `stacks/<vm>/secrets.sops.env`, `.sops.yaml` | One age key, on the Mac and on each VM for root. Decrypted to `/opt/stacks/<vm>/.env` (mode 600) at deploy time |
| Routes | `docs/gen_routes.py` | `stacks/edge/traefik/dynamic/routes.yml`, `stacks/edge/tsdproxy/apps.yaml` | Regenerate, then restart Traefik and TSDProxy (Compose does not restart on bind-mounted file changes) |
| Monitors | `docs/kuma_setup.py` | run by Goobs on the Mac | Prompts for the Kuma login and the Gmail app password, stores neither, idempotent by monitor name |
| Change log | Markdown | `~/HomeLab/changes-2026-10-21.md` on the Mac | Every host-level change with its undo step |

The repo's rules (never delete on TrueNAS, apps run as uid 3000, appdata local, secrets encrypted,
healthchecks everywhere) are listed in the top-level [README](../../README.md) and explained in
[Decision records](07-decisions.md).
