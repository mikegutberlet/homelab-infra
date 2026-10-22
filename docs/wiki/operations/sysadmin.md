# The junior sysadmin (n8n "Homelab sysadmin")

The n8n workflow that watches the lab every 15 minutes, emails what is critical, lists the rest
in the 08:00 digest, and runs a short list of safe fixes. It replaced the plain "Homelab digest"
workflow on 2026-10-22; the five legacy webhook paths (`/webhook/homelab/kuma|diun|scrutiny|backup|other`)
moved over unchanged.

Sources: `stacks/apps/n8n/sysadmin/*.js` (rules, one file per Code node) built into
`stacks/apps/n8n/homelab-sysadmin.workflow.json` by `docs/build_sysadmin_workflow.py`.
Per-VM side: Ansible role `ops` (`homelab-agent`, `homelab-ops`).

## How it fits together

| Part | Where | Does |
| --- | --- | --- |
| `homelab-agent` | every VM, timer `*:0/15` | POSTs the VM's state to `/webhook/homelab/report`: system, disks, NFS and backup mounts, last backup, containers (state, health, restart count), expected services missing, reclaimable Docker space, Sonarr/Radarr health and stuck queue, Prowlarr backoffs, SABnzbd, qBittorrent via gluetun, Tdarr, and on edge Tailscale plus a TLS check of every ts.net name |
| Evaluate report | n8n | Turns one report into issues (critical / warning / info) with a stable key; keeps the previous report, restart samples and an hourly trend point per VM |
| Every 15 min + Evaluate infra | n8n | Reads Proxmox (`cluster/resources`, `cluster/status`, node status for swap), TrueNAS (`pool`, `pool/dataset/id/DeepData`, `alert/list`) and Scrutiny (`/api/summary`), and flags VMs that have not reported for 40 min |
| Decide | n8n | Updates the issue store (first seen, resolved), emails new criticals (then every 6 h while they persist), and plans fixes within the limits |
| ops door | every VM | `ssh ops@<vm> <verb> <target>`: the key is pinned to `homelab-ops`, which only knows the verbs below and logs every call under `homelab-ops` in the journal |
| Build digest | n8n, 08:00 Pacific | Needs you, Fixed in the last 24 h, Would have fixed (report-only mode), Resolved since yesterday, For information, Trends, the legacy webhook sections, Quiet |

## Fix verbs and limits

| Verb | When | What it does |
| --- | --- | --- |
| `restart <service>` | unhealthy in two reports (30 min); expected service not running; gluetun with no VPN exit or no forwarded port (qbittorrent follows 60 s later) | `docker compose restart` if running, else `up -d --no-deps`; only services on `/etc/homelab-ops.allow` |
| `stop <service>` | 10+ restarts in an hour, after two restart attempts in 24 h | `docker stop`; the email carries the undo line |
| `arr-retry <app> <id>` | import blocked/pending or failed for 24 h with a "no files found / missing" message | removes the queue item (download kept on disk) and searches again |
| `arr-blocklist <app> <id>` | 0 bytes after 48 h; **off** until retry has run clean for a week | blocklists the release and searches again |
| `prowlarr-test <id>` | indexer disabled by backoff and the backoff has expired | re-tests, which re-enables it |
| `docker-prune` | over 20 GB reclaimable | dangling images and build cache only |
| `remount-nas` | `/mnt/nfs` empty or unmounted while TrueNAS answers on 2049 | lazy unmount, re-trigger the automount, recreate the stacks |

Limits: one fix per target per hour, three per day; `ops_never` services (n8n, Postgres, Redis,
MariaDB, autoheal, tailscale) are never touched. Never: reboot or power off anything, delete
under `/mnt/nfs`, pull images, change app settings, touch the Tdarr queue.

Switches live at the top of `stacks/apps/n8n/sysadmin/settings.js`: `FIXES_ENABLED` (false =
report-only, the digest shows "would have fixed") and `BLOCKLIST_ENABLED`. Change, rebuild, re-import.

## Credentials in n8n (never in the repo)

| Name | Type | Used by |
| --- | --- | --- |
| Gmail SMTP | SMTP | both email nodes (exists since the first digest) |
| ops edge / media / gpu / apps / ai | SSH private key, user `ops`, host `192.168.1.5x` | the five `ops <vm>` nodes |
| Proxmox API token | Header Auth, `Authorization: PVEAPIToken=homepage@pve!homepage=<secret>` | PVE nodes |
| TrueNAS API key | Header Auth, `Authorization: Bearer <key>` | NAS nodes |

The ops key pair is generated on the Mac (`~/.config/homelab/ops_ed25519`); the public half is
`ops_pubkey` in `ansible/inventory/group_vars/docker_vms.yml`.

## Operate it

- Import or update: n8n > Workflows > Import from file > `homelab-sysadmin.workflow.json`, map the
  credentials, activate. Deactivate the old "Homelab digest" (same webhook paths would clash).
- Test a report by hand: `sudo homelab-agent --print` on a VM shows what it sends;
  `sudo homelab-agent` sends it. Then n8n > Executions.
- Test the door: `ssh -i ~/.config/homelab/ops_ed25519 ops@192.168.1.51 status` from the Mac.
- Test the digest: open the workflow, select "Every day 08:00", Execute step.
- Reset the memory (issues, trends, fix log): the workflow's static data is cleared by
  deactivating and re-activating it after an import; or add a Code node that sets
  `$getWorkflowStaticData('global')` fields to `{}`.
- Journal on a VM: `journalctl -t homelab-ops` (every fix call), `journalctl -u homelab-agent`.
