# Read-only: Radarr's and Sonarr's current files -> {path: {id, lang, title}} for the Tdarr flow.
# id is the movie id (Radarr) or the series id (Sonarr); lang is the original language name.
# Run on the media VM: sudo python3 media_map.py /tmp  (writes radarr_map.json and sonarr_map.json),
# then copy both to /opt/appdata/tdarr/configs/hevc/ on the gpu VM (owner storage, mode 600).
# Files imported after the last run are skipped by the flow until the maps are refreshed.
import json, os, re, sys, urllib.request

def api(app, port, path):
    key = re.search(r"<ApiKey>([^<]+)", open(f"/opt/appdata/{app}/config.xml").read()).group(1)
    req = urllib.request.Request(f"http://localhost:{port}/api/v3/{path}", headers={"X-Api-Key": key})
    return json.load(urllib.request.urlopen(req, timeout=600))

out = sys.argv[1] if len(sys.argv) > 1 else "."
lang = lambda x: (x.get("originalLanguage") or {}).get("name", "Unknown")

movies = {}
for m in api("radarr", 7878, "movie"):
    f = m.get("movieFile")
    if m["hasFile"] and f:
        movies[f"{m['path']}/{f['relativePath']}"] = {"id": m["id"], "lang": lang(m), "title": f"{m['title']} ({m['year']})"}

episodes = {}
for s in api("sonarr", 8989, "series"):
    for f in api("sonarr", 8989, f"episodefile?seriesId={s['id']}"):
        episodes[f["path"]] = {"id": s["id"], "lang": lang(s), "title": s["title"]}

for name, data in (("radarr_map.json", movies), ("sonarr_map.json", episodes)):
    with open(os.path.join(out, name), "w") as fh:
        json.dump(data, fh)
print(len(movies), "movie files,", len(episodes), "episode files")
