#!/usr/bin/env python3
"""Add the 115 Disney Channel Original Movies (1997-2022) to Radarr (playbooks/dcom-add.yml).

Runs as root on the media VM; `--dry-run` only reports. Each title is looked up through Radarr's
own TMDB search, matched on title + year, and added monitored with the standard profile and a
`dcom` tag when it is not in the library yet. A second run adds nothing. Nothing is searched
right away: the daily backlog search (20 movies a day) works through them, or run
`--search` once to queue a search for every tagged movie that is still missing.

Matching: the title with punctuation and spaces removed must equal the TMDB title (or, failing
that, contain it) and the year must be the premiere year; a one-year difference is accepted only when it is the single candidate. More than one
candidate, or none, is listed for Goobs instead of guessing. Sequels and remakes with the same
name (Under Wraps 1997/2021, Kim Possible 2019, Freaky Friday 2018) are told apart by year.

The API key is read from Radarr's config file and never printed.
"""
import json
import re
import sys
import urllib.error
import urllib.parse
import urllib.request

DRY = "--dry-run" in sys.argv
SEARCH = "--search" in sys.argv
RADARR = "http://127.0.0.1:7878/api/v3/"
CONFIG = "/opt/appdata/radarr/config.xml"
ROOT = "/mnt/nfs/Media/Movies"
PROFILE = "Anything Not terrible"
TAG = "dcom"

MOVIES = [  # (premiere year, title) in Disney Channel premiere order
    (1997, "Northern Lights"), (1997, "Under Wraps"), (1998, "You Lucky Dog"), (1998, "Brink!"),
    (1998, "Halloweentown"), (1999, "Zenon: Girl of the 21st Century"), (1999, "Can of Worms"),
    (1999, "The Thirteenth Year"), (1999, "Smart House"), (1999, "Johnny Tsunami"), (1999, "Genius"),
    (1999, "P.U.N.K.S."), (1999, "Don't Look Under the Bed"), (1999, "Horse Sense"),
    (2000, "Up, Up and Away"), (2000, "The Color of Friendship"), (2000, "Alley Cats Strike!"),
    (2000, "Rip Girls"), (2000, "Miracle in Lane 2"), (2000, "Stepsister from Planet Weird"),
    (2000, "Ready to Run"), (2000, "Quints"), (2000, "The Other Me"),
    (2000, "Mom's Got a Date with a Vampire"), (2000, "Phantom of the Megaplex"),
    (2000, "The Ultimate Christmas Present"), (2001, "Zenon: The Zequel"), (2001, "Motocrossed"),
    (2001, "The Luck of the Irish"), (2001, "Hounded"), (2001, "Jett Jackson: The Movie"),
    (2001, "The Jennie Project"), (2001, "Jumping Ship"), (2001, "The Poof Point"),
    (2001, "Halloweentown II: Kalabar's Revenge"), (2001, "'Twas the Night"), (2002, "Double Teamed"),
    (2002, "Cadet Kelly"), (2002, "Tru Confessions"), (2002, "Get a Clue"), (2002, "Gotta Kick It Up!"),
    (2002, "A Ring of Endless Light"), (2002, "The Scream Team"), (2003, "You Wish!"),
    (2003, "Right on Track"), (2003, "The Even Stevens Movie"), (2003, "Eddie's Million Dollar Cook-Off"),
    (2003, "The Cheetah Girls"), (2003, "Full-Court Miracle"), (2004, "Pixel Perfect"),
    (2004, "Going to the Mat"), (2004, "Zenon: Z3"), (2004, "Stuck in the Suburbs"), (2004, "Tiger Cruise"),
    (2004, "Halloweentown High"), (2005, "Now You See It"), (2005, "Buffalo Dreams"),
    (2005, "Kim Possible: So the Drama"), (2005, "Go Figure"), (2005, "Life Is Ruff"),
    (2005, "The Proud Family Movie"), (2005, "Twitches"), (2006, "High School Musical"), (2006, "Cow Belles"),
    (2006, "Wendy Wu: Homecoming Warrior"), (2006, "Read It and Weep"), (2006, "The Cheetah Girls 2"),
    (2006, "Return to Halloweentown"), (2007, "Jump In!"), (2007, "Johnny Kapahala: Back on Board"),
    (2007, "High School Musical 2"), (2007, "Twitches Too"), (2008, "Minutemen"), (2008, "Camp Rock"),
    (2008, "The Cheetah Girls: One World"), (2009, "Dadnapped"), (2009, "Hatching Pete"),
    (2009, "Princess Protection Program"), (2009, "Wizards of Waverly Place: The Movie"),
    (2010, "Starstruck"), (2010, "Den Brother"), (2010, "Camp Rock 2: The Final Jam"), (2010, "Avalon High"),
    (2011, "The Suite Life Movie"), (2011, "Lemonade Mouth"),
    (2011, "Phineas and Ferb the Movie: Across the 2nd Dimension"), (2011, "Geek Charming"),
    (2011, "Good Luck Charlie, It's Christmas"), (2012, "Frenemies"), (2012, "Radio Rebel"),
    (2012, "Let It Shine"), (2012, "Girl Vs. Monster"), (2013, "Teen Beach Movie"), (2014, "Cloud 9"),
    (2014, "Zapped"), (2014, "How to Build a Better Boy"), (2015, "Bad Hair Day"), (2015, "Teen Beach 2"),
    (2015, "Descendants"), (2015, "Invisible Sister"), (2016, "Adventures in Babysitting"), (2016, "The Swap"),
    (2017, "Tangled: Before Ever After"), (2017, "Descendants 2"), (2018, "Zombies"), (2018, "Freaky Friday"),
    (2019, "Descendants 3"), (2019, "Kim Possible"), (2020, "Zombies 2"), (2020, "Upside-Down Magic"),
    (2021, "Spin"), (2021, "Under Wraps"), (2021, "Christmas Again"), (2022, "Zombies 3"), (2022, "Under Wraps 2"),
]

KEY = re.search(r"<ApiKey>(.*?)</ApiKey>", open(CONFIG).read()).group(1)
present, added, todo = [], [], []


def api(path, method="GET", body=None):
    req = urllib.request.Request(RADARR + path, method=method,
                                 data=json.dumps(body).encode() if body is not None else None,
                                 headers={"X-Api-Key": KEY, "Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=120) as r:
            data = r.read().decode()
    except urllib.error.HTTPError as e:
        sys.exit(f"radarr {method} {path}: HTTP {e.code} {e.read().decode(errors='replace')[:300]}")
    return json.loads(data) if data.strip() else None


def norm(title):
    return re.sub(r"[^a-z0-9]", "", title.lower().replace("&", "and"))


def match(year, title):
    """One TMDB candidate for the title, or None plus the reason."""
    want = norm(title)
    cands = [c for c in api("movie/lookup?term=" + urllib.parse.quote(title)) if c.get("tmdbId")]
    names = lambda c: {norm(c.get("title", "")), norm(c.get("originalTitle", ""))}
    by_name = [c for c in cands if want in names(c)]
    if not by_name:  # "Disney's X Movie" style names: the TMDB title inside ours
        by_name = [c for c in cands if any(len(n) > 4 and n in want for n in names(c))]
    exact = [c for c in by_name if c.get("year") == year]
    if len(exact) == 1:
        return exact[0], ""
    if exact:
        return None, "several TMDB entries: " + "; ".join(f"{c['title']} ({c['year']}, tmdb {c['tmdbId']})" for c in exact)
    near = [c for c in by_name if abs((c.get("year") or 0) - year) == 1]
    if len(near) == 1:
        return near[0], f"TMDB year {near[0]['year']}"
    seen = "; ".join(f"{c['title']} ({c.get('year')})" for c in by_name[:4]) or ", ".join(c.get("title", "?") for c in cands[:4])
    return None, f"no TMDB match for {year} (saw: {seen or 'nothing'})"


def main():
    profile = next((p["id"] for p in api("qualityprofile") if p["name"] == PROFILE), None)
    if not profile or not any(r["path"] == ROOT for r in api("rootfolder")):
        sys.exit(f"profile {PROFILE!r} or root folder {ROOT} not found")
    tag = next((t["id"] for t in api("tag") if t["label"] == TAG), None)
    if tag is None and not DRY:
        tag = api("tag", "POST", {"label": TAG})["id"]
    library = api("movie")
    by_tmdb = {m["tmdbId"]: m for m in library}
    by_name = {}
    for m in library:
        by_name.setdefault((norm(m["title"]), m.get("year")), m)
    for year, title in MOVIES:
        have = by_name.get((norm(title), year))
        if have is None:
            cand, why = match(year, title)
            if cand is None:
                todo.append(f"{title} ({year}): {why}")
                print(f"for Goobs: {title} ({year}): {why}", flush=True)
                continue
            have = by_tmdb.get(cand["tmdbId"])
        if have is not None:
            file = have.get("movieFile", {}).get("relativePath") if have.get("hasFile") else None
            present.append(f"{have['title']} ({have['year']}): {'file ' + file if file else 'in Radarr, no file yet'}")
            if tag is not None and tag not in have.get("tags", []) and not DRY:
                api(f"movie/{have['id']}", "PUT", {**have, "tags": have.get("tags", []) + [tag]})
            continue
        studio = cand.get("studio") or "no studio on TMDB"
        added.append(f"{cand['title']} ({cand['year']}, tmdb {cand['tmdbId']}, {studio}{'; ' + why if why else ''})")
        print(("would add: " if DRY else "added: ") + added[-1], flush=True)
        if not DRY:
            api("movie", "POST", {**cand, "qualityProfileId": profile, "rootFolderPath": ROOT, "monitored": True,
                                  "minimumAvailability": "released", "tags": [tag],
                                  "addOptions": {"searchForMovie": False}})
    for line in present:
        print("present: " + line)
    if SEARCH and not DRY and tag is not None:
        ids = [m["id"] for m in api("movie") if tag in m.get("tags", []) and not m.get("hasFile")]
        api("command", "POST", {"name": "MoviesSearch", "movieIds": ids})
        print(f"search queued for {len(ids)} movies")
    print(f"present: {len(present)}; {'would add' if DRY else 'added'}: {len(added)}; for Goobs: {len(todo)}")


if __name__ == "__main__":
    main()
