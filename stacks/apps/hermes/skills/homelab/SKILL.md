---
name: homelab
description: Goobs's home lab (three Proxmox NUCs, six Docker VMs, TrueNAS). Use for any question or change about the lab, its apps, or the homelab-infra repository.
---

# Working in Goobs's home lab

You run as a container on the apps VM (192.168.1.53). You can read the lab and change the
repository; you cannot deploy. Goobs deploys from their Mac with Ansible after merging a PR.

## Rules (from the repo's CLAUDE.md)

- State assumptions. If a request can be read two ways, say which reading you took.
- Minimum change that solves the problem. No speculative features, no refactors of adjacent code.
- Match the existing style of the file you touch. Every changed line traces to the request.
- Verify before claiming done: show the command and its output.
- Admin UIs are tailnet-only: `https://<app>.tail9af84.ts.net` behind Authentik. Never give a LAN
  `ip:port` URL; only Plex (32400), Emby (8096) and Requests (5055) are open on the LAN.
- Never print a secret. Secrets live encrypted in `stacks/<vm>/secrets.sops.env`; you cannot decrypt them.
- Never propose deleting or wiping TrueNAS data, pools or snapshots, or "Upgrade Pool".
- NUCs hang on software reboot; reboots and power changes are Goobs's, in person.

## The repository

`~/homelab-infra` (clone it if missing: `git clone https://github.com/mikegutberlet/homelab-infra ~/homelab-infra`;
the token is in git's credential store). Read `docs/wiki/` first: `03-architecture.md`,
`04-networking-and-access.md`, `services/<vm>.md`, `operations/deploy-changes.md`.

- Compose per VM: `stacks/<vm>/compose.yaml` (deployed to `/opt/stacks/<vm>/` by Ansible role `stacks`).
- Hosts and per-VM settings: `ansible/inventory/hosts.yml`; roles under `ansible/roles/`.
- VMs: edge .50 (Traefik, Authentik, Tailscale), media .51 (Sonarr, Radarr, Prowlarr, SABnzbd,
  qBittorrent, Seerr), gpu .52 (Plex, Emby, ErsatzTV, Immich, Tdarr), apps .53 (n8n sysadmin,
  Nextcloud, Paperless, Grafana, Prometheus, Homepage, you), ai .54 (AnythingLLM, Open WebUI),
  transcode .55 (Tdarr node with the iGPU). Proxmox hosts: RicksGarage .137, RicksShip .37,
  RicksPortalGun .38. TrueNAS VM 192.168.1.209.

## Changing something

1. `cd ~/homelab-infra && git fetch origin && git checkout -B hermes/<topic> origin/main`
2. Edit, then check: `python3 -c "import yaml,sys; yaml.safe_load(open(sys.argv[1]))" <file>` for YAML;
   `ansible-playbook --syntax-check` is not available here, so keep playbook edits small and say so.
3. `git commit` and `git push -u origin hermes/<topic>`
4. Open a draft PR with the GitHub API (no `gh` here):
   `curl -sS -H "Authorization: Bearer $GITHUB_TOKEN" -H "Accept: application/vnd.github+json" \
     https://api.github.com/repos/mikegutberlet/homelab-infra/pulls \
     -d '{"title":"...","head":"hermes/<topic>","base":"main","draft":true,"body":"..."}'`
   Report the PR URL and what Goobs must run to deploy, usually
   `cd ~/HomeLab/homelab-infra/ansible && ansible-playbook playbooks/site.yml --tags stacks --limit <vm>`.

## Looking at the live lab (read-only)

`ssh <vm> '<command>'` where `<vm>` is edge, media, gpu, apps, ai or transcode. The door accepts
one command, optionally piped into grep/head/tail/wc/sort/uniq/cut, from a read-only list:
`docker ps/logs/inspect/stats/images/info/top/port/diff`, `docker system df`, `journalctl`,
`systemctl status/list-*/show/cat`, `df`, `free`, `uptime`, `ss`, `ip`, `cat/head/tail/grep/find/ls`
on allowed paths (compose files, `/etc`, `/var/log`, `/proc`). Secrets are masked before they leave
the VM. Anything else is refused; say so and read the repo instead of guessing.

Examples: `ssh media 'docker ps --format "{{.Names}} {{.Status}}"'`, `ssh gpu 'docker logs --tail 100 plex'`,
`ssh apps 'df -h /'`.

## Not yours

Deploying (Ansible, `docker compose`), restarting containers, editing files on a VM, the n8n
sysadmin workflows (built by `docs/build_sysadmin_workflow.py`, never hand-edited), TrueNAS, and
anything on the Proxmox hosts. Ask Goobs for those, with the exact command.
