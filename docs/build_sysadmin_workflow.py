#!/usr/bin/env python3
"""Assemble stacks/apps/n8n/homelab-sysadmin.workflow.json from the sources in
stacks/apps/n8n/sysadmin/. Each Code node gets settings.js pasted on top of its own file, so
the rules are edited here, rebuilt, and re-imported into n8n (Workflows > Import from file).

    python3 docs/build_sysadmin_workflow.py

Credentials are not in the file; n8n asks on import. Names it expects:
  "Gmail SMTP"          emailSend (exists)
  "ops <vm>"            SSH private key, one per VM (host 192.168.1.5x, user ops, port 22)
  "Proxmox API token"   Header Auth: Authorization = PVEAPIToken=homepage@pve!homepage=<secret>
  "TrueNAS API key"     Header Auth: Authorization = Bearer <key>
"""
import json
import pathlib
import uuid

ROOT = pathlib.Path(__file__).resolve().parent.parent
SRC = ROOT / "stacks/apps/n8n/sysadmin"
OUT = ROOT / "stacks/apps/n8n/homelab-sysadmin.workflow.json"
MAIL_FROM = "Homelab <filingbin69@gmail.com>"
MAIL_TO = "mikegutberlet@gmail.com"
HOSTS = {"edge": "192.168.1.50", "media": "192.168.1.51", "gpu": "192.168.1.52", "apps": "192.168.1.53", "ai": "192.168.1.54"}
NS = uuid.UUID("3f1d2a8e-6b44-4c1a-9a3e-0d5c7b2f9e10")


def nid(name):
    return str(uuid.uuid5(NS, name))


def code(name):
    return (SRC / "settings.js").read_text() + "\n" + (SRC / name).read_text()


nodes, conn = [], {}
CRED_IDS = {}  # credential name -> id, filled by docs/sysadmin_deploy.py so the import needs no clicking


CRED_NAMES = {}  # lookup key -> the credential's real name in n8n, when it differs


def cred(kind, name):
    return {kind: {"id": CRED_IDS[name], "name": CRED_NAMES.get(name, name)}} if name in CRED_IDS else None


def node(name, type_, version, pos, params, **extra):
    n = {"id": nid(name), "name": name, "type": type_, "typeVersion": version, "position": list(pos), "parameters": params}
    n.update(extra)
    nodes.append(n)
    return name


def link(src, dst, out=0):
    conn.setdefault(src, {"main": []})
    while len(conn[src]["main"]) <= out:
        conn[src]["main"].append([])
    conn[src]["main"][out].append({"node": dst, "type": "main", "index": 0})


def webhook(name, path, pos):
    return node(name, "n8n-nodes-base.webhook", 2, pos, {"httpMethod": "POST", "path": path, "responseMode": "onReceived", "options": {}}, webhookId=nid("wh-" + name))


def http(name, url, pos, auth=None, insecure=False):
    p = {"method": "GET", "url": url, "options": {"timeout": 15000, "allowUnauthorizedCerts": insecure}}
    extra = {"onError": "continueRegularOutput"}
    if auth:
        p.update({"authentication": "genericCredentialType", "genericAuthType": "httpHeaderAuth"})
        c = cred("httpHeaderAuth", auth)
        if c:
            extra["credentials"] = c
    return node(name, "n8n-nodes-base.httpRequest", 4.2, pos, p, **extra)


def email(name, pos):
    c = cred("smtp", "Gmail SMTP")
    return node(name, "n8n-nodes-base.emailSend", 2.1, pos, {"fromEmail": MAIL_FROM, "toEmail": MAIL_TO, "subject": "={{ $json.subject }}", "emailFormat": "text", "text": "={{ $json.text }}", "options": {}}, **({"credentials": c} if c else {}))


def build(cred_ids=None, cred_names=None):
    """Return the workflow dict; cred_ids maps credential names to n8n ids (optional)."""
    nodes.clear(); conn.clear(); CRED_IDS.clear(); CRED_IDS.update(cred_ids or {})
    CRED_NAMES.clear(); CRED_NAMES.update(cred_names or {})
    _build()
    return {"name": "Homelab sysadmin", "nodes": list(nodes), "connections": dict(conn),
            "settings": {"executionOrder": "v1", "timezone": "America/Los_Angeles", "saveExecutionProgress": False}}


def _build():
    PVE, NAS = "Proxmox API token", "TrueNAS API key"


    # ---- VM reports ----
    webhook("report", "homelab/report", (0, 0))
    node("Evaluate report", "n8n-nodes-base.code", 2, (260, 0), {"jsCode": code("evaluate_report.js")})
    link("report", "Evaluate report")

    # ---- infra reads every 15 min ----
    node("Every 15 min", "n8n-nodes-base.scheduleTrigger", 1.2, (0, 300), {"rule": {"interval": [{"field": "cronExpression", "expression": "7/15 * * * *"}]}})
    chain = [
        http("PVE resources", "https://192.168.1.137:8006/api2/json/cluster/resources", (260, 300), auth=PVE, insecure=True),
        http("PVE cluster status", "https://192.168.1.137:8006/api2/json/cluster/status", (480, 300), auth=PVE, insecure=True),
        http("NAS pools", "https://192.168.1.209/api/v2.0/pool", (700, 300), auth=NAS, insecure=True),
        http("NAS dataset", "https://192.168.1.209/api/v2.0/pool/dataset/id/DeepData", (920, 300), auth=NAS, insecure=True),
        http("NAS alerts", "https://192.168.1.209/api/v2.0/alert/list", (1140, 300), auth=NAS, insecure=True),
        http("Scrutiny", "http://scrutiny:8080/api/summary", (1360, 300)),
    ]
    link("Every 15 min", chain[0])
    for a, b in zip(chain, chain[1:]):
        link(a, b)
    # One item per Proxmox node (name + ip from cluster status), then its status for swap.
    node("PVE node list", "n8n-nodes-base.code", 2, (1580, 300), {"jsCode": (
        "let nodes = [];\ntry { nodes = ($('PVE cluster status').first().json.data || []).filter(n => n.type === 'node' && n.online); } catch (e) {}\n"
        "if (!nodes.length) return [{ json: { node: null } }];\n"
        "return nodes.map(n => ({ json: { node: n.name, ip: n.ip } }));")})
    link(chain[-1], "PVE node list")
    node("PVE node status", "n8n-nodes-base.httpRequest", 4.2, (1800, 300), {
        "method": "GET", "url": "=https://{{ $json.ip }}:8006/api2/json/nodes/{{ $json.node }}/status",
        "authentication": "genericCredentialType", "genericAuthType": "httpHeaderAuth",
        "options": {"timeout": 15000, "allowUnauthorizedCerts": True}}, onError="continueRegularOutput", **({"credentials": cred("httpHeaderAuth", PVE)} if cred("httpHeaderAuth", PVE) else {}))
    link("PVE node list", "PVE node status")
    node("Evaluate infra", "n8n-nodes-base.code", 2, (2020, 300), {"jsCode": code("evaluate_infra.js")})
    link("PVE node status", "Evaluate infra")

    # ---- decide, email, fix ----
    node("Decide", "n8n-nodes-base.code", 2, (2300, 150), {"jsCode": code("decide.js")})
    link("Evaluate report", "Decide")
    link("Evaluate infra", "Decide")
    node("Email or fix", "n8n-nodes-base.switch", 3.2, (2520, 150), {"rules": {"values": [
        {"conditions": {"options": {"caseSensitive": True, "leftValue": "", "typeValidation": "strict"}, "conditions": [{"leftValue": "={{ $json.kind }}", "rightValue": "email", "operator": {"type": "string", "operation": "equals"}}], "combinator": "and"}, "renameOutput": True, "outputKey": "email"},
        {"conditions": {"options": {"caseSensitive": True, "leftValue": "", "typeValidation": "strict"}, "conditions": [{"leftValue": "={{ $json.kind }}", "rightValue": "fix", "operator": {"type": "string", "operation": "equals"}}], "combinator": "and"}, "renameOutput": True, "outputKey": "fix"},
    ]}, "options": {}})
    link("Decide", "Email or fix")
    email("Email critical", (2780, 40))
    link("Email or fix", "Email critical", 0)
    node("Route fix by host", "n8n-nodes-base.switch", 3.2, (2780, 260), {"rules": {"values": [
        {"conditions": {"options": {"caseSensitive": True, "leftValue": "", "typeValidation": "strict"}, "conditions": [{"leftValue": "={{ $json.host }}", "rightValue": h, "operator": {"type": "string", "operation": "equals"}}], "combinator": "and"}, "renameOutput": True, "outputKey": h}
        for h in HOSTS]}, "options": {}})
    link("Email or fix", "Route fix by host", 1)
    for i, h in enumerate(HOSTS):
        # The SSH credential "ops <vm>" carries host, port and user; the command is what Decide planned.
        c = cred("sshPrivateKey", f"ops {h}")
        node(f"ops {h}", "n8n-nodes-base.ssh", 1, (3040, 160 + 110 * i), {"resource": "command", "operation": "execute", "authentication": "privateKey", "command": "={{ $json.cmd }}", "cwd": "/"}, onError="continueRegularOutput", **({"credentials": c} if c else {}))
        link("Route fix by host", f"ops {h}", i)
        link(f"ops {h}", "Record fix result")
    node("Record fix result", "n8n-nodes-base.code", 2, (3300, 380), {"jsCode": code("record_fix.js")})
    # A failed fix emails; a follow-up (gluetun -> qbittorrent) waits a minute and goes round again.
    node("Failed or follow-up", "n8n-nodes-base.switch", 3.2, (3520, 380), {"rules": {"values": [
        {"conditions": {"options": {"caseSensitive": True, "leftValue": "", "typeValidation": "strict"}, "conditions": [{"leftValue": "={{ $json.kind }}", "rightValue": "email", "operator": {"type": "string", "operation": "equals"}}], "combinator": "and"}, "renameOutput": True, "outputKey": "email"},
        {"conditions": {"options": {"caseSensitive": True, "leftValue": "", "typeValidation": "strict"}, "conditions": [{"leftValue": "={{ $json.kind }}", "rightValue": "fix", "operator": {"type": "string", "operation": "equals"}}], "combinator": "and"}, "renameOutput": True, "outputKey": "fix"},
    ]}, "options": {}})
    link("Record fix result", "Failed or follow-up")
    link("Failed or follow-up", "Email critical", 0)
    node("Wait 60 s", "n8n-nodes-base.wait", 1.1, (3760, 480), {"amount": 60, "unit": "seconds"}, webhookId=nid("wait"))
    link("Failed or follow-up", "Wait 60 s", 1)
    link("Wait 60 s", "Route fix by host")

    # ---- the 08:00 digest and the legacy webhooks (unchanged paths) ----
    node("Every day 08:00", "n8n-nodes-base.scheduleTrigger", 1.2, (0, 900), {"rule": {"interval": [{"field": "cronExpression", "expression": "0 8 * * *"}]}})
    node("Build digest", "n8n-nodes-base.code", 2, (260, 900), {"jsCode": code("build_digest.js")})
    email("Email digest", (520, 900))
    link("Every day 08:00", "Build digest")
    link("Build digest", "Email digest")
    node("Store event", "n8n-nodes-base.code", 2, (260, 1200), {"jsCode": (SRC / "store_event.js").read_text()})
    for i, src in enumerate(["diun", "kuma", "scrutiny", "backup", "other"]):
        webhook(src, f"homelab/{src}", (0, 1100 + 90 * i))
        link(src, "Store event")


if __name__ == "__main__":
    wf = build()
    OUT.write_text(json.dumps(wf, indent=2) + "\n")
    print(f"wrote {OUT.relative_to(ROOT)}: {len(wf['nodes'])} nodes")
