#!/usr/bin/env python3
"""Torrent-path safety settings on the media VM (2026-10-26, docs/torrent-fallback.md).

Run by playbooks/torrent-hardening.yml as root on the media VM; `--dry-run` only reports.
Idempotent: every setting is read first and only written when it differs. Nothing is restarted.

  qBittorrent  skip executable file types in every torrent, bind to tun0 (second belt under
               gluetun's firewall), anonymous mode on, local peer discovery off
  SABnzbd      abort a job whose archive holds an executable (unwanted extensions)
  Prowlarr     tag `vpn` + HTTP indexer proxy gluetun:8888 on every torrent indexer (searches and
               .torrent fetches leave through PIA), FlareSolverr at http://gluetun:8191 (it shares
               gluetun's namespace now), TorrentDownload priority 40 -> 50, then a test of each
               torrent indexer so a broken proxy path shows up here and not in tomorrow's digest

The API keys are read from the apps' own config files and never printed.
"""
import json
import re
import subprocess
import sys
import urllib.error
import urllib.parse
import urllib.request

DRY = "--dry-run" in sys.argv
APPDATA = "/opt/appdata"
PROWLARR = "http://127.0.0.1:9696/api/v1"
SABNZBD = "http://127.0.0.1:8080/api"
QBIT = "http://127.0.0.1:8085/api/v2"  # inside gluetun's namespace (login skipped for localhost)
PROXY_NAME, PROXY_HOST, PROXY_PORT, VPN_TAG = "PIA (gluetun)", "gluetun", 8888, "vpn"
FLARESOLVERR_URL = "http://gluetun:8191"
DEMOTE = {"TorrentDownload": 50}  # 2026-10-26: source of the fake Resident Evil WEB-DL
# Executable types that have no business in a video, music or book release. Archives are not on
# the list (books arrive zipped at times); Sonarr/Radarr refuse an executable at import anyway,
# this keeps it off the NAS in the first place.
BAD_EXT = ["exe", "scr", "bat", "cmd", "com", "pif", "lnk", "msi", "msp", "vbs", "vbe", "js", "jse",
           "wsf", "wsh", "ps1", "psm1", "hta", "jar", "apk", "reg", "zipx", "arj"]

changes, problems = [], []


def note(what):
    changes.append(what)
    print(("would: " if DRY else "set: ") + what)


def http(url, data=None, headers=None, method=None, timeout=60):
    req = urllib.request.Request(url, data=data, headers=headers or {}, method=method)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            body = r.read().decode()
            return r.status, (json.loads(body) if body.strip().startswith(("{", "[")) else body)
    except urllib.error.HTTPError as e:
        body = e.read().decode(errors="replace")
        try:
            body = json.loads(body)
        except ValueError:
            pass
        return e.code, body


def docker_service(name):
    out = subprocess.run(["docker", "ps", "-q", "--filter", f"label=com.docker.compose.service={name}"],
                         capture_output=True, text=True).stdout.split()
    if not out:
        sys.exit(f"container for compose service {name} is not running")
    return out[0]


# ---------- qBittorrent (through gluetun's namespace) ----------
def qbit():
    gluetun = docker_service("gluetun")

    def wget(path, post=None):
        cmd = ["docker", "exec", gluetun, "wget", "-qO-", "-T", "20"]
        if post is not None:
            cmd += ["--post-data", post]
        r = subprocess.run(cmd + [QBIT + path], capture_output=True, text=True)
        if r.returncode:
            sys.exit(f"qBittorrent API {path} failed: {r.stderr.strip()[:200]}")
        return r.stdout

    prefs = json.loads(wget("/app/preferences"))
    want = {
        "excluded_file_names_enabled": True,
        "excluded_file_names": "\n".join(f"*.{e}" for e in BAD_EXT),
        "current_network_interface": "tun0",
        "current_interface_address": "",
        "anonymous_mode": True,
        "lsd": False,
    }
    diff = {k: v for k, v in want.items() if prefs.get(k) != v}
    for k, v in diff.items():
        note(f"qBittorrent {k} = {v!r}" if k != "excluded_file_names" else f"qBittorrent excluded_file_names = {len(BAD_EXT)} patterns")
    if diff and not DRY:
        wget("/app/setPreferences", "json=" + json.dumps(diff))
        after = json.loads(wget("/app/preferences"))
        bad = {k: after.get(k) for k, v in want.items() if after.get(k) != v}
        if bad:
            problems.append(f"qBittorrent did not keep {bad}")
    print(f"qBittorrent: {'ok' if not diff else 'updated'} (encryption={prefs.get('encryption')}, "
          f"dht={prefs.get('dht')}, pex={prefs.get('pex')}, port={prefs.get('listen_port')})")


# ---------- SABnzbd ----------
def sabnzbd():
    ini = open(f"{APPDATA}/sabnzbd/sabnzbd.ini").read()
    key = re.search(r"^api_key\s*=\s*(\S+)", ini, re.M).group(1)

    def api(**params):
        q = urllib.parse.urlencode({"apikey": key, "output": "json", **params})
        status, body = http(f"{SABNZBD}?{q}")
        if status != 200:
            sys.exit(f"SABnzbd API {params.get('mode')} -> {status}")
        return body

    misc = api(mode="get_config", section="misc")["config"]["misc"]
    want = {"unwanted_extensions": ",".join(BAD_EXT), "action_on_unwanted_extensions": 1, "unwanted_extensions_mode": 0}
    current = {"unwanted_extensions": ",".join(x.strip() for x in str(misc.get("unwanted_extensions", "")).split(",") if x.strip()),
               "action_on_unwanted_extensions": int(misc.get("action_on_unwanted_extensions", 0)),
               "unwanted_extensions_mode": int(misc.get("unwanted_extensions_mode", 0))}
    for k, v in want.items():
        if current[k] != v:
            note(f"SABnzbd {k} = {v!r} (was {current[k]!r})")
            if not DRY:
                api(mode="set_config", section="misc", keyword=k, value=v)
    if not DRY and any(current[k] != v for k, v in want.items()):
        misc = api(mode="get_config", section="misc")["config"]["misc"]
        if ",".join(x.strip() for x in str(misc.get("unwanted_extensions")).split(",")) != want["unwanted_extensions"] \
                or int(misc.get("action_on_unwanted_extensions")) != 1:
            problems.append("SABnzbd did not keep the unwanted-extensions settings")
    print("SABnzbd: ok" if all(current[k] == v for k, v in want.items()) else "SABnzbd: updated")


# ---------- Prowlarr ----------
def prowlarr():
    key = re.search(r"<ApiKey>(.*?)</ApiKey>", open(f"{APPDATA}/prowlarr/config.xml").read()).group(1)
    H = {"X-Api-Key": key, "Content-Type": "application/json"}

    def api(path, method="GET", body=None, timeout=60):
        status, out = http(f"{PROWLARR}/{path}", data=json.dumps(body).encode() if body is not None else None,
                           headers=H, method=method, timeout=timeout)
        if status >= 300 and not (method == "POST" and path.startswith("indexer/test")):
            sys.exit(f"Prowlarr {method} {path} -> {status}: {str(out)[:300]}")
        return status, out

    # The proxy must answer before any indexer is pointed at it (compose deployed first).
    prow = docker_service("prowlarr")
    r = subprocess.run(["docker", "exec", prow, "curl", "-fsS", "-m", "20", "-x", f"http://{PROXY_HOST}:{PROXY_PORT}", "https://api.ipify.org"],
                       capture_output=True, text=True)
    if r.returncode:
        sys.exit(f"gluetun's HTTP proxy ({PROXY_HOST}:{PROXY_PORT}) does not answer from Prowlarr; deploy the media "
                 f"stack first (site.yml --limit media --tags stacks): {r.stderr.strip()[:200]}")
    proxy_ip = r.stdout.strip()
    home_ip = subprocess.run(["curl", "-fsS", "-m", "20", "https://api.ipify.org"], capture_output=True, text=True).stdout.strip()
    if proxy_ip and proxy_ip == home_ip:
        sys.exit(f"the proxy exit IP equals the home IP ({home_ip}); gluetun is not on the VPN. Stopping.")
    print(f"gluetun proxy exit IP {proxy_ip} (home IP {home_ip or '?'})")

    # Tag
    _, tags = api("tag")
    tag = next((t for t in tags if t["label"] == VPN_TAG), None)
    if not tag:
        note(f"Prowlarr tag {VPN_TAG!r}")
        tag = {"id": -1} if DRY else api("tag", "POST", {"label": VPN_TAG})[1]

    # Indexer proxies: HTTP proxy to gluetun on the tag; FlareSolverr moved to gluetun's namespace.
    _, proxies = api("indexerproxy")
    http_proxy = next((p for p in proxies if p["name"] == PROXY_NAME), None)
    if not http_proxy:
        note(f"Prowlarr indexer proxy {PROXY_NAME!r} -> http://{PROXY_HOST}:{PROXY_PORT} on tag {VPN_TAG!r}")
        if not DRY:
            _, schemas = api("indexerproxy/schema")
            schema = next(s for s in schemas if s["implementation"] == "Http")
            schema.update({"name": PROXY_NAME, "tags": [tag["id"]]})
            for f in schema["fields"]:
                if f["name"] == "host":
                    f["value"] = PROXY_HOST
                elif f["name"] == "port":
                    f["value"] = PROXY_PORT
            api("indexerproxy", "POST", schema)
    for p in proxies:
        if p["implementation"] == "FlareSolverr":
            host = next(f for f in p["fields"] if f["name"] == "host")
            if host.get("value") != FLARESOLVERR_URL:
                note(f"Prowlarr FlareSolverr proxy host {host.get('value')!r} -> {FLARESOLVERR_URL!r}")
                host["value"] = FLARESOLVERR_URL
                if not DRY:
                    api(f"indexerproxy/{p['id']}", "PUT", p)

    # Torrent indexers: tag, and the demotions.
    _, indexers = api("indexer")
    torrent = [i for i in indexers if i["protocol"] == "torrent"]
    for i in torrent:
        upd = []
        if tag["id"] not in i.get("tags", []):
            i["tags"] = sorted(set(i.get("tags", [])) | {tag["id"]} - {-1})
            upd.append(f"tag {VPN_TAG}")
        if i["name"] in DEMOTE and i["priority"] != DEMOTE[i["name"]]:
            upd.append(f"priority {i['priority']} -> {DEMOTE[i['name']]}")
            i["priority"] = DEMOTE[i["name"]]
        if upd:
            note(f"Prowlarr indexer {i['name']}: {', '.join(upd)}")
            if not DRY:
                # forceSave: Prowlarr otherwise tests the indexer inside the save, and a FlareSolverr
                # site can take minutes (the first deploy timed out on 1337x). Tests run below.
                api(f"indexer/{i['id']}?forceSave=true", "PUT", i)
    if DRY:
        return

    # Test every torrent indexer through the new path (each hits the site once; FlareSolverr
    # sites are slow, so a long timeout and a timeout counts as a failure, not a crash).
    _, indexers = api("indexer")
    for i in (x for x in indexers if x["protocol"] == "torrent"):
        try:
            status, out = api("indexer/test", "POST", i, timeout=240)
        except (TimeoutError, OSError) as e:
            status, out = 599, [{"errorMessage": f"no answer within 240 s ({e.__class__.__name__})"}]
        ok = status < 300
        msg = "" if ok else "; ".join(e.get("errorMessage", str(e)) for e in (out if isinstance(out, list) else [out]))[:160]
        print(f"test {i['name']}: {'ok' if ok else 'FAILED ' + msg}")
        if not ok:
            problems.append(f"Prowlarr indexer {i['name']} fails through the proxy: {msg}")


for step in (qbit, sabnzbd, prowlarr):
    step()

print(f"\n{'would change' if DRY else 'changed'}: {len(changes)}")
if problems:
    print("PROBLEMS:\n  " + "\n  ".join(problems))
    sys.exit(2)
