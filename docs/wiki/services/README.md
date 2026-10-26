# Services

One page per VM, and one for TrueNAS and the Proxmox hosts. Each service entry follows the same
shape: what it is for, the image, where it listens, how you log in, where its data lives, what it
talks to, how its health is checked, and its state on 2026-10-22.

| Page | Covers |
| --- | --- |
| [edge](edge.md) | Traefik, Authentik, TSDProxy, Tailscale subnet router, Docker socket proxy, wg-easy, autoheal |
| [media](media.md) | Sonarr, Radarr, Prowlarr, Bazarr, SABnzbd, gluetun + qBittorrent, Recyclarr, Jellyseerr, MeTube, Storyteller, Audiobookshelf, RomM |
| [gpu](gpu.md) | Plex, Emby, ErsatzTV, Immich, Tdarr, Unmanic |
| [apps](apps.md) | Postgres, Redis, Nextcloud, Paperless-ngx, Stirling-PDF, n8n, Huginn, Tautulli, Homepage, Uptime Kuma, Scrutiny, Diun, OliveTin, Dockge |
| [ai](ai.md) | Open WebUI, AnythingLLM, Piper, the LangChain/CrewAI worker |
| [TrueNAS and the hosts](truenas-and-hosts.md) | TrueNAS VM, the Proxmox hosts' own agents (Scrutiny collector, heartbeat, vfio), the old VMs |

## Common to every stack

- `restart: unless-stopped` and the label `autoheal: "true"` on every service, plus one `autoheal`
  container (`willfarrell/autoheal:1.2.0`, 30 s interval) that restarts unhealthy containers.
- `PUID`/`PGID` 3000 and `TZ=America/Los_Angeles` from the stack's `.env` (decrypted from
  `secrets.sops.env`).
- Config under `/opt/appdata/<service>` on the VM's local disk.
- Logs rotated by the Docker daemon (`local` driver, 20 MB x 3) and `live-restore` on, so a Docker
  daemon restart does not stop containers.
- Deployed by `ansible-playbook playbooks/site.yml --limit <vm> --tags stacks`, which runs
  `docker compose up -d --wait` for the services listed under `stack_services` in the inventory (or
  the whole stack with `--remove-orphans` when no list is given).
- Image tags: mostly major-version pins (`traefik:v3.3`, `postgres:16-alpine`, `uptime-kuma:2`) or
  `latest` for the LinuxServer media images. Diun on every VM reports new versions to the sysadmin;
  it updates the services in hosts.yml `auto_update` itself between 03:00 and 05:00 Pacific (ops verb
  `update`, see [sysadmin](../operations/sysadmin.md)); everything else is updated by hand.
