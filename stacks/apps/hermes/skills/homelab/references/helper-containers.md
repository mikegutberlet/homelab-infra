# Helper containers that drive app APIs

Small Python services in `stacks/<vm>/<name>/` that call app APIs on a schedule (profile-sorter,
stalled-cleaner, backlog-search, hardcover-sync on media). Use this when adding or changing one.

## Shape (copy an existing one)

- `image: python:3.12-alpine`, `user: ${PUID}:${PGID}`, `command: ["python", "-u", "/app/<script>.py"]`,
  `./<name>:/app:ro`, `depends_on` the apps it calls. Standard library only (`urllib`, `json`), no pip.
- Talk to sibling apps by compose service name (`http://chaptarr:8789`), keys from env (`${X_API_KEY}`) that
  Goobs adds to `secrets.sops.env`; add empty lines for them to `secrets.example.env`.
- Switches: `DRY_RUN=true` logs what it would do; `ONCE=true` runs one pass. Ship new ones with
  `DRY_RUN: "true"` in compose, read the log with Goobs, flip it in a follow-up PR.
- Prefer the apps as the state (skip what is already there) over a state file: no new `/opt/appdata`
  folder, so the ops-door `deploy` verb can ship it.
- Same PR: add the service to `stack_services` (inventory), a section in the matching `docs/` page, and a
  compose comment saying why it exists.

## Finding the real API (do this before writing calls)

Arr forks and canary apps drift from their docs. Read the source at the exact pinned tag:

1. `git ls-remote --tags <repo>` for the tag matching the compose image, then
   `git clone -q --depth 1 --branch <tag> <repo>` into the scratch dir.
2. Find routes: `grep -rn 'Route(\|\[Http' --include=*.cs` (versioned routes look like `api/v{version}/x`, call `/api/v1/x`).
3. Copy what the app's own UI sends: grep the frontend for the endpoint (`createAjaxRequest`, `api.ts`) and its
   payload builder (e.g. `getNewBook.js`). The UI path is the one that works; a documented endpoint may have
   a known bug in that version (check the project's open issues for the endpoint name).
4. Auth: `X-Api-Key` header for arr-style apps and Listenarr.
5. Which provider/option the UI really uses here: have Goobs run the action once in the UI, then grep the
   app's log under `/opt/appdata/<app>/logs/` for that request (it logs the params). The UI default in the
   source can differ from this install's settings.
6. When the live run fails (HTTP 4xx/5xx), grep the app's own log for the stack trace and find the throwing
   line in the pinned source; the cause is usually a missing app setting, not your payload.

Known in this lab:
- Chaptarr: `book/lookup?mediaType=ebook` returns nothing (every remote result is tagged audiobook). Search
  with `/api/v1/search?term=&provider=goodreads` (results wrap `{book, author}`); `provider=hardcover` answers
  503 "Hardcover search failed" unless a Hardcover token is set in Chaptarr's own settings. Add with `POST /api/v1/book`
  carrying `mediaType`, per-format root folder/profile fields on the author, `addOptions.monitor: specificBook`
  + `booksToMonitor`, and at least one monitored edition. Only same-format rows count as "already there".
- Listenarr: `GET /api/v1/search/audible?query=`, `GET /api/v1/library/by-asin/<asin>` (404 = not there),
  `POST /api/v1/library/add` with `{metadata, monitored, autoSearch}`; 409 = already there.
- Hardcover: GraphQL `https://api.hardcover.app/v1/graphql`, `Authorization: Bearer <token>`,
  `me { user_books(where: {status_id: {_eq: 1}}) ... }` (1 = Want to Read). Send a User-Agent.
- Goodreads: no API and no working shelf RSS; don't plan around it.

## Matching

Match strictly and log misses (`no match`) instead of guessing: normalise titles (lowercase, drop
parenthesised parts like `(Sprawl, #1)` / `(Unabridged)`, punctuation, leading The/A/An) and require one
author's last name. The candidate's title must equal the wanted title or the wanted title's part before
`:`; never cut the subtitle from the candidate too, or a series title matches one volume ("The Lord of the
Rings" = "…: The Return of the King"). Skip Audible "Virtual Voice" (synthetic) narrations. Keep a small
table of want/candidate/expected cases and run it with every matcher change. Make dry-run lines name the matched title plus author or narrator, not only an ID,
so Goobs can spot a wrong edition.

## Test before the PR

No app keys here, so test against fakes: a `http.server.ThreadingHTTPServer` on 127.0.0.1 answering the
endpoints with realistic shapes, import the script, point its base URLs at the fake, run dry run, a real
pass, and a second pass. Assert: dry run posts nothing, real pass posts each item once with the expected
payload, second pass posts nothing, misses are reported. Then `py_compile` and the YAML check. Say plainly
that the live API is untested until the dry run on the VM.

## After deploy

A deploy that changes only the script does not restart the container (compose sees no config change), so
the old code keeps running. Check the container's uptime against the deploy time; if it predates it, propose
`ssh ops-<vm> 'restart <service>'` and wait for Goobs's go (offer "deploy and restart" as one request next time).

`docker ps -a` must show the new container (the deploy verb says `ok` even for an unlisted service), then
read `docker logs <vm>-<name>-1` and summarise per app: would add / already there / no match / failed
counts. Surprises in the input (list bigger than expected, 0 matches in one app) stop the flip to
`DRY_RUN=false` until Goobs confirms.
