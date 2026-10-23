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
| `homelab-agent` | every VM, timer `*:0/15` | POSTs the VM's state to `/webhook/homelab/report`: system, disks, NFS and backup mounts, last backup, containers (state, health, restart count), expected services missing, reclaimable Docker space, Sonarr/Radarr health and stuck queue, Prowlarr backoffs, SABnzbd, qBittorrent via gluetun, Tdarr, login and API-key fingerprints for Sonarr/Radarr/Prowlarr/SABnzbd and every stored copy of their keys (agent v2, 2026-10-22; no secret leaves the VM), and on edge Tailscale plus a TLS check of every ts.net name |
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

## Chat with the sysadmin (sysadmin.tail9af84.ts.net)

A chat page (Homepage > Sysadmin chat; Tailscale plus Authentik like every admin page) where you
ask the sysadmin questions and ask it to fix things. Sources: `stacks/apps/sysadmin-chat/` (the
page and its nginx, which forwards `/api/chat` to the workflow's chat webhook and adds the webhook
token), `stacks/apps/n8n/sysadmin/chat_*.js`, `tool_*.js`, `opsdoor_*.js`.

| Part | Does |
| --- | --- |
| Sysadmin agent (n8n AI Agent, Claude Haiku 5.5, falling back to Gemini Flash on the same key as Open WebUI) | Answers from tools, never from memory alone: `lab_overview` (open issues, when each VM reported, pool space), `vm_report` (one VM's latest 15-minute report), `recent_fixes`, `ops_lookup` (live `status` or `logs <service>` through the ops door), `propose_fix` |
| Homelab ops door (second, small workflow) | Host switch plus one SSH node per VM; used by the agent's lookups and by confirmed fixes, so the SSH nodes exist once |
| Chat memory | Per browser tab ("New chat" starts a fresh one), last 10 turns |
| Confirm / Cancel | A proposed fix is parked for 15 minutes with the chat session; the Confirm button posts it back and plain code re-checks it (same verbs, never-list and per-target counters as the automatic fixes, plus `CHAT_FIX_PER_HOUR` / `CHAT_FIX_PER_DAY`), logs it in the fix log (the digest's "Fixed" section) and runs it through the ops door. The model never runs anything |

Switches in `settings.js`: `CHAT_FIXES_ENABLED` (false = the chat proposes fixes but Confirm does
nothing; independent of `FIXES_ENABLED`, which gates the automatic fixes), `CHAT_FIX_PER_HOUR`,
`CHAT_FIX_PER_DAY`, `CHAT_CONFIRM_MIN`. The model name is `CHAT_MODEL` in `docs/build_sysadmin_workflow.py`.

Prompt injection: container logs and app names pass through the model, so a log line could try to
talk it into something. It can only read and propose; nothing runs without your Confirm, and the
ops door only knows its fixed verbs. `journalctl -t homelab-ops` on a VM shows every call, chat or automatic.

## Credentials in n8n (never in the repo)

| Name | Type | Used by |
| --- | --- | --- |
| Gmail SMTP | SMTP | both email nodes (exists since the first digest) |
| Anthropic API key | Anthropic, read by the deploy script from the Mac's `~/.config/homelab/anthropic_api_key` | the chat agent's model (Claude Haiku 5.5) |
| Gemini API key | Google Gemini (PaLM) API, the ai stack's `LLM_API_KEY` | the chat agent's fallback model (Gemini Flash) |
| Sysadmin chat token | Header Auth `X-Chat-Token`, made by the deploy script and kept in `/opt/appdata/sysadmin-chat/token.env` on the apps VM | the chat webhook |
| ops edge / media / gpu / apps / ai | SSH private key, user `ops`, host `192.168.1.5x` | the five `ops <vm>` nodes |
| Proxmox API token | Header Auth, `Authorization: PVEAPIToken=homepage@pve!homepage=<secret>` | PVE nodes |
| TrueNAS API key | Header Auth, `Authorization: Bearer <key>` | NAS nodes |

The ops key pair is generated on the Mac (`~/.config/homelab/ops_ed25519`); the public half is
`ops_pubkey` in `ansible/inventory/group_vars/docker_vms.yml`.

## Operate it

- Install or update everything in one go from the Mac: `python3 docs/sysadmin_deploy.py` (imports the
  credentials from the apps VM's `.env` and `~/.config/homelab/ops_ed25519`, imports the workflow with
  those credential ids, deactivates the old "Homelab digest", activates, restarts n8n).
- By hand instead: n8n > Workflows > Import from file > `homelab-sysadmin.workflow.json`, map the
  credentials, activate. Deactivate the old "Homelab digest" (same webhook paths would clash).
- Test a report by hand: `sudo homelab-agent --print` on a VM shows what it sends;
  `sudo homelab-agent` sends it. Then n8n > Executions.
- Test the door: `ssh -i ~/.config/homelab/ops_ed25519 ops@192.168.1.51 status` from the Mac.
- Test the digest: open the workflow, select "Every day 08:00", Execute step.
- Reset the memory (issues, trends, fix log): the workflow's static data is cleared by
  deactivating and re-activating it after an import; or add a Code node that sets
  `$getWorkflowStaticData('global')` fields to `{}`.
- Journal on a VM: `journalctl -t homelab-ops` (every fix call), `journalctl -u homelab-agent`.
