"""Send every book on Goobs's Hardcover "Want to Read" list to Chaptarr (ebook) and Listenarr (audiobook).

Goodreads retired its shelf RSS feeds and its API, so Hardcover holds the reading list (the Goodreads
library was imported there once, 2026-11-02). Every INTERVAL_MIN minutes this reads the list and, for
each book not yet in an app, looks it up and adds it monitored with a search, so it is grabbed.

The apps are the state: a book already in Chaptarr (same foreign book id) or Listenarr (same ASIN,
the add answers 409) is skipped, so the first pass takes the whole list and later passes only see
books added since. A book removed from an app while it is still on Want to Read is added again;
take it off the list in Hardcover instead. No state file, no /opt/appdata folder.

Matching is strict: the title (before any subtitle) must match and one author's last name must
appear. No match is logged as "no match" and retried after RETRY_HOURS; nothing is guessed.
DRY_RUN=true logs what would be added and adds nothing; ONCE=true runs one pass and exits.
"""

import json
import os
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

HARDCOVER = "https://api.hardcover.app/v1/graphql"
CHAPTARR, LISTENARR = "http://chaptarr:8789/api/v1", "http://listenarr:4545/api/v1"
INTERVAL_MIN = int(os.environ.get("INTERVAL_MIN", "60"))
RETRY_HOURS = int(os.environ.get("RETRY_HOURS", "24"))
BOOKS_ROOT = os.environ.get("BOOKS_ROOT", "/mnt/nfs/Media/Books")
DRY_RUN = os.environ.get("DRY_RUN", "false").lower() == "true"
UA = "homelab hardcover-sync (github.com/mikegutberlet/homelab-infra)"

WANT_TO_READ = """
query { me { user_books(where: {status_id: {_eq: 1}}, order_by: {date_added: asc}) {
  book_id
  book { title contributions { contribution author { name } } }
} } }"""

misses = {}  # (app, hardcover book id) -> time of the last miss; retried after RETRY_HOURS


def call(url, headers, body=None, method=None):
    req = urllib.request.Request(url, method=method or ("POST" if body is not None else "GET"),
                                 data=json.dumps(body).encode() if body is not None else None,
                                 headers={"Content-Type": "application/json", "User-Agent": UA, **headers})
    with urllib.request.urlopen(req, timeout=120) as resp:
        data = resp.read()
    return json.loads(data) if data else None


def norm(text):
    text = (text or "").lower().split(":")[0]
    text = re.sub(r"[^a-z0-9 ]+", " ", text.replace("&", " and "))
    return re.sub(r"^(the|a|an) ", "", " ".join(text.split()))


def matches(want, title, authors):
    if norm(title) != norm(want["title"]):
        return False
    names = " ".join(a.lower() for a in authors if a)
    return any(a.split()[-1].lower() in names for a in want["authors"] if a.split())


def want_to_read():
    data = call(HARDCOVER, {"Authorization": f"Bearer {os.environ['HARDCOVER_TOKEN']}"}, {"query": WANT_TO_READ})
    if data.get("errors"):
        raise RuntimeError(f"hardcover: {data['errors']}")
    books = []
    for ub in data["data"]["me"][0]["user_books"]:
        authors = [c["author"]["name"] for c in ub["book"]["contributions"]
                   if c.get("author") and c.get("contribution") in (None, "Author")]
        books.append({"id": ub["book_id"], "title": ub["book"]["title"], "authors": authors})
    return books


def due(app, book):
    last = misses.get((app, book["id"]))
    return last is None or time.time() - last > RETRY_HOURS * 3600


def label(book):
    return f"{book['title']} ({', '.join(book['authors']) or 'no author'})"


class Chaptarr:
    name = "chaptarr"

    def __init__(self):
        self.h = {"X-Api-Key": os.environ["CHAPTARR_API_KEY"]}
        self.have = {b.get("foreignBookId") for b in call(f"{CHAPTARR}/book", self.h)}

    def add(self, book):
        term = urllib.parse.quote(f"{book['title']} {' '.join(book['authors'][:1])}")
        found = call(f"{CHAPTARR}/book/lookup?term={term}&mediaType=ebook", self.h) or []
        hit = next((b for b in found if matches(book, b.get("title"), [b.get("author", {}).get("authorName")])), None)
        if hit is None:
            return "no match"
        if hit.get("id") or hit.get("foreignBookId") in self.have:
            return "already there"
        if DRY_RUN:
            return f"would add (foreignBookId {hit.get('foreignBookId')})"
        root = next(r for r in call(f"{CHAPTARR}/rootfolder", self.h) if r["path"].rstrip("/") == BOOKS_ROOT)
        hit["monitored"] = True
        hit["addOptions"] = {"searchForNewBook": True}
        hit["author"].update({
            "qualityProfileId": root["defaultQualityProfileId"],
            "metadataProfileId": root["defaultMetadataProfileId"],
            "rootFolderPath": root["path"], "monitored": True,
            # Only this book: the author's other books stay unmonitored.
            "addOptions": {"monitor": "none", "searchForMissingBooks": False},
        })
        call(f"{CHAPTARR}/book", self.h, hit)
        self.have.add(hit.get("foreignBookId"))
        return "added, search started"


class Listenarr:
    name = "listenarr"

    def __init__(self):
        self.h = {"X-Api-Key": os.environ["LISTENARR_API_KEY"]}

    def add(self, book):
        q = urllib.parse.quote(f"{book['title']} {' '.join(book['authors'][:1])}")
        found = (call(f"{LISTENARR}/search/audible?query={q}&region=us", self.h) or {}).get("results") or []
        hit = next((r for r in found if matches(book, r.get("title"), [a.get("name") for a in r.get("authors") or []])), None)
        if hit is None:
            return "no match"
        try:
            call(f"{LISTENARR}/library/by-asin/{urllib.parse.quote(hit.get('asin') or '-')}", self.h)
            return "already there"
        except urllib.error.HTTPError as exc:
            if exc.code != 404:
                raise
        if DRY_RUN:
            return f"would add (ASIN {hit.get('asin')})"
        series = (hit.get("series") or [{}])[0]
        meta = {
            "asin": hit.get("asin"), "source": "Audible", "region": "us",
            "title": hit.get("title"), "subtitle": hit.get("subtitle"),
            "authors": [a["name"] for a in hit.get("authors") or [] if a.get("name")],
            "narrators": [n["name"] for n in hit.get("narrators") or [] if n.get("name")],
            "imageUrl": hit.get("imageUrl"), "publisher": hit.get("publisher"), "language": hit.get("language"),
            "runtime": hit.get("runtimeLengthMin") or hit.get("lengthMinutes") or hit.get("runtimeMinutes"),
            "publishYear": (hit.get("releaseDate") or "")[:4] or None, "publishedDate": (hit.get("releaseDate") or "")[:10] or None,
            "series": series.get("name"), "seriesNumber": series.get("position"),
            "genres": [g["name"] for g in hit.get("genres") or [] if g.get("name")], "isbn": [],
        }
        try:
            call(f"{LISTENARR}/library/add", self.h, {"metadata": meta, "monitored": True, "autoSearch": True})
        except urllib.error.HTTPError as exc:
            if exc.code == 409:
                return "already there"
            raise
        return "added, search started"


def run_pass():
    books = want_to_read()
    print(f"hardcover: {len(books)} books on Want to Read", flush=True)
    for app_cls in (Chaptarr, Listenarr):
        try:
            app = app_cls()
        except Exception as exc:  # one app down must not stop the other
            print(f"{app_cls.name}: unreachable: {exc}", file=sys.stderr, flush=True)
            continue
        for book in books:
            if not due(app.name, book):
                continue
            try:
                result = app.add(book)
            except Exception as exc:
                result = f"failed: {exc}"
            if result == "no match" or result.startswith("failed"):
                misses[(app.name, book["id"])] = time.time()
            else:
                misses.pop((app.name, book["id"]), None)
            if result != "already there":
                print(f"{app.name}: {label(book)}: {result}{' (dry run)' if DRY_RUN else ''}", flush=True)


def main():
    once = os.environ.get("ONCE", "false").lower() == "true"
    while True:
        try:
            run_pass()
        except Exception as exc:  # keep running; the next pass retries
            print(f"pass failed: {exc}", file=sys.stderr, flush=True)
        if once:
            return
        time.sleep(INTERVAL_MIN * 60)


if __name__ == "__main__":
    main()
