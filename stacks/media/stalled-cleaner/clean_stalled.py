"""Remove dead torrents from Radarr/Sonarr so they search again.

Public torrents often have no seeders by the time they are grabbed (Prowlarr's 3-seeder minimum
is a tracker scrape, often stale). qBittorrent then waits forever on the magnet's metadata, and the
dead torrents fill its 5 download slots so healthy ones never start (2026-10-27: 62 of 80
unfinished torrents were dead, some for a week). Every 6 hours this removes any torrent in the
Radarr/Sonarr queue that is older than STALL_HOURS and has not downloaded a single byte, deletes
it from qBittorrent, blocklists that release and lets the app search again (skipRedownload=false),
so the next pick can be Usenet or a torrent that has seeders. Usenet items and anything with
progress are never touched. At most MAX_PER_RUN removals per app per pass.
"""

import json
import os
import sys
import time
import urllib.request
from datetime import datetime, timedelta, timezone

APPS = [("radarr", "http://radarr:7878", "RADARR_API_KEY"), ("sonarr", "http://sonarr:8989", "SONARR_API_KEY")]
STALL_HOURS = int(os.environ.get("STALL_HOURS", "48"))
MAX_PER_RUN = int(os.environ.get("MAX_PER_RUN", "20"))
DRY_RUN = os.environ.get("DRY_RUN", "false").lower() == "true"


def api(base, key, path, method="GET"):
    req = urllib.request.Request(f"{base}/api/v3/{path}", method=method, headers={"X-Api-Key": key})
    with urllib.request.urlopen(req, timeout=120) as resp:
        data = resp.read()
    return json.loads(data) if data else None


def dead(item, cutoff):
    if item.get("protocol") != "torrent" or not item.get("added"):
        return False
    added = datetime.fromisoformat(item["added"].replace("Z", "+00:00"))
    size, left = item.get("size") or 0, item.get("sizeleft") or 0
    return added < cutoff and (size == 0 or left >= size)


def clean_app(name, base, key_env):
    key = os.environ[key_env]
    cutoff = datetime.now(timezone.utc) - timedelta(hours=STALL_HOURS)
    queue = api(base, key, "queue?pageSize=1000&includeUnknownMovieItems=false&includeUnknownSeriesItems=false")
    # A season pack is one download but one queue record per episode; remove each download once.
    seen, todo = set(), []
    for item in queue.get("records", []):
        if dead(item, cutoff) and item.get("downloadId") not in seen:
            seen.add(item.get("downloadId"))
            todo.append(item)
    for item in todo[:MAX_PER_RUN]:
        print(f"{name}: dead since {item['added'][:16]}Z: {item['title']}", flush=True)
        if not DRY_RUN:
            api(base, key, f"queue/{item['id']}?removeFromClient=true&blocklist=true&skipRedownload=false", "DELETE")
    print(f"{name}: {min(len(todo), MAX_PER_RUN)} removed of {len(todo)} dead"
          f"{' (dry run)' if DRY_RUN else ''}", flush=True)


def main():
    while True:
        for app in APPS:
            try:
                clean_app(*app)
            except Exception as exc:  # keep running; the next pass retries
                print(f"{app[0]}: failed: {exc}", file=sys.stderr, flush=True)
        if os.environ.get("ONCE", "false").lower() == "true":
            return
        time.sleep(6 * 3600)


if __name__ == "__main__":
    main()
