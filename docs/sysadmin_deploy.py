#!/usr/bin/env python3
"""Install the "Homelab sysadmin" workflow and its credentials into n8n on the apps VM.

Goobs runs this on the Mac (lab SSH key; the ops key pair at ~/.config/homelab/ops_ed25519):

    python3 docs/sysadmin_deploy.py

What it does, in order:
  1. Reads the Proxmox token secret and the TrueNAS API key from the apps VM's decrypted
     /opt/stacks/apps/.env (root-only there; they came from docs/homepage_keys.py) and the ops
     private key from the Mac. Nothing is printed.
  2. Imports five SSH credentials (ops edge/media/gpu/apps/ai), two Header Auth credentials
     (Proxmox API token, TrueNAS API key) into n8n through its CLI, replacing same-named ones.
  3. Builds the workflow with those credential ids plus the existing "Gmail SMTP" id, imports it
     (replacing an earlier "Homelab sysadmin"), deactivates the old "Homelab digest" (same webhook
     paths), activates the new one and restarts n8n so the webhooks and timers register.
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
OPS_KEY = pathlib.Path.home() / ".config/homelab/ops_ed25519"
HOSTS = builder.HOSTS
N8N = "sudo docker compose -f /opt/stacks/apps/compose.yaml exec -T n8n"


def ssh(cmd, stdin=None, check=True):
    r = subprocess.run(["ssh", "-o", "BatchMode=yes", APPS, cmd], input=stdin, capture_output=True, text=True)
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
for name, value in (("Proxmox API token", f"PVEAPIToken=homepage@pve!homepage={pve_secret}"), ("TrueNAS API key", f"Bearer {nas_key}")):
    ids[name] = existing.get(name, new_id())
    creds.append({"id": ids[name], "name": name, "type": "httpHeaderAuth", "data": {"name": "Authorization", "value": value}})
ids["Gmail SMTP"] = existing[smtp_name]

print("Importing credentials...")
ssh(f"{N8N} sh -c 'cat > /tmp/creds.json && n8n import:credentials --input=/tmp/creds.json; rm -f /tmp/creds.json'", stdin=json.dumps(creds))

print("Importing the workflow...")
wf = builder.build(ids, {"Gmail SMTP": smtp_name})
workflows = list_workflows()
if "Homelab sysadmin" in workflows:
    wf["id"] = workflows["Homelab sysadmin"]
ssh(f"{N8N} sh -c 'cat > /tmp/wf.json && n8n import:workflow --input=/tmp/wf.json; rm -f /tmp/wf.json'", stdin=json.dumps(wf))
workflows = list_workflows()
if "Homelab sysadmin" not in workflows:
    sys.exit("The import did not produce a 'Homelab sysadmin' workflow; check n8n > Workflows before re-running.")
if "Homelab digest" in workflows:
    ssh(f"{N8N} n8n update:workflow --id={workflows['Homelab digest']} --active=false", check=False)
    print("  old 'Homelab digest' deactivated (kept)")
ssh(f"{N8N} n8n update:workflow --id={workflows['Homelab sysadmin']} --active=true", check=False)
print("Restarting n8n so the webhooks and timers register...")
ssh("sudo docker compose -f /opt/stacks/apps/compose.yaml restart n8n")
print("Done. Check n8n > Workflows > Homelab sysadmin (active) and Executions after the next 15-minute tick.")
