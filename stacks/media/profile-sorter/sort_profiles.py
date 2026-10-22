"""Move foreign-language titles into the "Foreign - Prefer English Dub" profiles.

Radarr and Sonarr cannot pick a quality profile by original language, so this runs once at start
and then daily: any movie or series whose original language is not English (and not unknown) and
that still uses the main profile moves to the foreign profile. It never moves anything back, never
touches titles on other profiles or tagged "keep-original", and never starts a search.
"""

import json
import os
import sys
import time
import urllib.request

FOREIGN = "Foreign - Prefer English Dub"
OPT_OUT_TAG = "keep-original"
ENGLISH, UNKNOWN = 1, 0
APPS = [
    # name, base url, api key env, main profile, list endpoint, editor id field
    ("radarr", "http://radarr:7878", "RADARR_API_KEY", "Anything Not terrible", "movie", "movieIds"),
    ("sonarr", "http://sonarr:8989", "SONARR_API_KEY", "Anything Not Terrible ", "series", "seriesIds"),
]
DRY_RUN = os.environ.get("DRY_RUN", "false").lower() == "true"


def api(base, key, path, method="GET", body=None):
    req = urllib.request.Request(
        f"{base}/api/v3/{path}",
        method=method,
        data=json.dumps(body).encode() if body is not None else None,
        headers={"X-Api-Key": key, "Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=120) as resp:
        data = resp.read()
    return json.loads(data) if data else None


def sort_app(name, base, key_env, main_name, endpoint, id_field):
    key = os.environ[key_env]
    profiles = {p["name"]: p["id"] for p in api(base, key, "qualityprofile")}
    if FOREIGN not in profiles or main_name not in profiles:
        print(f"{name}: profile missing ({FOREIGN!r} or {main_name!r}), skipping", flush=True)
        return
    opt_out = {t["id"] for t in api(base, key, "tag") if t["label"] == OPT_OUT_TAG}
    to_move = [
        item
        for item in api(base, key, endpoint)
        if item.get("qualityProfileId") == profiles[main_name]
        and (item.get("originalLanguage") or {}).get("id", UNKNOWN) not in (ENGLISH, UNKNOWN)
        and not opt_out.intersection(item.get("tags", []))
    ]
    for item in to_move:
        print(f"{name}: {item['title']} ({item['originalLanguage']['name']}) -> {FOREIGN}", flush=True)
    if to_move and not DRY_RUN:
        api(base, key, f"{endpoint}/editor", "PUT",
            {id_field: [i["id"] for i in to_move], "qualityProfileId": profiles[FOREIGN]})
    print(f"{name}: {len(to_move)} moved{' (dry run)' if DRY_RUN else ''}", flush=True)


def main():
    while True:
        for app in APPS:
            try:
                sort_app(*app)
            except Exception as exc:  # keep running; the next pass retries
                print(f"{app[0]}: failed: {exc}", file=sys.stderr, flush=True)
        if os.environ.get("ONCE", "false").lower() == "true":
            return
        time.sleep(24 * 3600)


if __name__ == "__main__":
    main()
