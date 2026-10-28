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
  "Anthropic API key"   Anthropic: ~/.config/homelab/anthropic_api_key on the Mac (chat agent's model)
  "Gemini API key"      Google Gemini(PaLM) Api: the ai stack's LLM_API_KEY (chat agent's fallback model)
  "Sysadmin chat token" Header Auth: X-Chat-Token = <token> (the chat page's nginx and the Telegram
                        bot send it to the chat webhook; the Telegram push nodes send it to the bot)
  "Sysadmin internal token" Header Auth: X-Internal-Token = <token> (n8n sends it to its own act,
                        fixresult and chatstate webhooks; sysadmin_deploy.py makes a new one on every
                        run and keeps it only in n8n)
  "Investigator token"  Header Auth: X-Investigator-Token = <token> (n8n sends it to the investigator,
                        stacks/apps/sysadmin-investigator; sysadmin_deploy.py writes the same token
                        into /opt/appdata/sysadmin-investigator/investigator.env)

The chat agent's lookups and confirmed fixes go through a second, small workflow, "Homelab ops
door" (stacks/apps/n8n/homelab-ops-door.workflow.json, fixed id OPS_DOOR_ID): one SSH node per VM
behind a host switch, so the sysadmin workflow does not need a second copy of them. Hard
questions go through a third, "Homelab investigator" (homelab-investigator.workflow.json, fixed id
INVESTIGATOR_ID), which posts them to the investigator container: the chat agent's investigate
tool and the act run's auto-investigation of new critical alerts both call it.
"""
import json
import pathlib
import re
import uuid

ROOT = pathlib.Path(__file__).resolve().parent.parent
SRC = ROOT / "stacks/apps/n8n/sysadmin"
OUT = ROOT / "stacks/apps/n8n/homelab-sysadmin.workflow.json"
OUT_DOOR = ROOT / "stacks/apps/n8n/homelab-ops-door.workflow.json"
OUT_INVESTIGATOR = ROOT / "stacks/apps/n8n/homelab-investigator.workflow.json"
OPS_DOOR_ID = "HomelabOpsDoor01"  # n8n keeps an id given on import; sysadmin_deploy.py checks it
INVESTIGATOR_ID = "HomelabInvestig1"
# The investigator (stacks/apps/sysadmin-investigator) on the apps stack's network. A case stops
# itself after CASE_SECONDS (240) and answers with what it has; the wait here leaves room for that.
INVESTIGATOR_URL = "http://sysadmin-investigator:3006"
INVESTIGATOR_TOKEN = "Investigator token"
CHAT_MODEL = "claude-haiku-5-5"  # Anthropic instead of Gemini Flash (Goobs, 2026-10-23)
CHAT_FALLBACK_MODEL = "models/gemini-flash-latest"  # another provider, used when Anthropic fails (n8n takes one fallback)
MAIL_FROM = "Homelab <filingbin69@gmail.com>"
MAIL_TO = "mikegutberlet@gmail.com"
# The Telegram bot (stacks/apps/sysadmin-telegram) on the apps stack's network; /notify sends
# {subject, text} to the allowed chats. Switches: TELEGRAM_ALERTS / TELEGRAM_DIGEST in settings.js.
TELEGRAM_NOTIFY_URL = "http://sysadmin-telegram:3004/notify"
# n8n posting to its own webhooks (same container). Decide and Record fix result hand their emails
# and fixes to homelab/act, and the fix run posts the answer to homelab/fixresult, so no report,
# infra or fix-result run waits on SMTP or SSH (n8n saves the whole static data when a run ends,
# last writer wins). The chat follows the same rule: its run waits on the model and the ops door and
# writes nothing; it posts proposals and Confirm/Cancel to homelab/chatstate (a short run, the chat's
# only writer) and a confirmed fix's answer to homelab/fixresult. build() checks that no Code node of
# the chat run writes static data. Two runs still load the static data, wait, then write: the
# 15-minute infra run (its HTTP reads come before Evaluate infra and Decide; normally 1-3 s, up to
# about 2 min when reads time out) and the 08:00 digest (the Telegram push and the email come before
# Digest sent). A report, chatstate or fixresult write that lands in that window is lost.
# The three internal webhooks take their own token (INTERNAL_TOKEN), not the chat token the chat
# page's nginx and the Telegram bot hold: act runs any fix and sends any email it is given.
INTERNAL = "http://127.0.0.1:5678/webhook"
INTERNAL_TOKEN = "Sysadmin internal token"
HOSTS = {"edge": "192.168.1.50", "media": "192.168.1.51", "gpu": "192.168.1.52", "apps": "192.168.1.53", "ai": "192.168.1.54", "transcode": "192.168.1.55"}
HOSTS_LIST = list(HOSTS)
NS = uuid.UUID("3f1d2a8e-6b44-4c1a-9a3e-0d5c7b2f9e10")


def nid(name):
    return str(uuid.uuid5(NS, name))


def code(name, *shared):
    return "\n".join((SRC / f).read_text() for f in ("settings.js", *shared, name))


def chat_code(name):
    return code(name, "chat_common.js")


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


def link(src, dst, out=0, kind="main", index=0):
    """Connect src output `out` to dst input `index`. kind is "main" or an AI sub-node port (ai_languageModel, ai_memory, ai_tool)."""
    conn.setdefault(src, {}).setdefault(kind, [])
    while len(conn[src][kind]) <= out:
        conn[src][kind].append([])
    conn[src][kind][out].append({"node": dst, "type": kind, "index": index})


def webhook(name, path, pos, auth=None, respond="onReceived"):
    """A POST webhook that answers at once (respond="responseNode": a Respond to Webhook node answers);
    auth names a Header Auth credential the caller must send."""
    p = {"httpMethod": "POST", "path": path, "responseMode": respond, "options": {}}
    extra = {}
    if auth:
        p["authentication"] = "headerAuth"
        c = cred("httpHeaderAuth", auth)
        if c:
            extra["credentials"] = c
    return node(name, "n8n-nodes-base.webhook", 2, pos, p, webhookId=nid("wh-" + name), **extra)


def http(name, url, pos, auth=None, insecure=False, body=None, timeout=15000, **more):
    """GET url, or POST body ({field: expression}) as JSON. A failure only marks this node."""
    p = {"method": "POST" if body else "GET", "url": url, "options": {"timeout": timeout, "allowUnauthorizedCerts": insecure}}
    extra = {"onError": "continueRegularOutput", **more}
    if auth:
        p.update({"authentication": "genericCredentialType", "genericAuthType": "httpHeaderAuth"})
        c = cred("httpHeaderAuth", auth)
        if c:
            extra["credentials"] = c
    if body:
        p.update({"sendBody": True, "contentType": "json", "specifyBody": "keypair",
                  "bodyParameters": {"parameters": [{"name": k, "value": v} for k, v in body.items()]}})
    return node(name, "n8n-nodes-base.httpRequest", 4.2, pos, p, **extra)


def handoff(name, path, pos, source=None, body=None, timeout=10000, **more):
    """POST to one of this workflow's internal webhooks, once: all of `source`'s items as {items: [...]},
    `body` (a JS expression for the JSON object), or the one input item as it is. A failure (or
    timeout) only marks this node, and the next node gets {error}."""
    body = (f"={{{{ JSON.stringify({{ items: $('{source}').all().map((i) => i.json) }}) }}}}" if source
            else f"={{{{ JSON.stringify({body}) }}}}" if body else "={{ JSON.stringify($json) }}")
    p = {"method": "POST", "url": f"{INTERNAL}/{path}", "authentication": "genericCredentialType", "genericAuthType": "httpHeaderAuth",
         "sendBody": True, "contentType": "json", "specifyBody": "json", "jsonBody": body, "options": {"timeout": timeout}}
    c = cred("httpHeaderAuth", INTERNAL_TOKEN)
    return node(name, "n8n-nodes-base.httpRequest", 4.2, pos, p, onError="continueRegularOutput", executeOnce=True, **more, **({"credentials": c} if c else {}))


def telegram_push(name, pos):
    """POST {subject, text} to the Telegram bot's /notify with the chat token (X-Chat-Token)."""
    return http(name, TELEGRAM_NOTIFY_URL, pos, auth="Sysadmin chat token", timeout=30000,
                body={"subject": "={{ $json.subject }}", "text": "={{ $json.text }}"})


def email(name, pos):
    c = cred("smtp", "Gmail SMTP")
    return node(name, "n8n-nodes-base.emailSend", 2.1, pos, {"fromEmail": MAIL_FROM, "toEmail": MAIL_TO, "subject": "={{ $json.subject }}", "emailFormat": "text", "text": "={{ $json.text }}", "options": {}}, **({"credentials": c} if c else {}))


def _reset(cred_ids, cred_names):
    nodes.clear(); conn.clear(); CRED_IDS.clear(); CRED_IDS.update(cred_ids or {})
    CRED_NAMES.clear(); CRED_NAMES.update(cred_names or {})


def build(cred_ids=None, cred_names=None, ops_door_id=OPS_DOOR_ID, investigator_id=INVESTIGATOR_ID):
    """Return the sysadmin workflow dict; cred_ids maps credential names to n8n ids (optional)."""
    _reset(cred_ids, cred_names)
    _build(ops_door_id, investigator_id)
    check_chat_run_writes_nothing()
    return {"name": "Homelab sysadmin", "nodes": list(nodes), "connections": dict(conn),
            "settings": {"executionOrder": "v1", "timezone": "America/Los_Angeles", "saveExecutionProgress": False}}


def build_ops_door(cred_ids=None, cred_names=None):
    """Return the "Homelab ops door" sub-workflow: {query} or {route: fix, host, cmd} in, the door's answer out."""
    _reset(cred_ids, cred_names)
    node("When called", "n8n-nodes-base.executeWorkflowTrigger", 1.1, (0, 0), {"inputSource": "passthrough"})
    node("Parse", "n8n-nodes-base.code", 2, (260, 0), {"jsCode": code("opsdoor_parse.js")})
    link("When called", "Parse")
    node("By host", "n8n-nodes-base.switch", 3.2, (520, 0), {"rules": {"values": [is_("host", h, h) for h in HOSTS]}, "options": {"fallbackOutput": "extra"}})
    link("Parse", "By host")
    for i, h in enumerate(HOSTS):
        c = cred("sshPrivateKey", f"ops {h}")
        node(f"ops {h}", "n8n-nodes-base.ssh", 1, (800, -220 + 110 * i), {"resource": "command", "operation": "execute", "authentication": "privateKey", "command": "={{ $json.cmd }}", "cwd": "/"}, onError="continueRegularOutput", **({"credentials": c} if c else {}))
        link("By host", f"ops {h}", i)
        link(f"ops {h}", "Answer")
    link("By host", "Answer", len(HOSTS))  # unknown host or bad query: Parse's error goes straight back
    node("Answer", "n8n-nodes-base.code", 2, (1080, 0), {"jsCode": code("opsdoor_answer.js")})
    return {"id": OPS_DOOR_ID, "name": "Homelab ops door", "nodes": list(nodes), "connections": dict(conn),
            "settings": {"executionOrder": "v1", "timezone": "America/Los_Angeles", "saveExecutionProgress": False}}


def build_investigator(cred_ids=None, cred_names=None):
    """Return the "Homelab investigator" sub-workflow: {query} (chat) or {query, source: auto, subject}
    (an alert) in, {status, answer, proposal, ...} out. It holds no static data, so its long wait
    loses nothing."""
    _reset(cred_ids, cred_names)
    node("When called", "n8n-nodes-base.executeWorkflowTrigger", 1.1, (0, 0), {"inputSource": "passthrough"})
    node("Investigate request", "n8n-nodes-base.code", 2, (260, 0), {"jsCode": code("investigate_request.js")})
    link("When called", "Investigate request")
    node("Ask or skip", "n8n-nodes-base.switch", 3.2, (520, 0), {"rules": {"values": [is_("route", "ask", "ask"), is_("route", "skip", "skip")]}, "options": {}})
    link("Investigate request", "Ask or skip")
    http("Investigator", f"{INVESTIGATOR_URL}/investigate", (780, -100), auth=INVESTIGATOR_TOKEN, timeout=300000,
         body={"question": "={{ $json.question }}", "source": "={{ $json.source }}"})
    link("Ask or skip", "Investigator", 0)
    node("Investigate answer", "n8n-nodes-base.code", 2, (1040, 0), {"jsCode": code("investigate_answer.js")})
    link("Investigator", "Investigate answer")
    link("Ask or skip", "Investigate answer", 1)
    return {"id": INVESTIGATOR_ID, "name": "Homelab investigator", "nodes": list(nodes), "connections": dict(conn),
            "settings": {"executionOrder": "v1", "timezone": "America/Los_Angeles", "saveExecutionProgress": False}}


def is_(field, value, key):
    """One Switch rule: output `key` when $json.<field> equals value."""
    return {"conditions": {"options": {"caseSensitive": True, "leftValue": "", "typeValidation": "strict"}, "conditions": [{"leftValue": f"={{{{ $json.{field} }}}}", "rightValue": value, "operator": {"type": "string", "operation": "equals"}}], "combinator": "and"}, "renameOutput": True, "outputKey": key}


def tool_code(name, description, file, schema=None, pos=(0, 0)):
    """A Code Tool for the agent. With a schema the tool's input arrives as the object `query`."""
    p = {"name": name, "description": description, "language": "javaScript", "jsCode": chat_code(file), "specifyInputSchema": bool(schema)}
    if schema:
        p.update({"schemaType": "manual", "inputSchema": json.dumps(schema, indent=2)})
    return node(f"tool {name}", "@n8n/n8n-nodes-langchain.toolCode", 1.3, pos, p)


# A write to static data in the chat run would be saved whole when that run ends, after the model
# or the ops door (up to minutes), over whatever the VM reports wrote meanwhile. Only Chat state
# (homelab/chatstate, a run of its own) writes. A chat-run node may read, as `const sd = ...`; the
# check looks for writes through sd and through names bound straight to it (const e = (sd.x || [])...).
def _writes(v):
    path = rf"\b{v}(?:\.[A-Za-z_$][\w$]*|\[[^\]\n]*\])"
    return [
        (path + r"+\s*(?:\*\*|<<|>>>?|\?\?|\|\||&&|[-+*/%&|^])?=(?![=>])", "assigns into static data"),
        (path + r"+\s*(?:\+\+|--)|(?:\+\+|--)\s*" + path + "+", "counts up or down in static data"),
        (rf"\bdelete\s+{v}\b", "deletes from static data"),
        (path + r"*\.(?:push|pop|shift|unshift|splice|sort|reverse|fill|copyWithin)\s*\(", "changes an array in static data"),
        (rf"\bObject\.assign\s*\(\s*{v}\b", "assigns into static data"),
    ]


def check_chat_run_writes_nothing():
    """Fail the build when a Code node or Code Tool on the chat webhook's run could write static data."""
    shared = [(SRC / f).read_text() for f in ("settings.js", "chat_common.js")]
    by_name = {n["name"]: n for n in nodes}
    run, todo = set(), ["chat"]
    while todo:  # the main connections from the chat webhook, plus the sub-nodes (tools) of each node reached
        name = todo.pop()
        if name in run:
            continue
        run.add(name)
        todo += [t["node"] for out in conn.get(name, {}).get("main", []) for t in out]
        todo += [src for src, kinds in conn.items() for kind, outs in kinds.items() if kind != "main" for out in outs for t in out if t["node"] == name]
    bad = []
    for name in sorted(run):
        js = by_name[name]["parameters"].get("jsCode")
        if js is None:
            continue
        for part in shared:
            js = js.replace(part, "", 1)
        js = re.sub(r"(^|\s)//.*", r"\1", js)
        if re.search(r"\bchatStore\s*\(", js):
            bad.append(f"{name}: calls chatStore (it creates sd.chat)")
        if js.count("$getWorkflowStaticData") != len(re.findall(r"\bconst sd = \$getWorkflowStaticData\('global'\);", js)):
            bad.append(f"{name}: reads static data under another name than `const sd`")
        names = {"sd"} | set(re.findall(r"\b(?:const|let|var)\s+([A-Za-z_$][\w$]*)\s*=\s*[(\s]*sd\b", js))
        bad += [f"{name}: {why}: {m.group(0).strip()}" for v in sorted(names) for rx, why in _writes(re.escape(v)) for m in re.finditer(rx, js)]
    if "Chat prepare" not in run or "tool propose_fix" not in run or "Chat state" in run:
        bad.append(f"the chat run is not what this check expects: {sorted(run)}")
    if bad:
        raise SystemExit("The chat run must not write static data (only Chat state, homelab/chatstate, does):\n  " + "\n  ".join(bad))


def _build(ops_door_id=OPS_DOOR_ID, investigator_id=INVESTIGATOR_ID):
    PVE = "Proxmox API token"
    DOOR = {"__rl": True, "mode": "id", "value": ops_door_id}
    INV = {"__rl": True, "mode": "id", "value": investigator_id}


    # ---- VM reports ----
    webhook("report", "homelab/report", (0, 0))
    node("Evaluate report", "n8n-nodes-base.code", 2, (260, 0), {"jsCode": code("evaluate_report.js")})
    link("report", "Evaluate report")

    # ---- infra reads every 15 min ----
    # At :14/:29/:44/:59, clear of the VM reports (homelab-agent.timer: edge :01, media :04, gpu :07, transcode :09,
    # apps :10, ai :12) and of the 08:00 digest. Each read runs once (an array answer, like TrueNAS's
    # pools and alerts, becomes one item per element and would run the next read once per item), and
    # an empty answer still passes one empty item on, so the chain never stops early.
    node("Every 15 min", "n8n-nodes-base.scheduleTrigger", 1.2, (0, 300), {"rule": {"interval": [{"field": "cronExpression", "expression": "14/15 * * * *"}]}})
    once = {"executeOnce": True, "alwaysOutputData": True}
    chain = [
        http("PVE resources", "https://192.168.1.137:8006/api2/json/cluster/resources", (260, 300), auth=PVE, insecure=True, **once),
        http("PVE cluster status", "https://192.168.1.137:8006/api2/json/cluster/status", (480, 300), auth=PVE, insecure=True, **once),
        # Through the stack's truenas-bridge (WebSocket API; the REST one is deprecated): same answers
        # as the old /api/v2.0/pool, /pool/dataset/id/DeepData and /alert/list.
        http("NAS pools", "http://truenas-bridge:8080/pool.query", (700, 300), **once),
        http("NAS dataset", 'http://truenas-bridge:8080/pool.dataset.get_instance?params=["DeepData"]', (920, 300), **once),
        http("NAS alerts", "http://truenas-bridge:8080/alert.list", (1140, 300), **once),
        http("Scrutiny", "http://scrutiny:8080/api/summary", (1360, 300), **once),
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

    # ---- decide, then hand off: the run's email and fixes go to homelab/act in one post ----
    node("Decide", "n8n-nodes-base.code", 2, (2300, 150), {"jsCode": code("decide.js")})
    link("Evaluate report", "Decide")
    link("Evaluate infra", "Decide")
    handoff("Hand off", "homelab/act", (2520, 150), source="Decide")
    link("Decide", "Hand off")

    # ---- act: emails and fixes, in a run that never touches static data ----
    webhook("act", "homelab/act", (0, 2600), auth=INTERNAL_TOKEN)
    node("Act", "n8n-nodes-base.code", 2, (260, 2600), {"jsCode": code("act.js")})
    link("act", "Act")
    node("Email or fix", "n8n-nodes-base.switch", 3.2, (520, 2600), {"rules": {"values": [is_("kind", "email", "email"), is_("kind", "fix", "fix")]}, "options": {}})
    link("Act", "Email or fix")
    # Every critical email is also pushed to Telegram (TELEGRAM_ALERTS). These nodes sit above
    # "Email critical" on the canvas, so n8n (execution order v1) runs them first: if Gmail fails,
    # the alert has already reached Telegram; a Telegram failure only marks "Telegram push".
    node("Telegram alert?", "n8n-nodes-base.code", 2, (780, 2360), {"jsCode": code("telegram_alert.js")})
    link("Email or fix", "Telegram alert?", 0)
    telegram_push("Telegram push", (1040, 2360))
    link("Telegram alert?", "Telegram push")
    email("Email critical", (780, 2480))
    link("Email or fix", "Email critical", 0)
    # A follow-up (gluetun -> qbittorrent) waits a minute first; Wait under 65 s stays in this run.
    node("Now or later", "n8n-nodes-base.switch", 3.2, (780, 2720), {"rules": {"values": [
        {"conditions": {"options": {"caseSensitive": True, "leftValue": "", "typeValidation": "strict"}, "conditions": [{"leftValue": "={{ $json.delay ? 'later' : 'now' }}", "rightValue": v, "operator": {"type": "string", "operation": "equals"}}], "combinator": "and"}, "renameOutput": True, "outputKey": v}
        for v in ("now", "later")]}, "options": {}})
    link("Email or fix", "Now or later", 1)
    node("Wait 60 s", "n8n-nodes-base.wait", 1.1, (1040, 2820), {"amount": 60, "unit": "seconds"}, webhookId=nid("wait"))
    link("Now or later", "Wait 60 s", 1)
    node("Route fix by host", "n8n-nodes-base.switch", 3.2, (1300, 2720), {"rules": {"values": [is_("host", h, h) for h in HOSTS]}, "options": {}})
    link("Now or later", "Route fix by host", 0)
    link("Wait 60 s", "Route fix by host")
    for i, h in enumerate(HOSTS):
        # The SSH credential "ops <vm>" carries host, port and user; the command is what Decide planned.
        # cwd is a required parameter (n8n refuses "" as missing); node-ssh turns "/" into "cd / ; <cmd>"
        # (quoted "cd '/' ; " in other versions), and homelab-ops-door.sh strips that prefix before its check.
        c = cred("sshPrivateKey", f"ops {h}")
        node(f"ops {h}", "n8n-nodes-base.ssh", 1, (1560, 2500 + 110 * i), {"resource": "command", "operation": "execute", "authentication": "privateKey", "command": "={{ $json.cmd }}", "cwd": "/"}, onError="continueRegularOutput", **({"credentials": c} if c else {}))
        link("Route fix by host", f"ops {h}", i)
        link(f"ops {h}", "Fix answer")
    node("Fix answer", "n8n-nodes-base.code", 2, (1820, 2720), {"jsCode": code("fix_answer.js")})
    handoff("Post fix result", "homelab/fixresult", (2080, 2720))
    link("Fix answer", "Post fix result")
    # A new critical (or a fix that did not help one) also goes to the investigator, once per run.
    # These nodes sit below the fixes on the canvas, so n8n runs them last: the alert email and this
    # run's fixes are out before the investigation starts, and its note follows as a second email.
    node("Investigate alert?", "n8n-nodes-base.code", 2, (780, 3000), {"jsCode": code("investigate_alert.js")})
    link("Email or fix", "Investigate alert?", 0)
    node("Investigate alert", "n8n-nodes-base.executeWorkflow", 1.2, (1040, 3000), {"source": "database", "workflowId": INV, "mode": "once", "options": {}}, onError="continueRegularOutput")
    link("Investigate alert?", "Investigate alert")
    node("Investigation email", "n8n-nodes-base.code", 2, (1300, 3000), {"jsCode": code("investigation_email.js")})
    link("Investigate alert", "Investigation email")
    node("Telegram investigation?", "n8n-nodes-base.code", 2, (1560, 2900), {"jsCode": code("telegram_alert.js")})
    link("Investigation email", "Telegram investigation?")
    telegram_push("Telegram investigation push", (1820, 2900))
    link("Telegram investigation?", "Telegram investigation push")
    email("Email investigation", (1560, 3020))
    link("Investigation email", "Email investigation")

    # ---- fixresult: log the answer (a short run), then hand off its email or follow-up ----
    webhook("fixresult", "homelab/fixresult", (0, 3100), auth=INTERNAL_TOKEN)
    node("Record fix result", "n8n-nodes-base.code", 2, (260, 3100), {"jsCode": code("record_fix.js")})
    link("fixresult", "Record fix result")
    handoff("Hand off result", "homelab/act", (520, 3100), source="Record fix result")
    link("Record fix result", "Hand off result")

    # ---- chat page (stacks/apps/sysadmin-chat) and Telegram bot: a question goes to the AI Agent,
    # which reads the sysadmin's state through Code Tools and the ops door through the sub-workflow;
    # a fix it proposes waits for Goobs's Confirm, which comes back as its own request and is checked
    # in code. This run waits on the model or the ops door, so it writes no static data: Chat state
    # (homelab/chatstate, below) registers proposals and claims Confirm/Cancel, and the fix's answer
    # goes to homelab/fixresult like an automatic fix's ----
    c = cred("httpHeaderAuth", "Sysadmin chat token")
    node("chat", "n8n-nodes-base.webhook", 2, (0, 1700), {"httpMethod": "POST", "path": "homelab/chat", "authentication": "headerAuth", "responseMode": "responseNode", "options": {}},
         webhookId=nid("wh-chat"), **({"credentials": c} if c else {}))
    node("Chat prepare", "n8n-nodes-base.code", 2, (260, 1700), {"jsCode": chat_code("chat_prepare.js")})
    link("chat", "Chat prepare")
    node("Chat next", "n8n-nodes-base.switch", 3.2, (520, 1700), {"rules": {"values": [is_("route", "reply", "reply"), is_("route", "agent", "agent"), is_("route", "claim", "claim")]}, "options": {}})
    link("Chat prepare", "Chat next")
    # Every answer but a confirmed fix's leaves here; only $json.reply ({text} or {text, proposal}) is sent.
    node("Chat respond", "n8n-nodes-base.respondToWebhook", 1.1, (1960, 1560), {"respondWith": "json", "responseBody": "={{ JSON.stringify($json.reply) }}", "options": {}})
    link("Chat next", "Chat respond", 0)
    # The agent and its sub-nodes. Its steps come back with the answer (returnIntermediateSteps), so
    # Chat finish can see which fix propose_fix allowed; they are never sent to the page or the bot.
    node("Sysadmin agent", "@n8n/n8n-nodes-langchain.agent", 3.1, (800, 1700), {
        "promptType": "define", "text": "={{ $json.chatInput }}", "hasOutputParser": False, "needsFallback": True,
        "options": {"systemMessage": (SRC / "chat_common.js").read_text().split("const CHAT_SYSTEM = `")[1].split("`;")[0],
                    "maxIterations": 8, "returnIntermediateSteps": True, "enableStreaming": False}}, onError="continueRegularOutput")
    link("Chat next", "Sysadmin agent", 1)
    # Retry each call; when the main model still fails, the agent falls back to Gemini, another
    # provider, so an Anthropic outage does not take the chat down. Haiku 5.5 rejects any temperature
    # but its default, so none is set. Thinking is switched off: Haiku 5.5 thinks by default, and
    # after a turn with parallel tool calls n8n sends the thinking blocks back in a form the API
    # rejects ("Invalid `signature` in `thinking` block"), which the Gemini fallback cannot read
    # either ("Unknown content type thinking"). Node 1.3 sends thinking {type: disabled} for false.
    retry = {"retryOnFail": True, "maxTries": 3, "waitBetweenTries": 5000}
    a = cred("anthropicApi", "Anthropic API key")
    node("Claude", "@n8n/n8n-nodes-langchain.lmChatAnthropic", 1.3, (640, 1900),
         {"model": {"__rl": True, "mode": "id", "value": CHAT_MODEL}, "options": {"maxTokensToSample": 4096, "thinking": False}}, **retry, **({"credentials": a} if a else {}))
    link("Claude", "Sysadmin agent", 0, "ai_languageModel")
    g = cred("googlePalmApi", "Gemini API key")
    node("Gemini fallback", "@n8n/n8n-nodes-langchain.lmChatGoogleGemini", 1.2, (760, 1900), {"modelName": CHAT_FALLBACK_MODEL, "options": {"temperature": 0.2}}, **retry, **({"credentials": g} if g else {}))
    link("Gemini fallback", "Sysadmin agent", 0, "ai_languageModel", index=1)
    node("Chat memory", "@n8n/n8n-nodes-langchain.memoryBufferWindow", 1.4, (860, 1900), {"sessionIdType": "customKey", "sessionKey": "={{ $json.sessionId }}", "contextWindowLength": 10})
    link("Chat memory", "Sysadmin agent", 0, "ai_memory")
    tools = [
        tool_code("lab_overview", "Current state of the whole lab: open issues by severity, whether automatic and chat fixes are on, when each VM last reported, free space on the DeepData pool, fix counts. Call this first for any health question. No input needed.", "tool_overview.js", pos=(1020, 1900)),
        tool_code("vm_report", "The latest 15-minute report of one VM: system (memory, swap, load, uptime, updates), disks, NFS and backup mounts, last backup, every container's state/health/restarts, and the media apps' queues (Sonarr/Radarr stuck items, Prowlarr indexer backoffs, SABnzbd, qBittorrent VPN, Tdarr).", "tool_report.js",
                  {"type": "object", "properties": {"host": {"type": "string", "enum": HOSTS_LIST, "description": "VM name"}}, "required": ["host"]}, pos=(1180, 1900)),
        tool_code("recent_fixes", "Fixes run in the last 7 days (automatic and chat), fixes only planned while in report-only mode, and issues that resolved by themselves since the last digest. No input needed.", "tool_fixes.js", pos=(1340, 1900)),
        tool_code("propose_fix", "Propose ONE safe fix for Goobs to confirm. It does not run anything: Goobs gets a Confirm button. verb is one of restart, stop, arr-retry, arr-blocklist, prowlarr-test, docker-prune, remount-nas, plex-remote-on (gpu only), update (newer image; auto-update list only); args: [service] for restart/stop/update, [sonarr|radarr, queue id] for arr-retry/arr-blocklist, [indexer id] for prowlarr-test, [] for the rest. Give a one-line reason.", "tool_propose.js",
                  {"type": "object", "properties": {"host": {"type": "string", "enum": HOSTS_LIST}, "verb": {"type": "string", "enum": ["restart", "stop", "arr-retry", "arr-blocklist", "prowlarr-test", "docker-prune", "remount-nas", "plex-remote-on", "update"]}, "args": {"type": "array", "items": {"type": "string"}}, "reason": {"type": "string"}}, "required": ["host", "verb", "args", "reason"]}, pos=(1500, 1900)),
    ]
    node("tool ops_lookup", "@n8n/n8n-nodes-langchain.toolWorkflow", 2.2, (1660, 1900), {
        "name": "ops_lookup",
        "description": "Live read-only lookup on one VM through the ops door. Input is a plain string: \"<vm> status\" lists every container with its state, or \"<vm> logs <service> [lines]\" returns the last lines (default 80, max 200) of that container's log, e.g. \"media logs sonarr 100\". vm is one of " + ", ".join(HOSTS_LIST) + ".",
        "source": "database", "workflowId": DOOR, "workflowInputs": {"mappingMode": "defineBelow", "value": None}})
    node("tool investigate", "@n8n/n8n-nodes-langchain.toolWorkflow", 2.2, (1820, 1900), {
        "name": "investigate",
        "description": "Hand a hard question to the investigator, a stronger model that can read any log, config file, journal, the Proxmox and TrueNAS APIs, Uptime Kuma and the lab's docs, and may propose one fix (a fixed verb or a short script) for Goobs to confirm. Takes one to four minutes and costs money, so use it only when the quick tools did not find the cause, or when Goobs asks you to dig in. Input is a plain string: the whole question with everything you already found, e.g. \"Sonarr on media keeps failing imports since this morning; logs show 'access denied' on /mnt/nfs/Media/TV. Why, and what fixes it?\"",
        "source": "database", "workflowId": INV, "workflowInputs": {"mappingMode": "defineBelow", "value": None}})
    for t in tools + ["tool ops_lookup", "tool investigate"]:
        link(t, "Sysadmin agent", 0, "ai_tool")
    node("Chat finish", "n8n-nodes-base.code", 2, (1080, 1700), {"jsCode": chat_code("chat_finish.js")})
    link("Sysadmin agent", "Chat finish")
    node("Finish next", "n8n-nodes-base.switch", 3.2, (1300, 1700), {"rules": {"values": [is_("route", "reply", "reply"), is_("route", "register", "register")]}, "options": {}})
    link("Chat finish", "Finish next")
    link("Finish next", "Chat respond", 0)
    # A proposal: Chat state numbers it and parks it for this session; Proposal reply adds the buttons.
    handoff("Register proposal", "homelab/chatstate", (1520, 1800), timeout=15000,
            body="{ op: 'propose', sessionId: $json.sessionId, fix: $json.fix, reason: $json.reason }")
    link("Finish next", "Register proposal", 1)
    node("Proposal reply", "n8n-nodes-base.code", 2, (1740, 1800), {"jsCode": chat_code("chat_proposal_reply.js")})
    link("Register proposal", "Proposal reply")
    link("Proposal reply", "Chat respond")
    # Confirm or Cancel: Chat state claims the proposal (and logs a confirmed fix as started); only
    # then does the ops door run it. Chat after fix answers Goobs first (Fix respond), then posts the
    # door's answer to homelab/fixresult, so a failed post never changes or delays the reply.
    # Claim waits long (Chat state normally answers in well under a second): giving up on a claim that
    # Chat state then makes would leave a "started" fix that never ran. Not retried: a second claim
    # finds the proposal gone.
    handoff("Claim", "homelab/chatstate", (800, 2100), timeout=60000,
            body="{ op: 'claim', sessionId: $json.sessionId, n: $json.n, action: $json.action, who: $json.who }")
    link("Chat next", "Claim", 2)
    node("Claim answer", "n8n-nodes-base.code", 2, (1020, 2100), {"jsCode": chat_code("chat_claim_answer.js")})
    link("Claim", "Claim answer")
    node("Claim next", "n8n-nodes-base.switch", 3.2, (1240, 2100), {"rules": {"values": [is_("route", "reply", "reply"), is_("route", "fix", "fix")]}, "options": {}})
    link("Claim answer", "Claim next")
    link("Claim next", "Chat respond", 0)
    node("Run confirmed fix", "n8n-nodes-base.executeWorkflow", 1.2, (1460, 2200), {"source": "database", "workflowId": DOOR, "mode": "once", "options": {}}, onError="continueRegularOutput")
    link("Claim next", "Run confirmed fix", 1)
    node("Chat after fix", "n8n-nodes-base.code", 2, (1680, 2200), {"jsCode": chat_code("chat_after_fix.js")})
    link("Run confirmed fix", "Chat after fix")
    node("Fix respond", "n8n-nodes-base.respondToWebhook", 1.1, (1900, 2200), {"respondWith": "json", "responseBody": "={{ JSON.stringify($json.reply) }}", "options": {}})
    link("Chat after fix", "Fix respond")
    # Retried (n8n retries a node whose output is {error}): a lost post leaves the entry "started"
    # for good. A second post of the same chat result is harmless (Record fix result sets it again).
    handoff("Post chat fix result", "homelab/fixresult", (2120, 2200), body="{ results: $json.results }",
            retryOnFail=True, maxTries=3, waitBetweenTries=2000)
    link("Fix respond", "Post chat fix result")

    # ---- chatstate: the chat's writes (register a proposal, claim a Confirm/Cancel) in a short run
    # that answers the chat run's "Register proposal" or "Claim" ----
    webhook("chatstate", "homelab/chatstate", (0, 3400), auth=INTERNAL_TOKEN, respond="responseNode")
    node("Chat state", "n8n-nodes-base.code", 2, (260, 3400), {"jsCode": chat_code("chat_state.js")})
    link("chatstate", "Chat state")
    node("Chat state reply", "n8n-nodes-base.respondToWebhook", 1.1, (520, 3400), {"respondWith": "json", "responseBody": "={{ JSON.stringify($json) }}", "options": {}})
    link("Chat state", "Chat state reply")

    # ---- the 08:00 digest and the legacy webhooks (unchanged paths) ----
    node("Every day 08:00", "n8n-nodes-base.scheduleTrigger", 1.2, (0, 900), {"rule": {"interval": [{"field": "cronExpression", "expression": "0 8 * * *"}]}})
    # The investigator's spend for the digest; when it is down or not set up, the digest leaves it out.
    http("Investigator usage", f"{INVESTIGATOR_URL}/usage", (130, 1000), auth=INVESTIGATOR_TOKEN, timeout=10000, executeOnce=True, alwaysOutputData=True)
    node("Build digest", "n8n-nodes-base.code", 2, (260, 900), {"jsCode": code("build_digest.js")})
    email("Email digest", (520, 900))
    link("Every day 08:00", "Investigator usage")
    link("Investigator usage", "Build digest")
    link("Build digest", "Email digest")
    # Clears what the digest reported, only once the email is out.
    node("Digest sent", "n8n-nodes-base.code", 2, (780, 900), {"jsCode": code("digest_sent.js")})
    link("Email digest", "Digest sent")
    # The digest to Telegram too, only while TELEGRAM_DIGEST is on (off by default).
    node("Telegram digest?", "n8n-nodes-base.code", 2, (520, 760), {"jsCode": code("telegram_digest.js")})
    link("Build digest", "Telegram digest?")
    telegram_push("Telegram digest push", (780, 760))
    link("Telegram digest?", "Telegram digest push")
    node("Store event", "n8n-nodes-base.code", 2, (260, 1200), {"jsCode": (SRC / "store_event.js").read_text()})
    for i, src in enumerate(["diun", "kuma", "scrutiny", "backup", "other"]):
        webhook(src, f"homelab/{src}", (0, 1100 + 90 * i))
        link(src, "Store event")


if __name__ == "__main__":
    door = build_ops_door()
    OUT_DOOR.write_text(json.dumps(door, indent=2) + "\n")
    print(f"wrote {OUT_DOOR.relative_to(ROOT)}: {len(door['nodes'])} nodes")
    inv = build_investigator()
    OUT_INVESTIGATOR.write_text(json.dumps(inv, indent=2) + "\n")
    print(f"wrote {OUT_INVESTIGATOR.relative_to(ROOT)}: {len(inv['nodes'])} nodes")
    wf = build()
    OUT.write_text(json.dumps(wf, indent=2) + "\n")
    print(f"wrote {OUT.relative_to(ROOT)}: {len(wf['nodes'])} nodes")
