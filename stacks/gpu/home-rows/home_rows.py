"""Emby home screen rows to match the Plex ones that Kometa builds (Goobs, 2026-10-22).

Emby has no Kometa, so the rows are made by the Home Screen Companion plugin (HSC, Emby plugin
catalog). This script writes HSC's sources through Emby's plugin-configuration API, runs HSC's
sync, and repeats daily at RUN_AT, after Kometa's 05:00 run:

- Rule rows (genres, decades, studios, networks...) are HSC smart rules on Emby's own metadata.
- List rows (Trending, Popular, Top Rated, seasonal, Oscars...) are copied from the Plex
  collection with the same name: the IMDb ids of its titles become an HSC "IMDb id" rule. A
  Plex collection that is missing (a seasonal one out of season) switches its Emby row off.
- Your Next Watch is HSC's per-user recommendation playlist.

Every row is an Emby collection. HOME rows are also put on every user's home screen; the rest
are browsable under each library's Collections. Only HSC sources named "homelab: ..." are ever
changed, so sources added by hand in the plugin page are left alone. Nothing here touches media
files, and DRY_RUN=true only prints what would change.

GET /health on HEALTH_PORT answers 200 while both this script's last sync and Kometa's last run
(its log file) are under 26 hours old, for Uptime Kuma and Homepage.
"""

import datetime as dt
import http.server
import json
import os
import sys
import threading
import time
import urllib.parse
import urllib.request

PLEX = os.environ.get("PLEX_URL", "http://127.0.0.1:32400")
EMBY = os.environ.get("EMBY_URL", "http://127.0.0.1:8096") + "/emby"
PLEX_TOKEN = os.environ.get("PLEX_TOKEN", "")
EMBY_KEY = os.environ.get("EMBY_API_KEY", "")
RUN_AT = os.environ.get("RUN_AT", "07:00")
HEALTH_PORT = int(os.environ.get("HEALTH_PORT", "8097"))
KOMETA_LOG = os.environ.get("KOMETA_LOG", "/kometa/logs/meta.log")
DRY_RUN = os.environ.get("DRY_RUN", "false").lower() == "true"

HSC_ID = "7c10708f-43e4-4d69-923c-77d01802315b"  # Plugin.Id in HomeScreenCompanion/Plugin.cs
HSC_SYNC_TASK = "HomeScreenCompanionSyncTask"
PREFIX = "homelab: "
STALE = dt.timedelta(hours=26)
MOVIES, SHOWS = "Movies", "TV Shows"  # Plex library names


def rule(*criteria, any_of=False):
    return {"Operator": "OR" if any_of else "AND", "GroupOperator": "AND", "Criteria": list(criteria)}


MOVIE, SERIES = rule("MediaType:Movie"), rule("MediaType:Series")

# (row name, home row?, source). Source: ("plex", library) copies the Plex collection of that
# name; ("rules", [groups]) is an HSC smart rule. Home rows appear in this order. Sort "Random"
# shuffles a row on every load; list rows keep Emby's default order.
ROWS = [
    ("Trending This Week", True, ("plex", MOVIES)),
    ("Popular Right Now", True, ("plex", MOVIES)),
    ("New Releases", True, ("rules", [MOVIE, rule("Year:>=:{last_year}")])),
    ("Halloween Movies", True, ("plex", MOVIES)),
    ("Thanksgiving Movies", True, ("plex", MOVIES)),
    ("Christmas Movies", True, ("plex", MOVIES)),
    ("Valentine's Day Movies", True, ("plex", MOVIES)),
    ("Family Night", True, ("rules", [MOVIE, rule("ContentRating:exact:G", "ContentRating:exact:PG", any_of=True)])),
    ("Top Rated of All Time", True, ("plex", MOVIES)),
    ("Trending Shows", True, ("plex", SHOWS)),
    ("Popular Shows", True, ("plex", SHOWS)),
    ("Hidden Gems", False, ("plex", MOVIES)),
    ("International Films in English", False, ("plex", MOVIES)),
    ("Oscar Winners", False, ("plex", MOVIES)),
    ("Quick Watches", False, ("rules", [MOVIE, rule("Runtime:>=:60", "Runtime:<=:95")])),
    ("Action Movies", False, ("rules", [MOVIE, rule("Genre:exact:Action")])),
    ("Comedy Movies", False, ("rules", [MOVIE, rule("Genre:exact:Comedy")])),
    ("Horror Movies", False, ("rules", [MOVIE, rule("Genre:exact:Horror")])),
    ("Sci-Fi Movies", False, ("rules", [MOVIE, rule("Genre:Science Fiction", "Genre:Sci-Fi", any_of=True)])),
    ("Thrillers", False, ("rules", [MOVIE, rule("Genre:exact:Thriller")])),
    ("Romance Movies", False, ("rules", [MOVIE, rule("Genre:exact:Romance")])),
    ("Animated Movies", False, ("rules", [MOVIE, rule("Genre:exact:Animation")])),
    ("Documentaries", False, ("rules", [MOVIE, rule("Genre:exact:Documentary")])),
    ("Crime Movies", False, ("rules", [MOVIE, rule("Genre:exact:Crime")])),
    ("80s Movies", False, ("rules", [MOVIE, rule("Year:>=:1980", "Year:<=:1989")])),
    ("90s Movies", False, ("rules", [MOVIE, rule("Year:>=:1990", "Year:<=:1999")])),
    ("2000s Movies", False, ("rules", [MOVIE, rule("Year:>=:2000", "Year:<=:2009")])),
    ("2010s Movies", False, ("rules", [MOVIE, rule("Year:>=:2010", "Year:<=:2019")])),
    ("Pixar", False, ("rules", [MOVIE, rule("Studio:Pixar")])),
    ("Disney", False, ("rules", [MOVIE, rule("Studio:Walt Disney")])),
    ("DreamWorks", False, ("rules", [MOVIE, rule("Studio:DreamWorks")])),
    ("Marvel Studios", False, ("rules", [MOVIE, rule("Studio:exact:Marvel Studios")])),
    ("A24", False, ("rules", [MOVIE, rule("Studio:exact:A24")])),
    ("Top Rated Shows", False, ("plex", SHOWS)),
    ("Sitcoms", False, ("plex", SHOWS)),
    ("Limited Series", False, ("plex", SHOWS)),
    ("Finished Series", False, ("plex", SHOWS)),
    ("Crime and Mystery", False, ("rules", [SERIES, rule("Genre:exact:Crime", "Genre:exact:Mystery", any_of=True)])),
    ("Reality TV", False, ("rules", [SERIES, rule("Genre:Reality")])),
    ("Kids Shows", False, ("rules", [SERIES, rule("Genre:Kids", "Genre:Children", "ContentRating:exact:TV-Y",
                                                  "ContentRating:exact:TV-Y7", any_of=True)])),
    # Emby keeps networks in a series' studios.
    ("HBO", False, ("rules", [SERIES, rule("Studio:HBO", "Studio:exact:Max", any_of=True)])),
    ("Netflix Originals", False, ("rules", [SERIES, rule("Studio:Netflix")])),
    ("FX", False, ("rules", [SERIES, rule("Studio:exact:FX", "Studio:exact:FXX", "Studio:FX on Hulu", any_of=True)])),
    ("AMC", False, ("rules", [SERIES, rule("Studio:exact:AMC", "Studio:exact:AMC+", any_of=True)])),
    ("Adult Swim", False, ("rules", [SERIES, rule("Studio:Adult Swim")])),
    ("Cartoon Network", False, ("rules", [SERIES, rule("Studio:Cartoon Network")])),
    ("Disney TV", False, ("rules", [SERIES, rule("Studio:Disney")])),
]
RANDOM_SORT = {name for name, _, (kind, _) in ROWS if kind == "rules" and name != "New Releases"}

state = {"last_sync": None, "last_error": None, "rows": 0, "rows_off": []}


def call(url, method="GET", body=None, headers=None):
    req = urllib.request.Request(url, method=method, headers={"Accept": "application/json", **(headers or {})},
                                 data=json.dumps(body).encode() if body is not None else None)
    if body is not None:
        req.add_header("Content-Type", "application/json")
    with urllib.request.urlopen(req, timeout=120) as resp:
        data = resp.read()
    return json.loads(data) if data.strip() else None


def plex(path, **params):
    q = urllib.parse.urlencode(params)
    return call(f"{PLEX}{path}{'?' + q if q else ''}", headers={"X-Plex-Token": PLEX_TOKEN})["MediaContainer"]


def emby(path, method="GET", body=None):
    return call(f"{EMBY}{path}", method, body, {"X-Emby-Token": EMBY_KEY})


def plex_collections():
    """{(library, collection title): [imdb ids in collection order]} for the Plex movie/show libraries."""
    out = {}
    for sec in plex("/library/sections").get("Directory", []):
        if sec["title"] not in (MOVIES, SHOWS):
            continue
        for col in plex(f"/library/sections/{sec['key']}/collections").get("Metadata", []):
            items = plex(f"/library/collections/{col['ratingKey']}/children", includeGuids=1).get("Metadata", [])
            ids = [g["id"][7:] for it in items for g in it.get("Guid", []) if g["id"].startswith("imdb://")]
            out[(sec["title"], col["title"])] = ids
    return out


def source(name, home, criteria, users, active):
    """The HSC TagConfig fields this script owns for one row (other fields are kept as they are)."""
    settings = {"SectionType": "boxset", "CustomName": name}
    if name in RANDOM_SORT:
        settings["SortBy"] = "Random"
    return {
        "Name": PREFIX + name, "Tag": name, "Active": active, "SourceType": "MediaInfo", "Limit": 0,
        "MediaInfoFilters": criteria,
        "EnableTag": False, "OnlyCollection": True,
        "EnableCollection": True, "CollectionName": name,
        "CollectionDescription": "Kept up to date by home-rows (homelab-infra).",
        "EnableHomeSection": home, "HomeSectionUserIds": users if home else [],
        "HomeSectionLibraryId": "auto", "HomeSectionSettings": json.dumps(settings),
    }


def next_watch(users):
    return {
        "Name": PREFIX + "Your Next Watch", "Tag": "Your Next Watch", "Active": True, "SourceType": "NextWatch",
        "Limit": 30, "EnableTag": False, "EnableCollection": False,
        "EnablePlaylist": True, "PlaylistName": "Your Next Watch", "PlaylistUserIds": users,
        "NextWatchUsersMigrated": True,
        "EnableHomeSection": True, "HomeSectionUserIds": users, "HomeSectionLibraryId": "auto",
        "HomeSectionSettings": json.dumps({"SectionType": "playlist", "CustomName": "Your Next Watch"}),
    }


def sync():
    users = [u["Id"] for u in emby("/Users/Query?IsDisabled=false")["Items"]]
    cols = plex_collections()
    wanted, off = [next_watch(users)], []
    for name, home, (kind, arg) in ROWS:
        if kind == "plex":
            ids = cols.get((arg, name))
            active = bool(ids)
            if not active:
                off.append(name)
            kind_rule = SERIES if arg == SHOWS else MOVIE
            # An empty IMDb rule matches nothing, so an inactive row keeps a placeholder.
            criteria = [kind_rule, rule("ImdbId:" + "\n".join(ids or ["tt0000000"]))]
        else:
            last_year = str(dt.date.today().year - 1)
            active = True
            criteria = [{**g, "Criteria": [c.replace("{last_year}", last_year) for c in g["Criteria"]]} for g in arg]
        wanted.append(source(name, home, criteria, users, active))

    cfg = emby(f"/Plugins/{HSC_ID}/Configuration")
    tags = cfg.get("Tags") or []
    by_name = {t.get("Name"): t for t in tags}
    now = dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.0000000Z")
    added = changed = 0
    for w in wanted:
        cur = by_name.get(w["Name"])
        if cur is None:
            tags.append({**w, "LastModified": now})
            added += 1
        elif any(cur.get(k) != v for k, v in w.items()):
            cur.update(w, LastModified=now)
            changed += 1
    # Rows dropped from ROWS: switch their source off (the plugin page can delete it for good).
    names = {w["Name"] for w in wanted}
    for t in tags:
        if t.get("Name", "").startswith(PREFIX) and t["Name"] not in names and t.get("Active"):
            t["Active"] = False
            changed += 1
    cfg["Tags"] = tags
    print(f"{len(wanted)} rows: {added} new, {changed} changed; off (no Plex collection): {', '.join(off) or 'none'}")
    if DRY_RUN:
        print("DRY_RUN: nothing written")
        return len(wanted), off
    if added or changed:
        emby(f"/Plugins/{HSC_ID}/Configuration", "POST", cfg)
    task = next((t for t in emby("/ScheduledTasks") if t.get("Key") == HSC_SYNC_TASK), None)
    if task:
        emby(f"/ScheduledTasks/Running/{task['Id']}", "POST")
        print("Started the Home Screen Companion sync")
    else:
        print("Home Screen Companion's sync task not found: is the plugin installed?")
    return len(wanted), off


def run_once():
    try:
        state["rows"], state["rows_off"] = sync()
        state["last_sync"], state["last_error"] = dt.datetime.now(dt.timezone.utc), None
    except Exception as e:  # keep the daily loop and the health endpoint alive
        state["last_error"] = f"{type(e).__name__}: {e}"
        print(f"Sync failed: {state['last_error']}", file=sys.stderr)


def health():
    now = dt.datetime.now(dt.timezone.utc)
    try:
        kometa = dt.datetime.fromtimestamp(os.path.getmtime(KOMETA_LOG), dt.timezone.utc)
    except OSError:
        kometa = None
    ok = bool(state["last_sync"] and now - state["last_sync"] < STALE and kometa and now - kometa < STALE)
    return ok, {
        "ok": ok,
        "emby_rows_synced": state["last_sync"].isoformat() if state["last_sync"] else None,
        "kometa_last_log": kometa.isoformat() if kometa else None,
        "rows": state["rows"], "rows_off": state["rows_off"], "last_error": state["last_error"],
    }


class Health(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        ok, body = health()
        data = json.dumps(body, indent=1).encode()
        self.send_response(200 if ok else 503)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def log_message(self, *args):
        pass


def seconds_until(hhmm):
    now = dt.datetime.now()
    h, m = map(int, hhmm.split(":"))
    nxt = now.replace(hour=h, minute=m, second=0, microsecond=0)
    if nxt <= now:
        nxt += dt.timedelta(days=1)
    return (nxt - now).total_seconds()


if __name__ == "__main__":
    if not PLEX_TOKEN or not EMBY_KEY:
        sys.exit("PLEX_TOKEN and EMBY_API_KEY are required")
    if "--once" in sys.argv:
        run_once()
        sys.exit(1 if state["last_error"] else 0)
    server = http.server.ThreadingHTTPServer(("0.0.0.0", HEALTH_PORT), Health)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    run_once()
    while True:
        time.sleep(seconds_until(RUN_AT))
        run_once()
