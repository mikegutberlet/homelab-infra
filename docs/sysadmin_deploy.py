#!/usr/bin/env python3
"""Install the "Homelab sysadmin" workflow and its credentials into n8n on the apps VM.

Goobs runs this on the Mac (lab SSH key; the ops key pair at ~/.config/homelab/ops_ed25519):

    python3 docs/sysadmin_deploy.py

What it does, in order:
  1. Reads the Proxmox token secret and the TrueNAS API key from the apps VM's decrypted
     /opt/stacks/apps/.env (root-only there; they came from docs/homepage_keys.py), the Gemini key
     from the ai VM's /opt/stacks/ai/.env (LLM_API_KEY, the one Open WebUI uses), and the Anthropic
     key (~/.config/homelab/anthropic_api_key) and ops private key from the Mac. Nothing is printed.
  2. Imports five SSH credentials (ops edge/media/gpu/apps/ai), four Header Auth credentials
     (Proxmox API token, TrueNAS API key, Gemini API key, Sysadmin chat token) and the "Anthropic
     API key" into n8n through its CLI, replacing same-named ones. The chat token is made here on first run and kept in
     /opt/appdata/sysadmin-chat/token.env on the apps VM, where the chat page's nginx reads it.
  3. Imports the "Homelab ops door" sub-workflow (the chat agent's SSH lookups and confirmed fixes),
     then builds the sysadmin workflow with those credential ids plus the existing "Gmail SMTP" id,
     imports it (replacing an earlier "Homelab sysadmin"), deactivates the old "Homelab digest" (same
     webhook paths), activates the new one and restarts n8n so the webhooks and timers register.
Secrets travel over SSH on stdin, into the n8n container's /tmp, and are removed right after.
"""
import json
import pathlib
import secrets
import string
import subprocess
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "docs"))
import build_sysadmin_workflow as builder  # noqa: E402

APPS = "goobs@192.168.1.53"
AI = "goobs@192.168.1.54"
TOKEN_ENV = "/opt/appdata/sysadmin-chat/token.env"
OPS_KEY = pathlib.Path.home() / ".config/homelab/ops_ed25519"
ANTHROPIC_KEY = pathlib.Path.home() / ".config/homelab/anthropic_api_key"  # the chat agent's model
HOSTS = builder.HOSTS
N8N = "sudo docker compose -f /opt/stacks/apps/compose.yaml exec -T n8n"


def ssh(cmd, stdin=None, check=True, host=APPS):
    r = subprocess.run(["ssh", "-o", "BatchMode=yes", host, cmd], input=stdin, capture_output=True, text=True)
    if check and r.returncode != 0:
        sys.exit(f"ssh failed: {cmd[:80]}...\n{r.stderr.strip()[-500:]}")
    return r.stdout


def n8n_json(cmd):
    """Run an n8n CLI command that prints JSON (the CLI adds log lines around it)."""
    out = ssh(f"{N8N} {cmd}", check=False)
    start = out.find("[")
    return json.loads(out[start:]) if start >= 0 else []


def list_workflows():
    """name -> id from `n8n list:workflow`, which prints plain 'id|name' lines (no JSON flag in n8n 2.x)."""
    out = ssh(f"{N8N} n8n list:workflow", check=False)
    found = {}
    for line in out.splitlines():
        if "|" in line and " " not in line.split("|", 1)[0]:
            wid, name = line.strip().split("|", 1)
            found[name] = wid
    return found


def set_active(wid, active):
    """n8n 2.x: publish:workflow / unpublish:workflow (update:workflow --active still works but is deprecated)."""
    verb = "publish" if active else "unpublish"
    r = subprocess.run(["ssh", "-o", "BatchMode=yes", APPS, f"{N8N} n8n {verb}:workflow --id={wid}"], capture_output=True, text=True)
    if r.returncode != 0:
        ssh(f"{N8N} n8n update:workflow --id={wid} --active={'true' if active else 'false'}", check=False)


def new_id():
    return "".join(secrets.choice(string.ascii_letters + string.digits) for _ in range(16))


if not OPS_KEY.exists():
    sys.exit(f"{OPS_KEY} not found. Create it: ssh-keygen -t ed25519 -N '' -C n8n-ops -f {OPS_KEY}")
priv = OPS_KEY.read_text()
env = ssh("sudo grep -E '^HOMEPAGE_(PROXMOX_TOKEN_SECRET|TRUENAS_KEY)=' /opt/stacks/apps/.env")
vals = dict(line.split("=", 1) for line in env.strip().splitlines() if "=" in line)
pve_secret, nas_key = vals.get("HOMEPAGE_PROXMOX_TOKEN_SECRET", ""), vals.get("HOMEPAGE_TRUENAS_KEY", "")
if not pve_secret or not nas_key:
    sys.exit("Proxmox token secret or TrueNAS key missing in the apps VM's .env; run docs/homepage_keys.py first.")
gemini_key = ssh("sudo grep -E '^LLM_API_KEY=' /opt/stacks/ai/.env", host=AI).strip().split("=", 1)[-1]
if not gemini_key:
    sys.exit("LLM_API_KEY missing in the ai VM's /opt/stacks/ai/.env (the Gemini key Open WebUI uses).")
anthropic_key = ANTHROPIC_KEY.read_text().strip() if ANTHROPIC_KEY.exists() else ""
if not anthropic_key:
    sys.exit(f"{ANTHROPIC_KEY} missing or empty. Create a key at console.anthropic.com and save it there (mode 600).")
# The chat page's webhook token: made once, kept on the apps VM, restarted into the nginx container.
chat_token = ssh(f"sudo cat {TOKEN_ENV} 2>/dev/null", check=False).strip().split("=", 1)[-1]
if not chat_token:
    chat_token = secrets.token_urlsafe(32)
    ssh(f"sudo install -d -m 750 -o root -g root {TOKEN_ENV.rsplit('/', 1)[0]} && sudo sh -c 'umask 077; cat > {TOKEN_ENV}'", stdin=f"CHAT_TOKEN={chat_token}\n")
    print(f"  new chat token written to {TOKEN_ENV}")

print("Existing credentials...")
all_creds = n8n_json("n8n export:credentials --all")
existing = {c["name"]: c["id"] for c in all_creds}
# The Gmail app-password credential: "Gmail SMTP" by name, else the only SMTP-type credential.
smtp = [c for c in all_creds if c.get("type") == "smtp"]
smtp_name = "Gmail SMTP" if "Gmail SMTP" in existing else (smtp[0]["name"] if len(smtp) == 1 else None)
if not smtp_name:
    sys.exit(f"Need exactly one SMTP credential in n8n (found {[c['name'] for c in smtp]}); create or rename it in the UI first.")
print(f"  using SMTP credential '{smtp_name}'")
ids = {}
creds = []
for h in HOSTS:
    name = f"ops {h}"
    ids[name] = existing.get(name, new_id())
    creds.append({"id": ids[name], "name": name, "type": "sshPrivateKey",
                  "data": {"host": builder.HOSTS[h], "port": 22, "username": "ops", "privateKey": priv, "passphrase": ""}})
for name, header, value in (("Proxmox API token", "Authorization", f"PVEAPIToken=homepage@pve!homepage={pve_secret}"),
                            ("TrueNAS API key", "Authorization", f"Bearer {nas_key}"),
                            ("Sysadmin chat token", "X-Chat-Token", chat_token)):
    ids[name] = existing.get(name, new_id())
    creds.append({"id": ids[name], "name": name, "type": "httpHeaderAuth", "data": {"name": header, "value": value}})
ids["Gemini API key"] = existing.get("Gemini API key", new_id())
creds.append({"id": ids["Gemini API key"], "name": "Gemini API key", "type": "googlePalmApi",
              "data": {"host": "https://generativelanguage.googleapis.com", "apiKey": gemini_key}})
ids["Anthropic API key"] = existing.get("Anthropic API key", new_id())
creds.append({"id": ids["Anthropic API key"], "name": "Anthropic API key", "type": "anthropicApi",
              "data": {"apiKey": anthropic_key, "url": "https://api.anthropic.com"}})
ids["Gmail SMTP"] = existing[smtp_name]

print("Importing credentials...")
ssh(f"{N8N} sh -c 'cat > /tmp/creds.json && n8n import:credentials --input=/tmp/creds.json; rm -f /tmp/creds.json'", stdin=json.dumps(creds))

print("Importing the ops door sub-workflow...")
workflows = list_workflows()
door = builder.build_ops_door(ids)
door["id"] = workflows.get("Homelab ops door", door["id"])
ssh(f"{N8N} sh -c 'cat > /tmp/door.json && n8n import:workflow --input=/tmp/door.json; rm -f /tmp/door.json'", stdin=json.dumps(door))
workflows = list_workflows()
door_id = workflows.get("Homelab ops door")
if not door_id:
    sys.exit("The import did not produce a 'Homelab ops door' workflow; check n8n > Workflows before re-running.")
set_active(door_id, True)  # a sub-workflow needs no trigger, but published is what Execute Workflow expects

print("Importing the workflow...")
wf = builder.build(ids, {"Gmail SMTP": smtp_name}, ops_door_id=door_id)
if "Homelab sysadmin" in workflows:
    wf["id"] = workflows["Homelab sysadmin"]
ssh(f"{N8N} sh -c 'cat > /tmp/wf.json && n8n import:workflow --input=/tmp/wf.json; rm -f /tmp/wf.json'", stdin=json.dumps(wf))
workflows = list_workflows()
if "Homelab sysadmin" not in workflows:
    sys.exit("The import did not produce a 'Homelab sysadmin' workflow; check n8n > Workflows before re-running.")
if "Homelab digest" in workflows:
    set_active(workflows["Homelab digest"], False)
    print("  old 'Homelab digest' deactivated (kept)")
set_active(workflows["Homelab sysadmin"], True)
print("Restarting n8n so the webhooks and timers register, and the chat page and Telegram bot so they pick up the token...")
ssh("sudo docker compose -f /opt/stacks/apps/compose.yaml restart n8n")
ssh("sudo docker compose -f /opt/stacks/apps/compose.yaml up -d --force-recreate sysadmin-chat", check=False)
# The bot reads CHAT_TOKEN only when its container is created; a no-op while the service is not deployed.
ssh("sudo docker compose -f /opt/stacks/apps/compose.yaml up -d --no-deps --force-recreate sysadmin-telegram", check=False)
print("Done. Check n8n > Workflows > Homelab sysadmin (active) and Executions after the next 15-minute tick;")
print("the chat page is https://sysadmin.<tailnet> (Homepage > Sysadmin chat).")
