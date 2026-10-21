# homelab-infra

Infrastructure as code for Goobs's home lab: two Intel NUC6i7KY Proxmox hosts
(RicksGarage, RicksShip, 31 GiB RAM each) and a TrueNAS VM serving a five-disk DAS.

Design and decisions: see the "Home lab app stack: infrastructure design" doc in the
Claude project.

## Layout

| VM | Host | IP | Stack |
| --- | --- | --- | --- |
| truenas (110, existing) | RicksGarage | 192.168.1.209 | not managed here |
| edge (201) | RicksGarage | 192.168.1.50 | Traefik, Authentik, TSDProxy, Tailscale, wg-easy |
| media (202) | RicksGarage | 192.168.1.51 | Arr apps, SABnzbd, qBittorrent via PIA, Jellyseerr, MeTube, Storyteller, Audiobookshelf, RomM |
| gpu (203) | RicksShip | 192.168.1.52 | Plex, Emby, ErsatzTV, Immich, Tdarr, Unmanic (Iris Pro 580) |
| apps (204) | RicksShip | 192.168.1.53 | Nextcloud, Paperless, Stirling-PDF, n8n, Huginn, Postgres, ops tools |
| ai (205) | RicksShip | 192.168.1.54 | Open WebUI, AnythingLLM, Piper, LangChain/CrewAI worker |

Deferred: Kasm, local Ollama models (until the eGPU), Frigate (no cameras).

```
tofu/       VMs on Proxmox (bpg/proxmox provider)
ansible/    OS hardening, Docker, NFS mounts, stack deploys
stacks/     one Compose project per VM
docs/       helpers (gen_routes.py regenerates Traefik and TSDProxy routes)
```

## Rules this repo follows

- Nothing on TrueNAS is deleted, moved or re-permissioned by automation. NFS mounts only.
- Every app runs as `storage` (uid/gid 3000) and sees the NAS at `/mnt/nfs`, the same path
  the old Servarr VM used.
- App config and databases live on each VM's local disk under `/opt/appdata`, never on NFS.
- Secrets are SOPS + age encrypted (`stacks/*/secrets.sops.env`); see `secrets.example.env`
  in each stack for the variables.
- Self-healing: `restart: unless-stopped`, healthchecks, autoheal in every stack, and a
  QEMU watchdog on every VM.

## Running it

```sh
# 1. VMs
cd tofu
export PROXMOX_VE_ENDPOINT=https://192.168.1.137:8006/
export PROXMOX_VE_API_TOKEN='tofu@pve!homelab=<secret>'
tofu init && tofu plan && tofu apply

# 2. OS + stacks
cd ../ansible
ansible-playbook playbooks/site.yml --limit edge   # one VM at a time
```

The NUC6 hosts hang on warm reboot: any host reboot needs someone at the power button.
