"""Search for missing movies and episodes a few at a time, every day.

Radarr and Sonarr find new releases through RSS only; a title that was missing when it was added
(or whose first grab failed) is searched again only when someone clicks Search. 2026-10-28: 226
monitored, released movies and the open Jellyseerr requests behind them had never been searched
again. Once a day at HOUR (local time) this searches the MOVIES_PER_DAY missing, available movies
and the EPISODES_PER_DAY missing, aired episodes that were searched longest ago (never first), so
the whole backlog is cycled slowly instead of hammering the indexers. The apps' own rules still
decide what is grabbed (profiles, custom formats, delay profile, minimum availability).

Skipped: anything tagged `no-auto-search` (e.g. a film kept at a bad copy until its real release),
episodes of daily series (talk shows, news; thousands of old episodes nobody wants searched).
DRY_RUN=true logs only; ONCE=true runs a single pass now and exits.
"""

import json
import os
import sys
import time
import urllib.request
from datetime import datetime, timedelta, timezone

RADARR, SONARR = "http://radarr:7878", "http://sonarr:8989"
MOVIES_PER_DAY = int(os.environ.get("MOVIES_PER_DAY", "20"))
EPISODES_PER_DAY = int(os.environ.get("EPISODES_PER_DAY", "30"))
HOUR = int(os.environ.get("HOUR", "4"))
SKIP_TAG = "no-auto-search"
DRY_RUN = os.environ.get("DRY_RUN", "false").lower() == "true"


def api(base, key, path, body=None):
    req = urllib.request.Request(f"{base}/api/v3/{path}", method="POST" if body else "GET",
                                 data=json.dumps(body).encode() if body else None,
                                 headers={"X-Api-Key": key, "Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=300) as resp:
        data = resp.read()
    return json.loads(data) if data else None


def skip_tags(base, key):
    return {t["id"] for t in api(base, key, "tag") if t["label"] == SKIP_TAG}


def never_first(item):  # oldest search first; never searched sorts before everything
    return item.get("lastSearchTime") or ""


def movies():
    key = os.environ["RADARR_API_KEY"]
    skip = skip_tags(RADARR, key)
    todo = sorted((m for m in api(RADARR, key, "movie")
                   if m["monitored"] and not m["hasFile"] and m.get("isAvailable")
                   and not skip.intersection(m.get("tags", []))), key=never_first)[:MOVIES_PER_DAY]
    for m in todo:
        print(f"radarr: {m['title']} ({m.get('year')}) last searched {(m.get('lastSearchTime') or 'never')[:10]}", flush=True)
    if todo and not DRY_RUN:
        api(RADARR, key, "command", {"name": "MoviesSearch", "movieIds": [m["id"] for m in todo]})
    print(f"radarr: {len(todo)} movies searched{' (dry run)' if DRY_RUN else ''}", flush=True)


def episodes():
    key = os.environ["SONARR_API_KEY"]
    skip = skip_tags(SONARR, key)
    series = {s["id"]: s for s in api(SONARR, key, "series")}
    now = datetime.now(timezone.utc).isoformat()
    # Newest 1000 missing episodes, then the ones searched longest ago among them.
    page = api(SONARR, key, "wanted/missing?pageSize=1000&sortKey=airDateUtc&sortDirection=descending&monitored=true")
    todo = sorted((e for e in page.get("records", [])
                   if (e.get("airDateUtc") or "9") < now
                   and series.get(e["seriesId"], {}).get("seriesType") != "daily"
                   and not skip.intersection(series.get(e["seriesId"], {}).get("tags", []))),
                  key=never_first)[:EPISODES_PER_DAY]
    for e in todo:
        s = series.get(e["seriesId"], {})
        print(f"sonarr: {s.get('title')} S{e['seasonNumber']:02}E{e['episodeNumber']:02} last searched {(e.get('lastSearchTime') or 'never')[:10]}", flush=True)
    if todo and not DRY_RUN:
        api(SONARR, key, "command", {"name": "EpisodeSearch", "episodeIds": [e["id"] for e in todo]})
    print(f"sonarr: {len(todo)} episodes searched{' (dry run)' if DRY_RUN else ''}", flush=True)


def main():
    once = os.environ.get("ONCE", "false").lower() == "true"
    while True:
        if not once:
            now = datetime.now()
            nxt = now.replace(hour=HOUR, minute=0, second=0, microsecond=0)
            if nxt <= now:
                nxt += timedelta(days=1)
            print(f"next pass at {nxt:%Y-%m-%d %H:%M}", flush=True)
            time.sleep((nxt - now).total_seconds())
        for job in (movies, episodes):
            try:
                job()
            except Exception as exc:  # keep running; tomorrow retries
                print(f"{job.__name__}: failed: {exc}", file=sys.stderr, flush=True)
        if once:
            return


if __name__ == "__main__":
    main()
