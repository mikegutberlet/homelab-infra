# Troubleshooting

Known failure modes from the build, with the fix that worked.

| Symptom | Cause | Fix |
| --- | --- | --- |
| Authentik page loads but shows "The request failed and the interceptors did not return an alternative response" | Traefik did not trust TSDProxy's `X-Forwarded-Proto`, so Authentik built `http://` API URLs and the browser blocked mixed content | `--entrypoints.web.forwardedHeaders.trustedIPs=172.16.0.0/12` on Traefik (in place) |
| Sonarr/Radarr form login says the credentials are invalid, right after a VM restart (2026-10-22) | The restart only logged the browser out; the password in the manager was from the old VM. The hash on the server had not changed | Try the login set at migration time; otherwise `playbooks/arr-auth.yml` ([Locked out](locked-out.md)) |
| Authentik initial-setup says "Flow does not apply to current user" | `akadmin` already had a password | `docker compose exec authentik-server ak create_recovery_key 1 akadmin`, open the link, set a password, delete the link file |
| An app name does not resolve or has no certificate | TSDProxy could not register it: single-use or expired Tailscale key, or `apps.yaml` changed without a restart | New reusable key in edge secrets; `docker compose restart tsdproxy`; first visit takes up to a minute |
| Route change has no effect | Compose does not recreate containers for bind-mounted file changes | Restart traefik / tsdproxy / homepage / recyclarr after editing their config |
| A config change never appears in the container | Single-file bind mount kept the old inode | Mount the folder, not the file (as recyclarr now does) |
| "Bad Gateway" on Huginn or n8n | Huginn: its DB user cannot `CREATE DATABASE` (now `DO_NOT_CREATE_DATABASE=true` and init.sh creates it). n8n: running as a non-image uid broke `/home/node/.cache` (now runs as uid 1000) | Both fixed in the compose; check `docker logs` for the next variant |
| Uptime Kuma warns it is unsupported | Pinned to 1.x | Now `uptime-kuma:2`; v2 migrates the v1 database on first start |
| Kuma script stops with `NOT NULL constraint failed: monitor.conditions` | uptime-kuma-api 1.x against Kuma 2 | The script passes `conditions="[]"`; re-run, it is idempotent |
| n8n SMTP test: `wrong version number` | Implicit TLS against port 587 | Use port 465 with SSL on (Gmail) |
| Media VM apps OOM-killed or sluggish | 6 GB VM with a 3 GB balloon floor; apps alone use about 3 GB, no swap | Balloon floor raised to 5 GB (`balloon_mb = 5120`); the old Servarr VM powered off to free host RAM |
| Sonarr "Couldn't add release to download queue", thousands of failed grabs | GingaDADDY capped the account after a retry storm caused by months of failed imports | Fix imports (one uid), disable the indexer with a 100/day grab limit, re-enable after the cap resets |
| Downloads complete but never import | Download client and Arr apps running as different users | Everything runs as 3000 now; on the old VM `storage` was added to the `ubuntu` group |
| Jellyseerr will not start on migrated Overseerr data | Missing `jellyfin` section / `mediaServerType` | Add `"jellyfin": {}` and `main.mediaServerType = 1` to `settings.json` |
| Recyclarr changed scores on profiles it should not touch | Wrong profile/instance names | Restore both apps from their latest backup; name profiles exactly (note Sonarr's trailing space) |
| Plex shows an empty or shrinking library after a reboot | Containers started before the NAS was mounted (empty autofs root) and "Empty trash automatically" was on | `wait-nas.service` (role nfs) and the Plex setting off; if it happens, stop scans, remount, restart the stack, and do not empty trash |
| Scrutiny crashes once at boot | InfluxDB not ready | It restarts by itself (autoheal / restart policy); planned `depends_on` with healthcheck |
| Host does not come back after `reboot` | NUC6 warm-reboot hang | Power button; always power off instead ([Reboot a host](reboot-a-host.md)) |
| Both hosts up after a power cut but no VM starts | `wait_for_all` needs both nodes seen once | Make sure the other NUC is on; check `pvecm status` |
| RicksShip screen is black after boot | iGPU is passed to VM 203 | Normal; use ping and SSH |
| New VM has no IP / panics on first boot | Wrong Debian image (`genericcloud`) or no serial port | Use `debian-12-generic`, keep the `serial_device` block (already in `vms.tf`) |
| `tofu apply` cannot set hostpci | Token lacks raw-device rights | `qm set 203 --hostpci0 0000:00:02.0,pcie=1` as root; tofu ignores hostpci |
| Old Plex VM cannot `apt` | Its resolver is an IPv6 router address that does not answer | Copy .debs in; the VM is retired anyway |
| Homebrew on the Mac is slow or locked | Intel Mac builds Ansible from source; overlapping `brew install` runs lock each other | Wait; use the venv at `~/.local/share/homelab-ansible` |
| The Mac session refuses a command | Claude Code permission rules on the Mac (allow-list for ssh to .51 and .52 only) or the NAS-restart safety rule | Add a rule to `~/.claude/settings.json` (no `claude` CLI on PATH) or give the OK in your own words |
| A long job's log vanished after a reboot | It was in `/tmp` on a VM | Keep logs on disk under `/home/goobs` or on the Mac |

## Useful commands

```sh
# What is running where
for h in 50 51 52 53 54; do echo == 192.168.1.$h; ssh goobs@192.168.1.$h 'sudo docker ps --format "{{.Names}}\t{{.Status}}"'; done

# NAS mount state on a VM
ssh goobs@192.168.1.51 'findmnt /mnt/nfs; ls /mnt/nfs | head; systemctl is-active wait-nas'

# Firewall as applied
ssh goobs@192.168.1.51 'sudo iptables -S DOCKER-USER'

# Traefik routes it actually loaded
ssh goobs@192.168.1.50 'sudo docker logs --tail 100 edge-traefik-1 2>&1 | grep -i -e error -e router'

# gluetun exit IP and forwarded port
ssh goobs@192.168.1.51 'sudo docker exec media-gluetun-1 wget -qO- https://ipinfo.io/ip; sudo docker logs --tail 20 media-gluetun-1 | grep -i port'

# Host health
ssh root@192.168.1.137 'pvecm status | grep -e Quorate -e Expected; free -h; qm list'
```

Container names follow Compose's `<stack>-<service>-1` pattern unless a `container_name` is set
(none are).
