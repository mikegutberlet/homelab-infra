"""Keep MyAnonamouse happy, once an hour (docs/books.md, "MyAnonamouse").

Runs inside gluetun's network namespace, so its IP is PIA's and qBittorrent answers on localhost
without a login. Two jobs:

1. Session IP: a MAM session (mam_id cookie) is locked to one IP and PIA's changes on reconnect.
   GET https://t.myanonamouse.net/json/dynamicSeedbox.php moves the session to the caller's IP
   ("Completed") or answers "No change". The cookie jar /data/cookies.txt keeps the current id
   (MAM may rotate it); MAM_ID only seeds the jar while it holds none.
2. Seeding: MAM counts a torrent as satisfied after SEED_HOURS of seeding (72 h), ratio aside, and
   qBittorrent's global rule (ratio 1.0 or 3 days, or 12 h without an upload) would stop earlier.
   Every torrent whose tracker is MAM gets its own share limits: no ratio cap, no inactivity cap,
   SEED_HOURS of seeding, then qBittorrent pauses it and Chaptarr/Listenarr remove it as usual.
   Other torrents keep the global rule.

ONCE=true runs both once and exits. DRY_RUN=true reports the share limits it would set.
"""
import http.cookiejar
import json
import os
import sys
import time
import urllib.parse
import urllib.request

JAR = os.environ.get("JAR", "/data/cookies.txt")
MAM = os.environ.get("MAM", "https://t.myanonamouse.net/json/dynamicSeedbox.php")
QBIT = os.environ.get("QBIT", "http://127.0.0.1:8085/api/v2")
SEED_HOURS = int(os.environ.get("SEED_HOURS", "72"))
INTERVAL_MIN = int(os.environ.get("INTERVAL_MIN", "60"))
DRY_RUN = os.environ.get("DRY_RUN", "false").lower() == "true"


def log(msg):
    print(f"{time.strftime('%F %T')} {msg}", flush=True)


def session_ip():
    jar = http.cookiejar.MozillaCookieJar(JAR)
    if os.path.exists(JAR):
        jar.load(ignore_discard=True)
    if not any(c.name == "mam_id" and c.value for c in jar):
        mam_id = os.environ.get("MAM_ID", "")
        if not mam_id:
            log("MAM_ID is empty: no MyAnonamouse session yet (docs/books.md)")
            return
        jar.set_cookie(http.cookiejar.Cookie(0, "mam_id", mam_id, None, False, ".myanonamouse.net", True, True,
                                             "/", False, True, None, False, None, None, {}))
    opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(jar))
    with opener.open(MAM, timeout=30) as resp:
        log(f"session: {resp.read().decode()[:200]}")
    jar.save(ignore_discard=True)


def qbit(path, data=None):
    req = urllib.request.Request(f"{QBIT}/{path}", data=urllib.parse.urlencode(data).encode() if data else None)
    with urllib.request.urlopen(req, timeout=60) as resp:
        body = resp.read()
    return json.loads(body) if body else None


def seed_limits():
    want = {"ratioLimit": -1, "seedingTimeLimit": SEED_HOURS * 60, "inactiveSeedingTimeLimit": -1}
    todo = [t for t in qbit("torrents/info")
            if "myanonamouse" in (t.get("tracker") or "")
            and (t.get("ratio_limit"), t.get("seeding_time_limit"), t.get("inactive_seeding_time_limit"))
            != (-1, want["seedingTimeLimit"], -1)]
    for t in todo:
        log(f"seeding: {SEED_HOURS} h, no ratio cap: {t['name']}")
    if todo and not DRY_RUN:
        qbit("torrents/setShareLimits", {"hashes": "|".join(t["hash"] for t in todo), **want})
    log(f"seeding: {len(todo)} MAM torrents set{' (dry run)' if DRY_RUN else ''}")


def main():
    while True:
        for job in (session_ip, seed_limits):
            try:
                job()
            except Exception as exc:  # keep running; next hour retries
                print(f"{job.__name__}: failed: {exc}", file=sys.stderr, flush=True)
        if os.environ.get("ONCE", "false").lower() == "true":
            return
        time.sleep(INTERVAL_MIN * 60)


if __name__ == "__main__":
    main()
