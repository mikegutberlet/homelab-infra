# Access and credentials

This page says **where** each credential lives and how it is used. It never contains the values.

## Principles

- Goobs's own passwords (Gmail, Kuma, app logins they chose) are never written to a file for
  automation; scripts prompt for them.
- Generated service secrets live in two places only: encrypted in the repo (`secrets.sops.env`) and
  in `~/.config/homelab/` on the Mac (mode 600).
- Nothing is ever pasted into chat. Hidden-prompt one-liners (`read -s`) were used for every key.
- The shared "IP's and Creds" document in the project files holds the original root/admin
  passwords for Proxmox, TrueNAS and the old VMs. Those should be rotated now that the build is done
  (the design said so), and the old VMs' passwords were typed into chat once.

## Where things are

| Credential | Lives | Used by |
| --- | --- | --- |
| age private key | Mac `~/.config/sops/age/keys.txt` (backed up next to the change log) and `/root/.config/sops/age/keys.txt` on each VM (copied by role `stacks`) | sops decrypts `secrets.sops.env` at deploy time |
| Stack secrets (DB passwords, app secret keys, API keys, PIA, Tailscale key, LLM key) | `stacks/<vm>/secrets.sops.env` (encrypted), decrypted to `/opt/stacks/<vm>/.env` (600) | Compose |
| Proxmox API token `tofu@pve!homelab` | Mac `~/.config/homelab/proxmox-tofu.env` | OpenTofu |
| SSH key `homelab_inventory_ed25519` | Mac `~/.ssh/`; public key authorized for root on both hosts, the TrueNAS user and `goobs` on VMs 201 to 205 (via cloud-init) | Ansible, Remote Control sessions |
| Tailscale reusable auth key | Mac `~/.config/homelab/ts-authkey`; in edge secrets as `TS_AUTHKEY` | TSDProxy (names) and the subnet router |
| PIA login | Mac `~/.config/homelab/pia.env`; in media secrets | gluetun |
| Sonarr / Radarr API keys | Inside each app; in media secrets for Recyclarr | Recyclarr, Prowlarr sync, Jellyseerr, Bazarr |
| qBittorrent WebUI password (`admin`) | Mac `~/.config/homelab/qbittorrent-webui` | Goobs |
| Huginn `admin` password (generated) | Mac `~/.config/homelab/huginn-admin` | Goobs (should change it in-app) |
| Paperless `goobs` password (generated) | Mac `~/.config/homelab/paperless-admin` | Goobs |
| n8n API key | Mac `~/.config/homelab/n8n-apikey` | Workflow activation, future automation |
| Gemini API key | Mac `~/.config/homelab/ai-apikey` (encrypted at rest by Goobs's setup); ai secrets as `LLM_API_KEY` | Open WebUI, AnythingLLM, worker |
| Gmail app password (alerts account) | Only inside n8n's "Gmail SMTP" credential and Kuma's "Email now (critical)" notification | Email |
| Authentik `akadmin` | Goobs's password manager | SSO |
| Authentik secret key and DB password | edge secrets | Authentik |
| Plex claim token | Was used once during the move (`PLEX_CLAIM` in gpu secrets); the server is claimed | |
| Emby Premiere key | Inside Emby | |
| App logins Goobs set (Sonarr, Radarr, Prowlarr, SABnzbd, Nextcloud, n8n owner, Kuma, Dockge, Emby admin, Immich admin, AnythingLLM) | Goobs's password manager | |
| healthchecks.io ping URLs | To be placed in `ansible/inventory/hosts.yml` (`heartbeat_url`); they are secrets in practice | heartbeat cron |

## How access works for operations

- **Remote Control** sessions run on Goobs's MacBook Pro in `~/HomeLab`, which is on the LAN and the
  tailnet. The Mac's Claude Code settings allow `ssh goobs@192.168.1.51` and `ssh goobs@192.168.1.52`
  without prompting; other hosts prompt. The Mac enforces that NAS restarts need Goobs's OK in their
  own words, re-asked after any interruption.
- **SSH** to the VMs as `goobs` (sudo, key only; password auth and root login are disabled by role
  base). To the hosts as `root` with the same key. To TrueNAS as the user Goobs shared.
- **Web UIs** through Tailscale names behind Authentik, or by LAN IP for Proxmox and TrueNAS.
- **GitHub**: the Mac has a `gh` login for pushing; PRs are reviewed and merged by Goobs
  (`mikegutberlet`).

## Rotation notes

- Tailscale key: 90-day expiry; regenerate (Reusable on, Ephemeral off, Pre-approved on), save with
  the hidden prompt to `~/.config/homelab/ts-authkey`, update edge secrets, redeploy edge, restart
  tsdproxy.
- Gemini key: update `~/.config/homelab/ai-apikey` and the ai secrets, redeploy ai.
- Anthropic key (sysadmin chat agent): replace `~/.config/homelab/anthropic_api_key`, run `python3 docs/sysadmin_deploy.py`.
- Gmail app password: revoke in the Google account, re-enter in n8n and Kuma.
- age key: if it is ever rotated, every `secrets.sops.env` must be re-encrypted (`sops updatekeys`)
  and the new private key copied to each VM by the `stacks` role.
- Proxmox/TrueNAS root and admin passwords: change in each UI and update the creds document; the
  automation does not use them.
