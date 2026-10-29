"""HTTP front for TrueNAS's JSON-RPC over WebSocket API (the only API from 25.04 on; REST goes
away in 26). n8n and the sysadmin investigator GET http://truenas-bridge:8080/<method> and get
the method's result as JSON, same shape the old /api/v2.0 paths returned: pool.query is /pool,
pool.dataset.get_instance?params=["DeepData"] is /pool/dataset/id/DeepData, alert.list is
/alert/list. Read-only: only query/list/get style methods pass. One connection per request;
a few calls an hour, nothing to pool."""
import http.server
import json
import os
import re
import ssl
import urllib.parse

import websockets.sync.client

URL = os.environ.get("TRUENAS_URL", "wss://192.168.1.209/api/current")
KEY = os.environ["TRUENAS_KEY"]
# query/list/get/info/status/results: reads. Anything with auth, keys or credentials never.
READ = re.compile(r"^(?!.*(auth|api_key|credential|keychain|certificate|kmip|config\.))[a-z0-9_.]+\.(query|get_instance|list|info|status|results|version|get)$")
SSL = ssl.create_default_context()
SSL.check_hostname = False
SSL.verify_mode = ssl.CERT_NONE  # self-signed, LAN only


def connect():
    # TrueNAS's middleware now and then never answers the WebSocket upgrade on a fresh connection
    # (about 1 in 15 at 2026-10-28, the next one succeeds), so one retry; n8n gives up at 15 s.
    for attempt in (1, 2):
        try:
            return websockets.sync.client.connect(URL, ssl=SSL, open_timeout=6)
        except TimeoutError:
            if attempt == 2:
                raise RuntimeError("TrueNAS did not answer the WebSocket handshake twice (6 s each)")


def call(method, params):
    with connect() as ws:
        for i, (m, p) in enumerate([("auth.login_with_api_key", [KEY]), (method, params)]):
            ws.send(json.dumps({"jsonrpc": "2.0", "id": i, "method": m, "params": p}))
            while True:  # skip server-pushed notifications (no id)
                msg = json.loads(ws.recv(timeout=60))
                if msg.get("id") == i:
                    break
            if "error" in msg:
                raise RuntimeError(f"{m}: {msg['error'].get('message') or msg['error']}")
            if m.startswith("auth.") and msg.get("result") is not True:
                raise RuntimeError("TrueNAS refused the API key")
        return msg["result"]


class Handler(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        u = urllib.parse.urlparse(self.path)
        method = u.path.strip("/")
        if method == "healthz":
            return self.reply(200, "ok", "text/plain")
        if not READ.fullmatch(method):
            return self.reply(400, f"method not allowed: {method}", "text/plain")
        try:
            params = json.loads(urllib.parse.parse_qs(u.query).get("params", ["[]"])[0])
            body = json.dumps(call(method, params))
        except Exception as e:  # the caller sees why, as text; the key never appears in messages
            return self.reply(502, str(e).replace(KEY, "***"), "text/plain")
        self.reply(200, body, "application/json")

    def reply(self, code, body, ctype):
        data = body.encode()
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def log_message(self, fmt, *args):
        print(fmt % args, flush=True)


http.server.ThreadingHTTPServer(("0.0.0.0", 8080), Handler).serve_forever()
