#!/usr/bin/python3
"""Read-only shell for the sysadmin's investigator (stacks/apps/sysadmin-investigator).

The investigator SSHes in as the `inspect` user, whose key is pinned to homelab-inspect-door
(forced command), and that runs this script through one sudo rule with the requested command
line as a single argument. Nothing here goes through a shell: the line is split like a shell
would, every program and flag is checked against the lists below, paths must sit inside the
readable roots, and the programs run directly. A pipe into a few text filters (grep, head,
tail, wc, sort, uniq, cut) is allowed. Output is cut to OUT_MAX characters and every secret we
can recognise (passwords, API keys, tokens, private keys) is masked before it leaves the VM.

    homelab-inspect 'journalctl -u docker -n 50'
    homelab-inspect 'docker logs --tail 200 media-sonarr-1 | grep -i error'
    homelab-inspect 'app sonarr /api/v3/queue?pageSize=50'

Anything that writes, follows (-f), executes (find -exec, docker exec) or reads a secret file is
refused with exit code 2. Each call is logged to the journal under homelab-inspect.
"""
import glob
import json
import os
import re
import shlex
import socket
import subprocess
import sys
import urllib.parse

OUT_MAX = 40000
RUN_TIMEOUT = 45
APPDATA = "/opt/appdata"

# Readable roots. /mnt/nfs only for listing and sizes (reading media through the model is useless
# and a hung NAS must not stall a cat); /proc minus environ/mem (environ holds secrets).
ROOTS = ("/etc", "/var/log", "/opt/stacks", "/opt/appdata", "/proc", "/sys", "/srv", "/run/systemd",
         "/var/lib/docker/containers", "/usr/local/sbin", "/boot", "/home/goobs")
NFS_ROOT = "/mnt/nfs"
NFS_PROGRAMS = {"ls", "stat", "du", "find", "df"}
DENY_PATH = re.compile(
    r"(^/etc/(g?shadow|sudoers)|/etc/sudoers\.d|/etc/ssh/ssh_host_.*key$|\.pem$|\.key$|/id_(rsa|ed25519|ecdsa)[^/]*$"
    r"|/\.ssh/|/\.config/sops|/keys\.txt$|/proc/(\d+|self|thread-self)/(environ|mem|maps|smaps|pagemap|fd)|/proc/kcore"
    r"|/etc/homelab-.*\.key|\.sops\.env$|/\.git/|/\.bash_history$)")

SECRET_WORD = r"(?:PASS(?:WORD|WD|PHRASE)?|SECRET|TOKEN|API_?KEY|APIKEY|PRIVATE_?KEY|ACCESS_?KEY|CREDENTIALS?|COOKIE|SALT|AUTH_?KEY|ENCRYPTION_?KEY|PSK|WIREGUARD_PRIVATE_KEY)"
MASKS = [
    (re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----.*?-----END [A-Z ]*PRIVATE KEY-----", re.S), "[private key masked]"),
    (re.compile(r"\b(sk-ant-|tskey-|ghp_|gho_|github_pat_|xox[bpas]-|AGE-SECRET-KEY-1|glpat-)[\w-]+"), r"\1***"),
    (re.compile(r"\beyJ[\w-]{8,}\.[\w-]{8,}\.[\w-]{8,}"), "[jwt masked]"),
    (re.compile(r"\b(\d{8,10}):[\w-]{30,}"), r"\1:***"),  # Telegram bot tokens
    (re.compile(r"(\b[a-z][a-z0-9+.-]*://[^/\s:@'\"]+):[^@\s/'\"]+@", re.I), r"\1:***@"),
    (re.compile(r"(?i)\b(authorization|proxy-authorization|x-api-key|x-plex-token|x-chat-token|x-internal-token|x-investigator-token)(\s*[:=]\s*)(bearer\s+|basic\s+|pveapitoken=)?[^\s,;'\"]+"), r"\1\2\3***"),
    (re.compile(r"(?i)([?&](?:apikey|api_key|token|x-plex-token|password|pass|pwd|secret)=)[^&\s'\"<>]+"), r"\1***"),
    (re.compile(r"(?i)(<(\w*(?:key|password|token|secret|pass)\w*)>)[^<]{3,}(</\2>)"), r"\1***\3"),
    (re.compile(r"(?i)(\b[\w.-]*" + SECRET_WORD + r"[\w.-]*[\"']?\s*(?:=|:)\s*)([\"'])[^\"'\n]{2,}?\2"), r"\1\2***\2"),
    # KEY=value, KEY: value, "key": "value", key = value (ini), PlexOnlineToken="value"
    (re.compile(r"(?i)([\"']?\b[\w.-]*" + SECRET_WORD + r"[\w.-]*[\"']?\s*(?:=|:|=>)\s*)([\"']?)([^\s\"',;<>{}\[\]]{3,})"), r"\1\2***"),
]


def mask(text):
    for rx, repl in MASKS:
        text = rx.sub(repl, text)
    return text


class Refused(Exception):
    pass


def num(v, hi):
    if not re.fullmatch(r"\d{1,6}", v or "") or int(v) > hi:
        raise Refused(f"needs a number up to {hi}, got {v!r}")
    return v


def readable(path, prog):
    """A path argument: globbed (no shell here), resolved, inside a root, not a secret file."""
    out = []
    hits = glob.glob(path) if any(c in path for c in "*?[") else [path]
    for p in hits[:200] or [path]:
        real = os.path.realpath(p)
        if real == NFS_ROOT or real.startswith(NFS_ROOT + "/"):
            if prog not in NFS_PROGRAMS:
                raise Refused(f"{prog} may not read under /mnt/nfs (ls, stat, du, find and df may)")
        elif not any(real == r or real.startswith(r + "/") for r in ROOTS):
            raise Refused(f"{p} is outside the readable folders ({', '.join(ROOTS)}, {NFS_ROOT} for listing)")
        if DENY_PATH.search(real) or DENY_PATH.search(p):
            raise Refused(f"{p} holds secrets and is not readable")
        out.append(p)
    return out


def flags(args, allowed, with_value=(), max_values=None):
    """Split args into checked flags and the rest. allowed: flags without a value; with_value:
    flags that take one (separately or as --flag=value). Combined short flags (-tulpn) are split."""
    max_values = max_values or {}
    out, rest, i = [], [], 0
    while i < len(args):
        a = args[i]
        if a == "--":
            rest += args[i + 1:]
            break
        if a.startswith("--"):
            name, eq, val = a.partition("=")
            if name in with_value:
                if not eq:
                    i += 1
                    if i >= len(args):
                        raise Refused(f"{name} needs a value")
                    val = args[i]
                if name in max_values:
                    num(val, max_values[name])
                out += [name, val]
            elif name in allowed and not eq:
                out.append(name)
            else:
                raise Refused(f"flag {a} is not allowed")
        elif a.startswith("-") and len(a) > 1 and not re.fullmatch(r"-\d+", a):
            j = 1
            while j < len(a):
                f = "-" + a[j]
                if f in with_value:
                    val = a[j + 1:] or None
                    if val is None:
                        i += 1
                        if i >= len(args):
                            raise Refused(f"{f} needs a value")
                        val = args[i]
                    if f in max_values:
                        num(val, max_values[f])
                    out += [f, val]
                    break
                if f not in allowed:
                    raise Refused(f"flag {f} is not allowed")
                out.append(f)
                j += 1
        else:
            rest.append(a)
        i += 1
    return out, rest


def paths_cmd(prog, allowed, with_value=(), max_values=None, need=False, anywhere=False):
    def check(args):
        f, rest = flags(args, allowed, with_value, max_values)
        if need and not rest:
            raise Refused(f"{prog} needs a path")
        # df only reports a filesystem's size, so any mount point will do (df -h /).
        ps = [p for p in rest if re.fullmatch(r"/[\w/.-]*", p)] if anywhere else [x for p in rest for x in readable(p, prog)]
        if anywhere and len(ps) != len(rest):
            raise Refused(f"{prog}: plain paths only")
        return [prog] + f + ps
    return check


def grep_cmd(filter_only):
    allowed = {"-i", "-v", "-n", "-c", "-l", "-L", "-h", "-H", "-w", "-x", "-o", "-s", "-E", "-F", "-r", "-R", "-I", "-a", "-z",
               "--ignore-case", "--invert-match", "--line-number", "--count", "--files-with-matches", "--recursive",
               "--extended-regexp", "--fixed-strings", "--only-matching", "--no-messages", "--text"}
    with_value = {"-A", "-B", "-C", "-m", "-e", "--include", "--exclude", "--exclude-dir", "--max-count", "--context"}
    maxv = {"-A": 500, "-B": 500, "-C": 500, "-m": 100000, "--max-count": 100000, "--context": 500}

    def check(args):
        f, rest = flags(args, allowed, with_value, maxv)
        if "-e" not in f:
            if not rest:
                raise Refused("grep needs a pattern")
            f += ["-e", rest.pop(0)]
        if filter_only:
            if rest:
                raise Refused("grep after a pipe reads only the pipe")
            return ["grep"] + f
        if not rest:
            raise Refused("grep needs a path (or use it after a pipe)")
        return ["grep"] + f + [x for p in rest for x in readable(p, "grep")]
    return check


def find_cmd(args):
    tests_v = {"-name", "-iname", "-path", "-ipath", "-type", "-mtime", "-mmin", "-newer", "-size", "-maxdepth",
               "-mindepth", "-user", "-group", "-perm", "-printf", "-regex", "-iregex", "-links", "-uid", "-gid"}
    tests = {"-print", "-print0", "-ls", "-o", "-a", "-or", "-and", "-not", "!", "(", ")", "-prune", "-xdev", "-empty",
             "-mount", "-noleaf", "-follow", "-L", "-H", "-P", "-true", "-false", "-readable", "-depth"}
    starts = [i for i, a in enumerate(args) if a.startswith("-") or a in ("!", "(", ")")]
    first = starts[0] if starts else len(args)
    roots, expr = args[:first], args[first:]
    if not roots:
        raise Refused("find needs a starting folder")
    out, i = [], 0
    while i < len(expr):
        a = expr[i]
        if a in tests_v:
            if i + 1 >= len(expr):
                raise Refused(f"{a} needs a value")
            if a == "-maxdepth" or a == "-mindepth":
                num(expr[i + 1], 50)
            if a == "-newer":
                readable(expr[i + 1], "find")
            out += [a, expr[i + 1]]
            i += 2
            continue
        if a not in tests:
            raise Refused(f"find {a} is not allowed (no -exec, -delete, -fprint, -ok)")
        out.append(a)
        i += 1
    return ["find"] + [x for p in roots for x in readable(p, "find")] + out


def follow_free(prog, allowed, with_value, maxv):
    def check(args):
        f, rest = flags(args, allowed, with_value, maxv)
        return [prog] + f + rest
    return check


def journalctl(args):
    f, rest = flags(args, {"-k", "--dmesg", "-r", "--reverse", "-x", "--no-pager", "--list-boots", "--disk-usage", "-q",
                           "--quiet", "-a", "--all", "--utc", "--no-hostname", "-e", "--pager-end", "-b", "--boot"},
                    {"-u", "--unit", "-n", "--lines", "--since", "--until", "-S", "-U", "-p", "--priority",
                     "-t", "--identifier", "-g", "--grep", "-o", "--output", "-I", "_PID", "--user-unit"},
                    {"-n": 20000, "--lines": 20000})
    for i, a in enumerate(f):
        if a in ("-o", "--output") and f[i + 1] not in ("short", "short-iso", "short-precise", "short-monotonic", "cat", "with-unit", "verbose", "json"):
            raise Refused("journalctl -o: short, short-iso, short-precise, cat, with-unit, verbose or json")
    for r in rest:  # FIELD=value matches (_SYSTEMD_UNIT=docker.service, CONTAINER_NAME=x) only
        if not re.fullmatch(r"_?[A-Z_]+=[\w@.:-]+|\+|-?\d{1,3}", r):
            raise Refused(f"journalctl argument {r!r} is not allowed")
    if not any(x in f for x in ("-n", "--lines", "--since", "-S", "--list-boots", "--disk-usage")):
        f += ["-n", "300"]
    return ["journalctl", "--no-pager"] + f + rest


def systemctl(args):
    f, rest = flags(args, {"--no-pager", "--all", "-a", "--failed", "-l", "--full", "--no-legend", "--plain", "-q", "--quiet"},
                    {"--type", "-t", "--state", "-p", "--property", "-n", "--lines", "--output", "-o"}, {"-n": 2000, "--lines": 2000})
    sub = rest[0] if rest else "list-units"
    if sub not in ("status", "list-units", "list-timers", "list-unit-files", "list-sockets", "is-active", "is-enabled",
                   "is-failed", "show", "cat", "list-dependencies", "--failed"):
        raise Refused(f"systemctl {sub} is not allowed (status, list-*, is-*, show, cat)")
    for u in rest[1:]:
        if not re.fullmatch(r"[\w@.:*-]+", u):
            raise Refused(f"bad unit name {u!r}")
    return ["systemctl", "--no-pager"] + f + rest


CONTAINER = r"[\w.-]+"


def docker(args):
    if not args:
        raise Refused("docker needs a subcommand")
    sub, rest = args[0], args[1:]
    if sub == "ps":
        f, r = flags(rest, {"-a", "--all", "-s", "--size", "--no-trunc", "-q", "--quiet", "-l", "--latest"}, {"--filter", "-f", "--format", "-n", "--last"}, {"-n": 500, "--last": 500})
        if r:
            raise Refused("docker ps takes flags only")
        return ["docker", "ps"] + f
    if sub in ("logs",):
        f, r = flags(rest, {"-t", "--timestamps", "--details"}, {"--tail", "-n", "--since", "--until"}, {"--tail": 20000, "-n": 20000})
        if len(r) != 1 or not re.fullmatch(CONTAINER, r[0]):
            raise Refused("docker logs needs one container name (no -f)")
        if not any(x in f for x in ("--tail", "-n", "--since")):
            f += ["--tail", "300"]
        return ["docker", "logs"] + f + r
    if sub == "inspect":
        f, r = flags(rest, {"-s", "--size"}, {"--format", "-f", "--type"})
        if not r or not all(re.fullmatch(r"[\w.:/@-]+", x) for x in r):
            raise Refused("docker inspect needs container, image, network or volume names")
        return ["docker", "inspect"] + f + r
    if sub == "stats":
        f, r = flags(rest, {"-a", "--all", "--no-trunc", "--no-stream"}, {"--format"})
        if not all(re.fullmatch(CONTAINER, x) for x in r):
            raise Refused("docker stats takes container names")
        return ["docker", "stats", "--no-stream"] + [x for x in f if x != "--no-stream"] + r
    if sub in ("images", "image") and (sub == "images" or (rest and rest[0] in ("ls", "inspect", "history"))):
        if sub == "image":
            sub2, rest = rest[0], rest[1:]
            f, r = flags(rest, {"-a", "--all", "--no-trunc", "-q", "--digests", "-H", "--human"}, {"--format", "-f", "--filter"})
            return ["docker", "image", sub2] + f + r
        f, r = flags(rest, {"-a", "--all", "--no-trunc", "-q", "--digests"}, {"--format", "-f", "--filter"})
        return ["docker", "images"] + f + r
    if sub in ("info", "version"):
        f, r = flags(rest, set(), {"--format", "-f"})
        return ["docker", sub] + f
    if sub in ("top", "port", "diff"):
        if not rest or not re.fullmatch(CONTAINER, rest[0]) or len(rest) > 2:
            raise Refused(f"docker {sub} needs one container name")
        return ["docker", sub] + rest[:1] + ([rest[1]] if sub == "port" and len(rest) > 1 and re.fullmatch(r"\d+(/\w+)?", rest[1]) else [])
    if sub in ("network", "volume") and rest and rest[0] in ("ls", "inspect"):
        f, r = flags(rest[1:], {"-q", "--no-trunc"}, {"--format", "-f", "--filter"})
        if not all(re.fullmatch(r"[\w.-]+", x) for x in r):
            raise Refused("bad name")
        return ["docker", sub, rest[0]] + f + r
    if sub == "system" and rest[:1] == ["df"]:
        f, r = flags(rest[1:], {"-v", "--verbose"}, {"--format"})
        return ["docker", "system", "df"] + f
    if sub == "compose":
        f, r = flags(rest, set(), {"-f", "--file", "-p", "--project-name", "--project-directory"})
        for i in range(0, len(f), 2):
            if f[i] in ("-f", "--file", "--project-directory"):
                readable(f[i + 1], "compose")
            elif not re.fullmatch(r"[\w-]+", f[i + 1]):
                raise Refused("bad project name")
        verb = r[0] if r else ""
        if verb not in ("ls", "ps", "images", "top", "config", "version"):
            raise Refused("docker compose: ls, ps, images, top, config or version only")
        g, extra = flags(r[1:], {"-a", "--all", "-q", "--services", "--volumes", "--images", "--no-interpolate", "--no-trunc"}, {"--format", "--filter", "--status"})
        if not all(re.fullmatch(r"[\w-]+", x) for x in extra):
            raise Refused("bad service name")
        return ["docker", "compose"] + f + [verb] + g + extra
    raise Refused(f"docker {sub} is not allowed (ps, logs, inspect, stats, images, info, version, top, port, diff, "
                  "network/volume ls|inspect, system df, compose ls|ps|images|top|config)")


def ip_cmd(args):
    opts = {"-br", "-brief", "-4", "-6", "-s", "-d", "-c", "-j", "-p", "-details", "-json", "-pretty", "-stats"}
    f = [a for a in args if a in opts]
    rest = [a for a in args if a not in opts]
    if any(a.startswith("-") for a in rest):
        raise Refused("ip: flags -br, -4, -6, -s, -d, -j only")
    if not rest or rest[0] not in ("addr", "a", "address", "route", "r", "link", "l", "neigh", "n", "rule", "-br"):
        raise Refused("ip: addr, route, link, neigh or rule (show only)")
    if any(x not in ("show", "list", "ls", "dev", "table", "all") and not re.fullmatch(r"[\w.:/-]+", x) for x in rest[1:]) or \
            any(x in ("add", "del", "delete", "set", "flush", "change", "replace", "append") for x in rest[1:]):
        raise Refused("ip: show only")
    return ["ip"] + f + rest


def curl_cmd(args):
    f, rest = flags(args, {"-s", "--silent", "-S", "--show-error", "-i", "--include", "-I", "--head", "-k", "--insecure",
                           "-L", "--location", "-v", "--compressed", "-f", "--fail", "-4", "-6"},
                    {"-m", "--max-time", "--connect-timeout", "-w", "--write-out", "-H", "--header", "-A", "--user-agent"},
                    {"-m": 60, "--max-time": 60, "--connect-timeout": 30})
    if len(rest) != 1:
        raise Refused("curl needs exactly one URL (GET only; no -X, -d, -o, -T, -F)")
    u = urllib.parse.urlsplit(rest[0])
    host = (u.hostname or "").lower()
    ok = u.scheme in ("http", "https") and (host in ("localhost", "127.0.0.1", "::1") or host.startswith("192.168.1.")
                                             or host.endswith(".ts.net") or host.startswith("172.") or re.fullmatch(r"[a-z0-9-]+", host or "-"))
    if not ok:
        raise Refused("curl only to the lab: localhost, 192.168.1.x, 172.x (Docker), container names or *.ts.net")
    if not any(x in f for x in ("-m", "--max-time")):
        f += ["--max-time", "20"]
    return ["curl", "-sS"] + f + [rest[0]]


def app_cmd(args):
    """app <name> <path>: a GET to one of the VM's apps with its own API key, read from its config
    here (the key never leaves the VM). SABnzbd only for read modes."""
    if len(args) != 2 or not args[1].startswith("/"):
        raise Refused("app <sonarr|radarr|prowlarr|lidarr|sabnzbd|jellyseerr|plex|bazarr> </path?query>")
    name, path = args
    if not re.fullmatch(r"/[\w/.%=&?,:+-]*", path) or ".." in path:
        raise Refused("bad path")

    def xml_key(app):
        try:
            return re.search(r"<ApiKey>([^<]+)</ApiKey>", open(f"{APPDATA}/{app}/config.xml").read()).group(1)
        except (OSError, AttributeError):
            raise Refused(f"{app} is not on this VM (no {APPDATA}/{app}/config.xml)")
    ports = {"sonarr": 8989, "radarr": 7878, "prowlarr": 9696, "lidarr": 8686}
    if name in ports:
        return {"url": f"http://127.0.0.1:{ports[name]}{path}", "headers": {"X-Api-Key": xml_key(name)}}
    if name == "sabnzbd":
        q = urllib.parse.parse_qs(urllib.parse.urlsplit(path).query)
        if q.get("mode", [""])[0] not in ("queue", "history", "warnings", "version", "status", "fullstatus", "server_stats", "get_cats", "get_scripts"):
            raise Refused("sabnzbd: read modes only (queue, history, warnings, version, status, fullstatus, server_stats)")
        if q.get("name", [""])[0] not in ("",) or any(k in q for k in ("value", "del_files")):
            raise Refused("sabnzbd: no name= or value= (those change things)")
        try:
            key = re.search(r"^api_key\s*=\s*(\S+)", open(f"{APPDATA}/sabnzbd/sabnzbd.ini").read(), re.M).group(1)
        except (OSError, AttributeError):
            raise Refused("sabnzbd is not on this VM")
        sep = "&" if "?" in path else "?"
        return {"url": f"http://127.0.0.1:8080{path}{sep}output=json&apikey={key}", "headers": {}}
    if name == "jellyseerr":
        try:
            key = json.load(open(f"{APPDATA}/jellyseerr/settings.json"))["main"]["apiKey"]
        except (OSError, KeyError, ValueError):
            raise Refused("jellyseerr is not on this VM")
        return {"url": f"http://127.0.0.1:5055{path}", "headers": {"X-Api-Key": key}}
    if name == "bazarr":
        try:
            key = re.search(r"^auth:.*?^\s+apikey:\s*'?([^'\s]+)", open(f"{APPDATA}/bazarr/config/config.yaml").read(), re.M | re.S).group(1)
        except (OSError, AttributeError):
            raise Refused("bazarr is not on this VM")
        return {"url": f"http://127.0.0.1:6767{path}", "headers": {"X-API-KEY": key}}
    if name == "plex":
        try:
            prefs = open(f"{APPDATA}/plex/Library/Application Support/Plex Media Server/Preferences.xml").read()
            key = re.search(r'PlexOnlineToken="([^"]+)"', prefs).group(1)
        except (OSError, AttributeError):
            raise Refused("plex is not on this VM")
        return {"url": f"http://127.0.0.1:32400{path}", "headers": {"X-Plex-Token": key, "Accept": "application/json"}}
    raise Refused(f"unknown app {name}")


def simple(prog, allowed=frozenset(), with_value=frozenset(), maxv=None, args_ok=None):
    def check(args):
        f, rest = flags(args, set(allowed), set(with_value), maxv)
        if rest and not (args_ok and all(re.fullmatch(args_ok, r) for r in rest)):
            raise Refused(f"{prog} takes {'only flags' if not args_ok else 'these arguments: ' + args_ok}")
        return [prog] + f + rest
    return check


PROGRAMS = {
    "journalctl": journalctl,
    "systemctl": systemctl,
    "docker": docker,
    "cat": paths_cmd("cat", {"-n", "-A", "-v"}, need=True),
    "head": paths_cmd("head", {"-q"}, {"-n", "-c", "--lines", "--bytes"}, {"-n": 100000, "-c": 10**7}, need=True),
    "tail": paths_cmd("tail", {"-q"}, {"-n", "-c", "--lines", "--bytes"}, {"-n": 100000, "-c": 10**7}, need=True),
    "ls": paths_cmd("ls", set("-" + c for c in "lahtrRSd1iFnUXv") | {"--full-time", "--color=never", "--time-style=long-iso"}),
    "stat": paths_cmd("stat", {"-L", "-t"}, {"-c", "--format"}, need=True),
    "wc": paths_cmd("wc", {"-l", "-c", "-w", "-m"}, need=True),
    "du": paths_cmd("du", {"-s", "-h", "-x", "-a", "-c", "-k", "-m", "--apparent-size"}, {"-d", "--max-depth"}, {"-d": 10, "--max-depth": 10}, need=True),
    "df": paths_cmd("df", {"-h", "-i", "-T", "-k", "-m", "-l", "-P", "-H"}, {"-t", "--type", "-x"}, anywhere=True),
    "readlink": paths_cmd("readlink", {"-f", "-e"}, need=True),
    "realpath": paths_cmd("realpath", set(), need=True),
    "md5sum": paths_cmd("md5sum", set(), need=True),
    "sha256sum": paths_cmd("sha256sum", set(), need=True),
    "file": paths_cmd("file", {"-b", "-L"}, need=True),
    "grep": grep_cmd(False),
    "find": find_cmd,
    "free": simple("free", {"-h", "-m", "-g", "-w", "-t", "-k"}),
    "uptime": simple("uptime", {"-p", "-s"}),
    "uname": simple("uname", {"-a", "-r", "-m", "-n", "-v"}),
    "hostname": simple("hostname", {"-I", "-f"}),
    "date": simple("date", {"-u", "-R", "-I"}),
    "nproc": simple("nproc"),
    "lscpu": simple("lscpu"),
    "lspci": simple("lspci", {"-k", "-nn", "-v", "-vv"}, {"-s"}),
    "lsusb": simple("lsusb", {"-t", "-v"}),
    "lsblk": simple("lsblk", {"-f", "-a", "-b", "-d", "-p", "-J", "-l", "-m"}, {"-o", "--output"}),
    "findmnt": simple("findmnt", {"-l", "-J", "-D", "--df", "-A", "-n"}, {"-t", "-T", "-o", "--target", "--types"}, args_ok=r"/[\w/.-]*"),
    "mount": simple("mount"),
    "ss": simple("ss", set("-" + c for c in "tulpnaeiosHx4") | {"--summary"}),
    "ip": ip_cmd,
    "ps": simple("ps", {"-e", "-f", "-F", "-A", "-H", "-L", "-ww", "aux", "auxf", "axo"}, {"-o", "--sort", "-p", "-C", "-u", "-eo"}, args_ok=r"aux|auxf|auxww|axf|[\w,%:=+-]+"),
    "top": lambda a: ["top", "-b", "-n", "1", "-w", "200"] + flags(a, {"-c", "-H", "-i"}, {"-o", "-p", "-u"})[0],
    "vmstat": simple("vmstat", {"-w", "-s", "-d", "-a", "-t"}, args_ok=r"[1-9]|10"),
    "iostat": simple("iostat", {"-x", "-z", "-k", "-m", "-d", "-c", "-N", "-t"}, args_ok=r"[1-9]|10"),
    "dmesg": simple("dmesg", {"-T", "-k", "-x", "-H", "--ctime", "--notime", "-t", "-L", "--color=never"}, {"-l", "--level", "-f", "--facility"}),
    "timedatectl": simple("timedatectl", args_ok=r"status|show|timesync-status|show-timesync"),
    "getent": simple("getent", args_ok=r"hosts|ahosts|passwd|group|services|[\w.@-]+"),
    "id": simple("id", args_ok=r"[\w-]+"),
    "who": simple("who", {"-b", "-a", "-r"}),
    "last": simple("last", {"-x", "-F", "-w"}, {"-n"}, {"-n": 500}),
    "ping": simple("ping", {"-4", "-6", "-q", "-n"}, {"-c", "-W", "-w", "-i"}, {"-c": 5, "-W": 5, "-w": 15}, args_ok=r"[\w.:-]+"),
    "nfsstat": simple("nfsstat", {"-m", "-c", "-s", "-v", "-l", "-r", "-n", "-o", "-3", "-4"}),
    "apt": lambda a: (["apt", "list"] + flags(a[1:], {"--upgradable", "--installed", "-a"}, set())[0] + [x for x in a[1:] if not x.startswith("-")]
                      if a[:1] == ["list"] and all(re.fullmatch(r"-[-\w]+|[\w.*+-]+", x) for x in a[1:]) else (_ for _ in ()).throw(Refused("apt: list only"))),
    "apt-cache": simple("apt-cache", args_ok=r"policy|show|[\w.+-]+"),
    "dpkg": simple("dpkg", {"-l"}, {"-s", "-L"}, args_ok=r"[\w.*+-]+"),
    "crontab": lambda a: ["crontab", "-l"] if a == ["-l"] else (_ for _ in ()).throw(Refused("crontab -l only")),
    "sensors": simple("sensors"),
    "zcat": paths_cmd("zcat", {"-f"}, need=True),
    "curl": curl_cmd,
}
FILTERS = {
    "grep": grep_cmd(True),
    "head": simple("head", set(), {"-n", "-c"}, {"-n": 100000, "-c": 10**7}, args_ok=r"-?\d+"),
    "tail": simple("tail", set(), {"-n", "-c"}, {"-n": 100000, "-c": 10**7}, args_ok=r"-?\d+"),
    "wc": simple("wc", {"-l", "-c", "-w", "-m"}),
    "sort": simple("sort", {"-r", "-n", "-u", "-h", "-V", "-f", "-b"}, {"-k", "-t"}),
    "uniq": simple("uniq", {"-c", "-d", "-u", "-i"}),
    "cut": simple("cut", {"-s"}, {"-d", "-f", "-c", "-b"}),
    "tr": simple("tr", {"-d", "-s"}, args_ok=r"[\w :,.\\-]{1,20}"),
    "column": simple("column", {"-t"}, {"-s"}),
}
SPECIAL = {"app", "tcp"}


def split(line):
    lex = shlex.shlex(line, posix=True, punctuation_chars="|;&<>`")
    lex.whitespace_split = True
    lex.commenters = ""
    toks = list(lex)
    stages, cur = [], []
    for t in toks:
        if t == "|":
            stages.append(cur)
            cur = []
        elif t and set(t) <= set(";&<>|`"):
            raise Refused(f"{t!r} is not allowed: one command, optionally piped into grep/head/tail/wc/sort/uniq/cut")
        else:
            cur.append(t)
    stages.append(cur)
    if any(not s for s in stages):
        raise Refused("empty command")
    if "$(" in line or "${" in line:
        raise Refused("no shell expansion here")
    return stages


def plan(line):
    stages = split(line)
    first, rest = stages[0], stages[1:]
    prog = first[0]
    if prog in SPECIAL:
        if rest:
            raise Refused(f"{prog} cannot be piped")
        return prog, (app_cmd(first[1:]) if prog == "app" else first[1:])
    if prog not in PROGRAMS:
        raise Refused(f"{prog} is not on the read-only list: {', '.join(sorted(PROGRAMS))}, app, tcp")
    if len(rest) > 4:
        raise Refused("at most four pipes")
    cmds = [PROGRAMS[prog](first[1:])]
    for s in rest:
        if s[0] not in FILTERS:
            raise Refused(f"after a pipe only {', '.join(sorted(FILTERS))}")
        cmds.append(FILTERS[s[0]](s[1:]))
    return "run", cmds


def cut(text):
    if len(text) <= OUT_MAX:
        return text
    return text[:6000] + f"\n...({len(text) - OUT_MAX} characters cut)...\n" + text[-(OUT_MAX - 6000):]


def execute(kind, payload):
    if kind == "tcp":
        if len(payload) != 2 or not re.fullmatch(r"[\w.:-]+", payload[0]) or not re.fullmatch(r"\d{1,5}", payload[1]):
            raise Refused("tcp <host> <port>")
        try:
            with socket.create_connection((payload[0], int(payload[1])), timeout=4):
                return f"tcp {payload[0]}:{payload[1]} open\n", 0
        except OSError as e:
            return f"tcp {payload[0]}:{payload[1]} closed or unreachable: {e}\n", 1
    if kind == "app":
        req = payload
        cmd = ["curl", "-sS", "-i", "--max-time", "20", "-K", "-"]
        conf = f'url = "{req["url"]}"\n' + "".join(f'header = "{k}: {v}"\n' for k, v in req["headers"].items())
        r = subprocess.run(cmd, input=conf, capture_output=True, text=True, timeout=RUN_TIMEOUT)
        return r.stdout + r.stderr, r.returncode
    procs, prev = [], None
    env = {"PATH": "/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin", "LANG": "C.UTF-8", "SYSTEMD_PAGER": "", "PAGER": "cat", "COLUMNS": "200", "TERM": "dumb"}
    for c in payload:
        p = subprocess.Popen(c, stdin=prev.stdout if prev else subprocess.DEVNULL, stdout=subprocess.PIPE,
                             stderr=subprocess.STDOUT, env=env, cwd="/")
        if prev:
            prev.stdout.close()
        procs.append(p)
        prev = p
    try:
        out, _ = procs[-1].communicate(timeout=RUN_TIMEOUT)
    except subprocess.TimeoutExpired:
        for p in procs:
            p.kill()
        out = (procs[-1].communicate()[0] or b"") + f"\n(stopped after {RUN_TIMEOUT} s)\n".encode()
    for p in procs[:-1]:
        try:
            p.wait(timeout=5)
        except subprocess.TimeoutExpired:
            p.kill()
    return out.decode("utf-8", "replace"), procs[-1].returncode


def main():
    line = " ".join(sys.argv[1:]).strip()
    # n8n's and some SSH clients' "cd / ; " prefix is not ours to keep.
    line = re.sub(r"^cd '?/'? ; ", "", line)
    subprocess.run(["logger", "-t", "homelab-inspect", line[:600]], check=False)
    try:
        if not line:
            raise Refused("empty command")
        if len(line) > 2000:
            raise Refused("command too long")
        kind, payload = plan(line)
        text, rc = execute(kind, payload)
    except Refused as e:
        print(f"refused: {e}")
        sys.exit(2)
    except (FileNotFoundError, PermissionError) as e:
        print(f"error: {e}")
        sys.exit(1)
    sys.stdout.write(mask(cut(text)))
    if rc not in (0, None):
        print(f"\n(exit code {rc})")
    sys.exit(0)


if __name__ == "__main__":
    main()
