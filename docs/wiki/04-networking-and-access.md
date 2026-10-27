# Networking and access

## Addresses

The network is one flat `192.168.1.0/24` segment with the router at `192.168.1.1` as gateway and
DNS. There are no VLANs, so isolation comes from per-VM firewall rules rather than from the switch.

| Address | What | Notes |
| --- | --- | --- |
| 192.168.1.1 | Router | DHCP, DNS; no port forwards |
| 192.168.1.37 | RicksShip (Proxmox) | UI https://192.168.1.37:8006 |
| 192.168.1.38 | RicksPortalGun (Proxmox) | UI https://192.168.1.38:8006 (third NUC, 2026-10-26) |
| 192.168.1.137 | RicksGarage (Proxmox) | UI https://192.168.1.137:8006 |
| 192.168.1.209 | TrueNAS VM | UI https://192.168.1.209, NFS, SMB |
| 192.168.1.50 | edge VM | Traefik :80 (only TSDProxy and the lab use it), Tailscale subnet router, wg-easy 51820/udp when enabled |
| 192.168.1.51 | media VM | see port table |
| 192.168.1.52 | gpu VM | Plex 32400 (host network), Emby 8096 |
| 192.168.1.53 | apps VM | Postgres 5432 (lab VMs only), Scrutiny 8087 (also the three hosts) |
| 192.168.1.54 | ai VM | Open WebUI 3010 (edge VM only) |
| 192.168.1.14 | old Servarr VM 113 | free (VM removed 2026-10-26) |
| 192.168.1.15 | old Plex VM 117 | free (VM removed 2026-10-26) |
| 192.168.1.237 | former cluster node PlanetC137 | removed from the cluster 2026-10-21 |

Static IPs are set by cloud-init from `tofu/terraform.tfvars`. The addresses .50 to .54 were chosen
by Goobs and checked free before use; whether they sit outside the router's DHCP range is
`(unverified)`.

## Tailscale

- Tailnet: `tail9af84.ts.net`, with MagicDNS and HTTPS certificates enabled.
- **Subnet router**: the `tailscale` container on the edge VM (hostname `lab-router`, host network,
  `TS_ROUTES=192.168.1.0/24`). An `autoApprovers` rule in the tailnet ACL approves that route
  automatically, so Tailscale devices reach any LAN IP without an approval step after a rebuild.
- **Per-app names**: TSDProxy logs in about 31 ephemeral-looking devices, one per app, using the same
  reusable auth key (`TS_AUTHKEY` in the edge secrets; must be *Reusable*, not single-use, which was
  the overnight-build lesson). Each gets `<app>.tail9af84.ts.net` and a Let's Encrypt certificate
  from Tailscale. The first visit to a new name can take up to a minute while the certificate is
  issued.
- Devices must run the Tailscale client to use the names, even at home. Without it, LAN IPs still
  work for the LAN-open ports.
- Old Tailscale nodes from the previous setup (planetc137, tahoe-plex-vm, downloader-vm,
  ubuntu-storage) have been offline for over a year and can be removed from the admin console.
- The reusable key lives on the Mac at `~/.config/homelab/ts-authkey` and encrypted in
  `stacks/edge/secrets.sops.env`. It was created with a 90-day expiry, so around 2027-01-19 `(verify the exact date in the
  admin console)` TSDProxy will need a new key before its registered names can re-authenticate.

## Authentik (single sign-on)

- Authentik 2025.8 on the edge VM, UI at https://auth.tail9af84.ts.net, admin user `akadmin`
  (password set by Goobs; a bootstrap/recovery path was used once and the recovery link deleted).
- Traefik uses the embedded outpost as a forward-auth middleware
  (`http://authentik-server:9000/outpost.goauthentik.io/auth/traefik`), passing
  `X-authentik-username`, `X-authentik-groups` and `X-authentik-email` to the app.
- Only `akadmin` exists so far. There is no 2FA configured yet `(unverified)`; adding a TOTP stage
  is a reasonable next step since one login now guards every admin UI.
- Apps **behind** Authentik: Sonarr, Radarr, Prowlarr, Bazarr, SABnzbd, qBittorrent, MeTube, RomM,
  ErsatzTV, Tdarr, Unmanic, Paperless, Stirling-PDF, n8n, Huginn, Homepage, Uptime Kuma, Scrutiny,
  OliveTin, Dockge, Tautulli, Open WebUI, AnythingLLM, wg-easy.
- Apps that **skip** Authentik, because they have their own accounts and mobile clients: Plex,
  Emby, Requests (Jellyseerr), Immich, Nextcloud, Audiobookshelf, Storyteller, and Authentik itself.
- Most apps behind Authentik also keep their own login as a second lock, because their LAN ports are
  reachable from the lab VMs. Sonarr, Radarr and Prowlarr can run with `AuthenticationMethod=External`
  instead (`playbooks/arr-auth.yml`, 2026-10-22), Authentik being their only login. Open WebUI is the exception: it trusts `X-authentik-email` and signs the
  user in automatically, which is why its port is locked to the edge VM only.

## Ports and who may reach them

The LAN firewall (`ansible/roles/lanfw`, chain `DOCKER-USER`) applies to published container ports.
Host services (SSH on the VMs, Plex on host networking) are not covered by it.

Default for every published port: reachable from the five lab VMs (.50 to .54) and nothing else on
the LAN. Exceptions:

| Port | VM | Open to | Why |
| --- | --- | --- | --- |
| 32400/tcp | gpu | whole LAN | Plex is host-networked; the TVs need it (remote Plex apps come in through the plex.tv relay) |
| 8096/tcp | gpu | whole LAN | Emby for the TVs |
| 5055/tcp | media | whole LAN | Requests, so family can request without Tailscale |
| 51820/udp | edge | whole LAN (and a router forward, later) | wg-easy fallback VPN, once it is set up |
| 8087/tcp | apps | lab VMs plus 192.168.1.137, .37 and .38 | Scrutiny collectors on the hosts post here |
| 3010/tcp | ai | edge VM only | Open WebUI trusts the Authentik header |
| 3005/tcp | apps | edge VM only | Grafana trusts the Authentik header |
| 2003/tcp | apps | lab VMs plus 192.168.1.209 | TrueNAS pushes Graphite metrics |
| 9100/tcp (host) | every VM | apps VM only (`LANFW-IN`) | node-exporter, host-networked; 9002 too on edge (Tailscale metrics) |
| 5432/tcp | apps | lab VMs | Postgres, used by the ai VM's AnythingLLM and worker |

Full port map (LAN address, all behind the default rule unless listed above):

| VM | Service and port |
| --- | --- |
| edge (.50) | Traefik 80; wg-easy 51820/udp, 51821/tcp (not started) |
| media (.51) | Sonarr 8989, Radarr 7878, Prowlarr 9696, Bazarr 6767, SABnzbd 8080, qBittorrent 8085 (via gluetun), Jellyseerr 5055, MeTube 8081, Storyteller 8001, Audiobookshelf 13378, RomM 8095 |
| gpu (.52) | Plex 32400 (+ discovery ports, host network), Emby 8096, ErsatzTV 8409, Immich 2283, Tdarr 8265 (UI) and 8266 (server), Unmanic 8888 |
| apps (.53) | Postgres 5432, Nextcloud 8090 (nginx), Paperless 8000, Stirling-PDF 8070, n8n 5678, Huginn 3000, Tautulli 8181, Homepage 3001, Uptime Kuma 3002, Scrutiny 8087, OliveTin 1337, Dockge 5001 |
| ai (.54) | Open WebUI 3010, AnythingLLM 3011, Piper 10200 (Wyoming), worker 8100 (not started) |
| truenas (.209) | 443 UI, NFS 2049, SMB (not firewalled by the lab) |

## Traefik routes

Generated by `docs/gen_routes.py`; one router per app on the `web` entrypoint, matching
`Host(<app>.<tailnet>)`. The mapping of names to LAN targets:

| Name | Target | Name | Target |
| --- | --- | --- | --- |
| sonarr | .51:8989 | plex | .52:32400 |
| radarr | .51:7878 | emby | .52:8096 |
| prowlarr | .51:9696 | ersatztv | .52:8409 |
| bazarr | .51:6767 | photos | .52:2283 |
| sabnzbd | .51:8080 | tdarr | .52:8265 |
| qbittorrent | .51:8085 | unmanic | .52:8888 |
| requests | .51:5055 | cloud | .53:8090 |
| metube | .51:8081 | paperless | .53:8000 |
| storyteller | .51:8001 | pdf | .53:8070 |
| audiobooks | .51:13378 | n8n | .53:5678 |
| romm | .51:8095 | huginn | .53:3000 |
| home | .53:3001 | status | .53:3002 |
| disks | .53:8087 | olivetin | .53:1337 |
| dockge | .53:5001 | tautulli | .53:8181 |
| chat | .54:3010 | anythingllm | .54:3011 |
| auth | authentik-server:9000 (in-VM) | wireguard | wg-easy:51821 (in-VM) |

## Remote access policy

- Remote access is **Tailscale only** ([D05](07-decisions.md#d05-remote-access-only-through-tailscale)).
  No port on the router is open. The old Plex forward (6996) was removed on 2026-10-26: Starlink's
  carrier NAT meant it could never be reached, so remote Plex apps without Tailscale use the
  plex.tv relay, with or without it.
- **wg-easy** (WireGuard) is in the edge stack as a fallback way in if Tailscale is down, but it is
  not started: its first-run admin page would be open on the LAN until an admin password exists.
  Setting it up is a joint step with Goobs ([Open items](08-open-items.md)).
- Proxmox and TrueNAS UIs are reached by LAN IP, which works from the tailnet through the subnet
  route. They are not behind Authentik.

## Outbound traffic

- **Torrents** leave only through PIA: `gluetun` (OpenVPN, region set in the media secrets, Vancouver
  at build time because PIA has no port forwarding in US regions) with port forwarding on, and
  qBittorrent in gluetun's network namespace. If the tunnel is down, qBittorrent has no network.
  Since 2026-10-26 the torrent **searches** go the same way: Prowlarr's torrent indexers use gluetun's
  HTTP proxy (`gluetun:8888`, compose network only) and FlareSolverr shares gluetun's namespace.
- **Usenet** (SABnzbd, the Usenet indexers in Prowlarr) and the Arr apps talk to the internet directly
  from the media VM, on purpose: a PIA outage must not stop Usenet.
- **Mail** goes out from n8n and Uptime Kuma to `smtp.gmail.com:465` (implicit TLS; 587 with
  STARTTLS failed with a TLS version error in n8n).
- **AI** requests go from the ai VM to Google's Gemini API.
- **Heartbeat** pings go from the Proxmox hosts to healthchecks.io (once configured).

## DNS

The VMs use the router (192.168.1.1) as resolver. App names resolve through Tailscale's MagicDNS on
devices running Tailscale. There is no split DNS or local zone for `192.168.1.x` names; the LAN is
addressed by IP.
