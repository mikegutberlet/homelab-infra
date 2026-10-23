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
| Sysadmin agent (n8n AI Agent, Gemini Flash on the same key as Open WebUI) | Answers from tools, never from memory alone: `lab_overview` (open issues, when each VM reported, pool space), `vm_report` (one VM's latest 15-minute report), `recent_fixes`, `ops_lookup` (live `status` or `logs <service>` through the ops door), `propose_fix` |
| Homelab ops door (second, small workflow) | Host switch plus one SSH node per VM; used by the agent's lookups and by confirmed fixes, so the SSH nodes exist once |
| Chat memory | Per browser tab ("New chat" starts a fresh one), last 10 turns |
| Confirm / Cancel | A proposed fix is parked for 15 minutes with the chat session; the Confirm button posts it back and plain code re-checks it (same verbs, never-list and per-target counters as the automatic fixes, plus `CHAT_FIX_PER_HOUR` / `CHAT_FIX_PER_DAY`), logs it in the fix log (the digest's "Fixed" section) and runs it through the ops door. The model never runs anything |

Switches in `settings.js`: `CHAT_FIXES_ENABLED` (false = the chat proposes fixes but Confirm does
nothing; independent of `FIXES_ENABLED`, which gates the automatic fixes), `CHAT_FIX_PER_HOUR`,
`CHAT_FIX_PER_DAY`, `CHAT_CONFIRM_MIN`. The model name is `CHAT_MODEL` in `docs/build_sysadmin_workflow.py`.

Prompt injection: container logs and app names pass through the model, so a log line could try to
talk it into something. It can only read and propose; nothing runs without your Confirm, and the
ops door only knows its fixed verbs. `journalctl -t homelab-ops` on a VM shows every call, chat or automatic.

## Telegram bot

The same chat in Telegram, for when you are away from a browser, plus critical alerts on your phone.
A small Python container on the apps VM (compose service `sysadmin-telegram`,
`stacks/apps/sysadmin-telegram/bot.py`, standard library only) **long-polls** the Telegram Bot API
(`getUpdates`). It only makes outgoing HTTPS calls to `api.telegram.org`, so nothing in the lab is
exposed to the internet: no public webhook, no Tailscale Funnel.

It is a thin front end. Every question goes to the same chat webhook as the chat page
(`http://n8n:5678/webhook/homelab/chat`, with the chat token and `X-Chat-Via: telegram`), so the
agent, its tools, the Confirm checks and the limits are the same, and the bot decides nothing. Fixes
confirmed from Telegram show up in the fix log as `chat (telegram): <reason>`.

**Allow-list.** The bot answers only the chat ids in `TELEGRAM_ALLOWED_CHAT_IDS` (your private chat
with the bot). Anyone can find a bot by its username and message it. Updates from any other chat
are ignored, and the log shows `ignored update from chat id X` without their content. A button
press from anyone else gets "Not allowed". An empty list means the bot answers nobody.

| Command | Does |
| --- | --- |
| `/start`, `/help` | Short help |
| `/new` | New conversation: fresh agent memory (session `tg-<chat id>-<k>`, `k` goes up by one) |
| `/status` | Asks "What needs my attention right now?" |
| `/fixes` | Asks "What fixes ran in the last 24 hours?" |
| any other text | The question. "typing..." shows while the agent works (up to about 5 minutes) |

Non-text messages (photos, voice and so on) get a short "text only" reply, and an unknown `/command`
gets a pointer to `/help`. Text that starts with a path (`/var/log/syslog is huge`) is a question,
not a command. Questions are cut at 2,000 characters, the webhook's limit, and the
answer says so. One question runs at a time per chat. A second one sent while the first is still
running gets "Still working on your last question" and is not queued, so send it again after the
answer. Button presses are handled straight away, even while a question is still running.
The chat page and Telegram keep separate memories (different session ids). If the bot restarts
while a question or a Confirm is still running (a stacks deploy, or a confirmed restart of the bot
itself), that answer is lost; after the restart the bot says so ("I was restarted before I could
answer"). Ask again, and use `/fixes` to see whether a confirmed fix ran.

**Confirm buttons.** A proposed fix arrives as its own message: "Proposed fix: `<cmd>` on `<host>`",
the reason, "Expires in 15 min" (`CHAT_CONFIRM_MIN`), and Confirm and Cancel buttons. When you
press one, the buttons disappear and the message gets "Confirmed at HH:MM" (Pacific) or "Cancelled".
The result ("Done: ...", "That did not work: ...", "Cancelled: ...", "Proposal n is gone ...") comes
as a new message. A second press of the same message only gets "Already handled". The bot keeps
each proposal's session in `state.json`, so Confirm still works after `/new`. A newer proposal in
the same conversation removes the older message's buttons ("Replaced by proposal n"). A press on a
message the bot no longer knows (over a day old, after a lost `state.json`, or a proposal number
reused after a workflow re-import) only removes its buttons ("This proposal is gone; ask again")
and never reaches n8n.
Everything else works the same as on the page: the workflow parks one proposal per session for
`CHAT_CONFIRM_MIN`, a newer proposal replaces it, and the code re-checks the verb, the never-list
and the limits when you press Confirm. While `CHAT_FIXES_ENABLED` is false, the message says chat
fixes are switched off and offers only a Dismiss button.

**Alerts.** Two switches in `settings.js` (rebuild and re-import after a change, e.g. `python3 docs/sysadmin_deploy.py`):

| Switch | Default | Does |
| --- | --- | --- |
| `TELEGRAM_ALERTS` | `true` | Every critical email also goes to Telegram: criticals from Decide (at once, then every 6 h while they persist) and failed fixes from Record fix result. Path: "Telegram alert?" then "Telegram push" |
| `TELEGRAM_DIGEST` | `false` | The 08:00 digest also goes to Telegram: "Telegram digest?" then "Telegram digest push" |

The push nodes POST `{subject, text}` to `http://sysadmin-telegram:3004/notify` with the
"Sysadmin chat token" credential. The bot sends "subject, blank line, text" as plain text to every
allowed chat, in messages of up to about 3,500 characters. Texts are cut at 8,000 characters
(alerts) or 12,000 (digest), and the email always has the full text. The Telegram nodes sit above the email nodes on the canvas, so n8n runs them first. If Gmail
fails, the alert has already gone to Telegram. If Telegram fails (bot down, Telegram unreachable),
only the push node shows the error ("continue on error") and the email still goes out.

**What Telegram sees.** Your questions, the agent's answers (VM state, container names, any log
lines it quotes), alerts and digests pass through Telegram's servers. Bot chats are not end-to-end
encrypted. No credentials do: the bot token stays on the apps VM and the Mac, the chat token on the
apps VM. The bot logs chat ids, update types and lengths, never message text or the token.

| File or port | What |
| --- | --- |
| `stacks/apps/sysadmin-telegram/bot.py` | The bot |
| `/opt/appdata/sysadmin-telegram/telegram.env` (apps VM, root, 600) | `TELEGRAM_BOT_TOKEN`, `TELEGRAM_ALLOWED_CHAT_IDS`; written by `docs/telegram_setup.py` |
| `/opt/appdata/sysadmin-chat/token.env` (apps VM) | `CHAT_TOKEN`, shared with the chat page: the bot sends it to the webhook and checks it on `/notify`. Read when the container is created, so the bot must be recreated after the token changes (`docs/sysadmin_deploy.py` recreates it along with the chat page) |
| `/opt/appdata/sysadmin-telegram/data/state.json` | The `getUpdates` offset (a restart neither replays nor loses messages), the bot's id (state left by another bot's token is reset), the per-chat session counters, the open proposals (kept a day) and marks for questions or Confirms in flight; folder owned by the storage user (3000:3000) |
| `~/.config/homelab/telegram-bot-token` (Mac, mode 600) | The bot token, read by `docs/telegram_setup.py` |
| `192.168.1.53:3004` | `GET /healthz` (Kuma "Sysadmin Telegram bot") and `POST /notify` (n8n, needs `X-Chat-Token`). lanfw: the homelab VMs reach it, and so do tailnet devices through the edge VM's subnet route (they arrive as 192.168.1.50); `/notify` is guarded only by the chat token |

### Set up or change the bot

1. In Telegram, open **@BotFather**, send `/newbot`, and pick a name (e.g. "Homelab sysadmin") and a
   username ending in `bot`. Copy the token it gives you.
2. On the Mac, with the token on the clipboard (this keeps it out of shell history):
   `(umask 077; mkdir -p ~/.config/homelab && pbpaste > ~/.config/homelab/telegram-bot-token)`
3. Keep Telegram at hand: in step 6 the setup script prints a one-time code for you to send to the
   new bot. Anyone can find a bot by its username and message it, and a display name can be
   copied, so the code is how the script tells your chat from a stranger's.
4. Deploy the compose service. From `ansible/`:
   `~/.local/share/homelab-ansible/bin/ansible-playbook -i inventory/hosts.yml playbooks/site.yml --tags stacks --limit apps`.
   Until step 6 the bot idles and `/healthz` answers 503 "not configured".
5. Rebuild and re-import the workflow, so the push nodes exist and have their credential:
   `python3 docs/sysadmin_deploy.py`.
6. From `~/HomeLab/homelab-infra`, run `python3 docs/telegram_setup.py` (the Mac's system Python
   is enough). The script:
   - checks the token file and asks Telegram (`getMe`)
   - stops the bot if it is running (not with `--chat-id`)
   - prints a one-time code: send `/start <code>` to the bot from your account (or open the
     `t.me/<bot>?start=<code>` link it prints), then press Enter. Only a private chat that sent the
     code is taken; other chats are counted and ignored. `--chat-id <id>` skips the code
     (Telegram's `getChat` shows whose chat it is).
   - writes `telegram.env` on the apps VM over ssh (the token goes over stdin, never on a command line)
   - creates the data folder, recreates the bot, waits for `/healthz` (up to 2 minutes)
   - sends "Homelab sysadmin bot is connected" to your chat

   `--dry-run` runs every check but writes nothing on the VM and sends nothing. If the apps VM's
   compose file does not have the service yet, the script stops and prints the step 4 command. If
   the chat token is missing, it stops and asks for step 5 first.
7. Add the Kuma monitor: `~/.local/share/homelab-ansible/bin/python docs/kuma_setup.py` (it skips
   existing monitors).

To switch to another chat or replace the token later, re-run `docs/telegram_setup.py`. It writes
the one chat you pick. To replace the token, first use BotFather's `/revoke` and save the new token
over the old file. If you make a new bot instead (`/newbot`), the bot sees the new bot id at start
and resets the old bot's offset and open proposals in `state.json`. To allow more than one chat,
edit `telegram.env` by hand (`sudo`; the ids are comma-separated in `TELEGRAM_ALLOWED_CHAT_IDS`),
then recreate the container:
`sudo docker compose -f /opt/stacks/apps/compose.yaml up -d --force-recreate sysadmin-telegram`.

After a change to `stacks/apps/sysadmin-telegram/bot.py`, run the stacks play (step 4), then
restart the bot on the apps VM: `sudo docker compose -f /opt/stacks/apps/compose.yaml restart sysadmin-telegram`.
The play copies the new file, but the code is bind-mounted, so `docker compose up` sees no change
and leaves the old code running.

Test a push without waiting for an alert. On the apps VM (the token goes over stdin, so it never
shows in `ps`):
`sudo sh -c '. /opt/appdata/sysadmin-chat/token.env; printf "X-Chat-Token: %s\n" "$CHAT_TOKEN" | curl -sS -H @- -H "Content-Type: application/json" -d "{\"subject\":\"Test\",\"text\":\"Push from the apps VM\"}" http://127.0.0.1:3004/notify'`
should answer `{"sent": 1, "failed": 0}`. A 502 with `"failed"` above 0 means Telegram refused the
message (see the log), and a 401 means the token did not match.

### Troubleshooting

- **Health**: `curl -s http://127.0.0.1:3004/healthz` on the apps VM. It answers 200 "ok ..." while
  the last successful poll is under 3 minutes old. Otherwise it answers 503 with the reason:
  - "not configured: TELEGRAM_BOT_TOKEN is not set": run `docs/telegram_setup.py`.
  - "not configured: CHAT_TOKEN is not set": `token.env` was missing when the container was created.
    Run `python3 docs/sysadmin_deploy.py`, then recreate the bot (command above).
  - "cannot write /data/state.json": see "State not saved" below.
  - "no successful getUpdates yet": normal for up to a minute after a start (the first long poll).
    If it lasts, or carries an error such as `401 Unauthorized` (token revoked), read the log.
  - "last successful getUpdates N min ago": Telegram is unreachable, the token was revoked, or
    there is a conflict (below).
  Logs: `sudo docker logs --tail 100 apps-sysadmin-telegram-1`.
- **409 Conflict** in the log: something else is reading this bot's updates. That is a second copy
  of the bot (an old container, or a test run on the Mac), or a webhook set on the bot. Only one
  poller per token works. The bot clears a webhook once by itself (`deleteWebhook`) and backs off,
  so stop the other poller.
- **"ignored update from chat id X"**: a chat that is not on the allow-list wrote to the bot. If X
  is yours (for example, a new Telegram account), re-run `docs/telegram_setup.py --chat-id X`.
  Otherwise ignore it: that chat gets no answer.
- **No answer or "The agent failed"**: same as the chat page. Look at n8n > Executions for the chat
  webhook. Gemini is the usual cause.
- **"The chat webhook refused my token"** (and `/notify` answering 401): the bot's `CHAT_TOKEN` and
  the n8n credential differ, for example after `token.env` was replaced by hand.
  `sysadmin_deploy.py` recreates the bot after it writes the token; otherwise recreate the bot
  (command above) so it re-reads `token.env`.
  "I could not reach n8n" means n8n is down or restarting.
- **"not configured" right after using Dockge**: never press Start or Update on the apps stack in
  Dockge. Its `docker compose` runs inside the Dockge container, which cannot see
  the env files under `/opt/appdata`. Both env files are optional, so it recreates `sysadmin-telegram` (and
  `sysadmin-chat`) without their tokens. Restart is safe. To recover, run on the apps VM:
  `sudo docker compose -f /opt/stacks/apps/compose.yaml up -d --force-recreate sysadmin-chat sysadmin-telegram`.
- **State not saved** (log `cannot write /data/state.json: PermissionError`, the same in
  `/healthz`): Docker created the data folder as root before the setup script ran. Fix it with
  `sudo chown 3000:3000 /opt/appdata/sysadmin-telegram/data`, then
  `sudo docker compose -f /opt/stacks/apps/compose.yaml restart sysadmin-telegram`, or re-run the
  setup script.

## Credentials in n8n (never in the repo)

| Name | Type | Used by |
| --- | --- | --- |
| Gmail SMTP | SMTP | both email nodes (exists since the first digest) |
| Gemini API key | Google Gemini (PaLM) API, the ai stack's `LLM_API_KEY` | the chat agent's model |
| Sysadmin chat token | Header Auth `X-Chat-Token`, made by the deploy script and kept in `/opt/appdata/sysadmin-chat/token.env` on the apps VM | the chat webhook (chat page and Telegram bot), "Telegram push" and "Telegram digest push" (the bot's `/notify` checks it) |
| ops edge / media / gpu / apps / ai | SSH private key, user `ops`, host `192.168.1.5x` | the five `ops <vm>` nodes |
| Proxmox API token | Header Auth, `Authorization: PVEAPIToken=homepage@pve!homepage=<secret>` | PVE nodes |
| TrueNAS API key | Header Auth, `Authorization: Bearer <key>` | NAS nodes |
| Telegram bot token | Not in n8n: the Bot API token from @BotFather, kept in `/opt/appdata/sysadmin-telegram/telegram.env` on the apps VM and `~/.config/homelab/telegram-bot-token` on the Mac (mode 600) | the `sysadmin-telegram` container only ([Telegram bot](#telegram-bot)) |

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
