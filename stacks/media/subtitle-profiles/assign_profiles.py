"""Give every series and movie in Bazarr a language profile.

Bazarr only fetches subtitles for titles that have a language profile, and its "default profile"
setting only covers titles added after it is switched on. This runs once at start and then daily:
any title with no profile, or still on one of the legacy profiles in REASSIGN_FROM, gets

  * "English + Japanese" when the original language in Sonarr/Radarr is Japanese, or the title
    carries the tag "ja-subs" there;
  * "English" otherwise.

Titles on any other profile are left alone, so a profile picked by hand in Bazarr sticks.
It never starts a search; Bazarr's own wanted-search task picks the new items up.
"""

import json
import os
import sys
import time
import urllib.parse
import urllib.request

BAZARR = os.environ.get("BAZARR_URL", "http://bazarr:6767")
ENGLISH = os.environ.get("PROFILE_EN", "English")
ENGLISH_JA = os.environ.get("PROFILE_EN_JA", "English + Japanese")
OPT_IN_TAG = os.environ.get("JA_TAG", "ja-subs")
REASSIGN_FROM = [p.strip() for p in os.environ.get("REASSIGN_FROM", "Eng/Japanese Subs").split(",") if p.strip()]
DRY_RUN = os.environ.get("DRY_RUN", "false").lower() == "true"
BATCH = 100
APPS = [
    # name, arr base url, arr api key env, arr list endpoint, bazarr endpoint, bazarr id field, arr id field in bazarr
    ("radarr", "http://radarr:7878", "RADARR_API_KEY", "movie", "movies", "radarrid", "radarrId"),
    ("sonarr", "http://sonarr:8989", "SONARR_API_KEY", "series", "series", "seriesid", "sonarrSeriesId"),
]


def arr_api(base, key, path):
    req = urllib.request.Request(f"{base}/api/v3/{path}", headers={"X-Api-Key": key})
    with urllib.request.urlopen(req, timeout=120) as resp:
        return json.loads(resp.read())


def bazarr_api(path, method="GET", form=None):
    data = urllib.parse.urlencode(form, doseq=True).encode() if form is not None else None
    req = urllib.request.Request(
        f"{BAZARR}/api/{path}", method=method, data=data,
        headers={"X-API-KEY": os.environ["BAZARR_API_KEY"],
                 "Content-Type": "application/x-www-form-urlencoded"},
    )
    with urllib.request.urlopen(req, timeout=300) as resp:
        body = resp.read()
    return json.loads(body) if body else None


def wants_japanese(arr_item, ja_tag_ids):
    language = (arr_item.get("originalLanguage") or {}).get("name", "")
    return language.lower() == "japanese" or bool(ja_tag_ids.intersection(arr_item.get("tags", [])))


def assign(name, base, key_env, arr_endpoint, bz_endpoint, bz_id_field, arr_id_key):
    key = os.environ[key_env]
    profiles = {p["name"]: p["profileId"] for p in bazarr_api("system/languages/profiles")}
    if ENGLISH not in profiles or ENGLISH_JA not in profiles:
        print(f"{name}: Bazarr profile missing ({ENGLISH!r} or {ENGLISH_JA!r}), skipping", flush=True)
        return
    reassign_ids = {profiles[p] for p in REASSIGN_FROM if p in profiles}
    ja_tag_ids = {t["id"] for t in arr_api(base, key, "tag") if t["label"] == OPT_IN_TAG}
    arr_items = {item["id"]: item for item in arr_api(base, key, arr_endpoint)}

    changes = []
    for item in bazarr_api(f"{bz_endpoint}?start=0&length=-1")["data"]:
        current = item.get("profileId")
        if current is not None and current not in reassign_ids:
            continue
        arr_item = arr_items.get(item[arr_id_key])
        if arr_item is None:
            continue  # Bazarr has not synced it yet; next pass
        wanted = profiles[ENGLISH_JA] if wants_japanese(arr_item, ja_tag_ids) else profiles[ENGLISH]
        if wanted != current:
            changes.append((item[arr_id_key], item.get("title", "?"), current, wanted))

    for arr_id, title, current, wanted in changes:
        print(f"{name}: {title} [{arr_id}] profile {current} -> {wanted}", flush=True)
    if changes and not DRY_RUN:
        for start in range(0, len(changes), BATCH):
            chunk = changes[start:start + BATCH]
            bazarr_api(bz_endpoint, "POST", {
                bz_id_field: [c[0] for c in chunk],
                "profileid": [c[3] for c in chunk],
            })
            time.sleep(5)  # Bazarr re-lists missing subtitles for each title after the update
    print(f"{name}: {len(changes)} assigned{' (dry run)' if DRY_RUN else ''}", flush=True)


def main():
    while True:
        for app in APPS:
            try:
                assign(*app)
            except Exception as exc:  # keep running; the next pass retries
                print(f"{app[0]}: failed: {exc}", file=sys.stderr, flush=True)
        if os.environ.get("ONCE", "false").lower() == "true":
            return
        time.sleep(24 * 3600)


if __name__ == "__main__":
    main()
