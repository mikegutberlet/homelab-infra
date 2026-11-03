"""Daily upkeep for the Tdarr HEVC run, so it needs no hands (Goobs, 2026-11-02).

1. Maps. Writes radarr_map.json and sonarr_map.json ({path: {id, lang, title}} for every current
   Radarr/Sonarr file) to /mnt/nfs/Media/.tdarr-staging/maps/, where the flow on both Tdarr nodes
   reads them (stacks/gpu/tdarr-flow/flow.js). They used to be copied by hand and were 11 days old on
   2026-11-02, so files imported or renamed since then were skipped, and Tdarr never looks at a
   skipped file again. For that reason a map that shrinks by more than MAX_SHRINK against the one on
   disk is not written (an API hiccup would otherwise mark most of a library "done").
2. Purge. Deletes Tdarr originals held in /mnt/nfs/to-delete/tdarr-originals/<YYYY-MM-DD>/ once that
   day is KEEP_DAYS old. The flow moves an original there only after its replacement passed the size
   and full-decode checks. A held file is deleted only if the day's MANIFEST.tsv lists it with the
   same size, and its replacement either exists at the listed path or did reach it (no copy of it is
   left in .tdarr-staging; Sonarr/Radarr may have renamed or upgraded it since). When the flow's
   final rename fails, the replacement stays in .tdarr-staging as <kind>-<id>-<name> and the held
   original is the only whole copy, so it is kept. Everything kept is logged and counted. Each
   deleted file is appended to tdarr-originals/PURGED.tsv. Nothing outside tdarr-originals is
   touched; the other to-delete folders are Goobs's.

Runs once at start, then daily at HOUR (local time). DRY_RUN=true logs the purge without deleting.
"""

import json
import os
import re
import sys
import time
import urllib.request
from datetime import date, datetime, timedelta

RADARR, SONARR = "http://radarr:7878", "http://sonarr:8989"
STAGING = "/mnt/nfs/Media/.tdarr-staging"
MAPS = f"{STAGING}/maps"
HOLD_ROOT = "/mnt/nfs/to-delete/tdarr-originals"
LIB_ROOTS = {"/mnt/nfs/Media/Movies/": "", "/mnt/nfs/Media/TVShows/": "TVShows/"}  # flow.js LIBS[].hold
KEEP_DAYS = int(os.environ.get("KEEP_DAYS", "14"))
HOUR = int(os.environ.get("HOUR", "5"))
MAX_SHRINK = 0.2
DRY_RUN = os.environ.get("DRY_RUN", "false").lower() == "true"


def api(base, key, path):
    req = urllib.request.Request(f"{base}/api/v3/{path}", headers={"X-Api-Key": key})
    with urllib.request.urlopen(req, timeout=600) as resp:
        return json.load(resp)


def lang(x):
    return (x.get("originalLanguage") or {}).get("name", "Unknown")


def write_map(name, data):
    path = os.path.join(MAPS, name)
    try:
        with open(path) as fh:
            old = len(json.load(fh))
    except (OSError, ValueError):
        old = 0
    if old and len(data) < old * (1 - MAX_SHRINK):
        print(f"maps: {name} not written: {len(data)} files against {old} on disk", file=sys.stderr, flush=True)
        return
    tmp = f"{path}.tmp"
    with open(tmp, "w") as fh:
        json.dump(data, fh)
    os.replace(tmp, path)
    print(f"maps: {name} {len(data)} files (was {old})", flush=True)


def maps():
    os.makedirs(MAPS, exist_ok=True)
    key = os.environ["RADARR_API_KEY"]
    movies = {}
    for m in api(RADARR, key, "movie"):
        f = m.get("movieFile")
        if m.get("hasFile") and f:
            movies[f"{m['path']}/{f['relativePath']}"] = {"id": m["id"], "lang": lang(m), "title": f"{m['title']} ({m['year']})"}
    write_map("radarr_map.json", movies)
    key = os.environ["SONARR_API_KEY"]
    episodes = {}
    for s in api(SONARR, key, "series"):
        for f in api(SONARR, key, f"episodefile?seriesId={s['id']}"):
            episodes[f["path"]] = {"id": s["id"], "lang": lang(s), "title": s["title"]}
    write_map("sonarr_map.json", episodes)


def manifest(day_dir):
    """held path -> (size, replacement path), from the flow's MANIFEST.tsv (size, original, replacement)."""
    held = {}
    try:
        with open(os.path.join(day_dir, "MANIFEST.tsv")) as fh:
            for line in fh:
                parts = line.rstrip("\n").split("\t")
                if len(parts) != 3 or not parts[0].isdigit():
                    continue
                size, src, dest = int(parts[0]), parts[1], parts[2]
                for root, hold in LIB_ROOTS.items():
                    if src.startswith(root):
                        held[os.path.join(day_dir, hold + src[len(root):])] = (size, dest)
    except OSError:
        pass
    return held


def purge():
    cutoff = date.today() - timedelta(days=KEEP_DAYS)
    staged = [f for f in os.listdir(STAGING) if os.path.isfile(os.path.join(STAGING, f))]
    for name in sorted(os.listdir(HOLD_ROOT)):
        day_dir = os.path.join(HOLD_ROOT, name)
        if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", name) or os.path.islink(day_dir) or not os.path.isdir(day_dir):
            continue
        if date.fromisoformat(name) > cutoff:
            continue
        listed = manifest(day_dir)
        gone = kept = freed = 0
        log = open(os.path.join(HOLD_ROOT, "PURGED.tsv"), "a") if not DRY_RUN else None
        for dirpath, _dirs, files in os.walk(day_dir, topdown=False):
            for f in files:
                p = os.path.join(dirpath, f)
                if p == os.path.join(day_dir, "MANIFEST.tsv") or os.path.islink(p):
                    continue
                entry = listed.get(p)
                size = os.path.getsize(p)
                stuck = entry and not os.path.isfile(entry[1]) and any(
                    f.endswith(f"-{os.path.basename(entry[1])}") for f in staged)
                if not entry or entry[0] != size or stuck:
                    kept += 1
                    why = "not in MANIFEST.tsv" if not entry else "size differs" if entry[0] != size else "replacement still in .tdarr-staging"
                    print(f"purge: kept {p} ({why})", flush=True)
                    continue
                if log:
                    os.remove(p)
                    log.write(f"{datetime.now().isoformat(timespec='seconds')}\t{size}\t{p}\t{entry[1]}\n")
                gone += 1
                freed += size
            if not DRY_RUN and dirpath != day_dir:
                try:
                    os.rmdir(dirpath)  # only succeeds when empty
                except OSError:
                    pass
        if log:
            log.close()
        if not DRY_RUN and kept == 0:
            try:
                os.remove(os.path.join(day_dir, "MANIFEST.tsv"))
            except OSError:
                pass
            try:
                os.rmdir(day_dir)
            except OSError:
                pass
        print(f"purge: {name}: {'would delete' if DRY_RUN else 'deleted'} {gone} files, {freed / 1e9:.1f} GB; kept {kept}", flush=True)


def main():
    first = True
    while True:
        if not first:
            now = datetime.now()
            nxt = now.replace(hour=HOUR, minute=0, second=0, microsecond=0)
            if nxt <= now:
                nxt += timedelta(days=1)
            print(f"next pass at {nxt:%Y-%m-%d %H:%M}", flush=True)
            time.sleep((nxt - now).total_seconds())
        first = False
        for job in (maps, purge):
            try:
                job()
            except Exception as exc:  # keep running; tomorrow retries
                print(f"{job.__name__}: failed: {exc}", file=sys.stderr, flush=True)


if __name__ == "__main__":
    main()
