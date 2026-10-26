# Curated home screen rows (Plex and Emby)

Set up 2026-10-22. Goal (Goobs): Netflix-style rows of movies and shows on the Plex and Emby
home screens. Goobs picked all 25 rows from the menu (2026-10-22 20:45 UTC: more rows on Home, and Continue Watching, New Releases and Recently Added at the top), with these defaults: no poster badges,
nothing is ever downloaded because of a list, and only the top rows go on Home.

## How it works

| Piece | Where | When | What it does |
| --- | --- | --- | --- |
| Kometa | gpu stack, `stacks/gpu/kometa/` | daily 05:00 | Builds Plex collections and pins the featured ones to Home (owner and shared users) |
| home-rows | gpu stack, `stacks/gpu/home-rows/home_rows.py` | at start, then daily 07:00 | Writes the Emby rows into the Home Screen Companion plugin and runs its sync |
| Home Screen Companion (HSC) | Emby plugin catalog | its own sync 04:00 + when home-rows asks | Keeps the Emby collections, home rows and "Your Next Watch" playlists |

- Plex rows live in `movies.yml` and `shows.yml`. `HomeRow` rows go on Home in file order
  (`auto_sort_hubs: configured`); `Row` rows are on each library's Recommended tab and under
  Collections. Plex keeps each library's Home rows in one block, in library pin order, so movie
  and TV rows cannot be mixed. Goobs's order (2026-10-26): Continue Watching, New Releases,
  Recently Added Movies, Recently Released Episodes, Recently Added TV, then the TV rows. So
  New Releases is the only movie `HomeRow`; the built-in rows' order and Home switches are set
  in Plex (Settings > Manage > Libraries > Manage Recommendations), which Kometa never touches.
  Movies must be pinned above TV Shows. Franchise boxed sets come from Kometa's
  `franchise` default (2+ owned films).
- Emby rows are listed in `ROWS` in `home_rows.py`. Rule rows (genres, decades, studios,
  networks, Family Night, Quick Watches, New Releases) use Emby's own metadata. List rows
  (Trending, Popular, Top Rated, seasonal, Oscars, Hidden Gems, International, Sitcoms, Limited
  and Finished Series) copy the IMDb ids of the Plex collection with the same name, so the two
  servers show the same titles. A Plex collection that is missing (a seasonal row out of season)
  switches the Emby row off, and HSC removes its collection and home row until it returns.
- Every Emby row is a collection; Home rows are on every user's home screen, the rest are under
  Collections. After each HSC sync, home-rows puts every user's Home in this order: Continue
  Watching, New Releases, Latest Media, Your Next Watch, then the Home rows in `ROWS` order
  (`reorder_home`, through HSC's `Hsc/UserSections` API); rows it does not know keep their place
  below. A row a user drags by hand is put back at the next sync. Emby users also get **Your Next Watch** (HSC's per-user picks, computed on the
  server, no internet). home-rows only edits HSC sources named `homelab: ...`.
- Seasonal rows: Halloween 10/01-10/31, Thanksgiving 11/01-11/30, Christmas 12/01-12/31,
  Valentine's Day 02/01-02/14.
- Nothing touches media files. Kometa has no Radarr/Sonarr section, so lists never add downloads.
  Load is API calls only; the first Kometa run looks up every title on TMDb (Hidden Gems,
  International, Sitcoms, Limited/Finished Series) and is cached for 60 days after that.

## Keys

`docs/home_rows_keys.py` (run on the Mac) puts `PLEX_TOKEN`, `HOME_ROWS_EMBY_KEY` (an Emby API
key named "home-rows"), `TMDB_API_KEY` and `MDBLIST_API_KEY` into `stacks/gpu/secrets.sops.env`.
The TMDb and MDBList keys are Goobs's free accounts, saved by hand in
`~/.config/homelab/tmdb.env` / `mdblist.env` first (the script deletes those files after).

## Health

`http://192.168.1.52:8097/health` answers 200 when Kometa's log and the last Emby sync are both
under 26 hours old, 503 otherwise, with JSON saying which rows are off. Homepage ("Home rows")
and Uptime Kuma ("Home rows (Kometa + Emby)", digest only) watch it.

## Changing rows

- Add or rename a Plex row in `movies.yml`/`shows.yml`; if it is a list row, add the same name
  to `ROWS` in `home_rows.py`. Check with Kometa's validator before deploying:
  `docker compose run --rm kometa --validate-file /config/homelab/movies.yml`.
- Run Kometa now: `docker compose run --rm kometa --run` (on the gpu VM in /opt/stacks/gpu).
- Re-sync Emby now: `docker compose restart home-rows`.
- Order of Emby home rows: set it once on Goobs's account, then HSC's Home Screen Sync can copy
  the layout to the other users.

## Not done / later

- "Finished Series" means ended on TMDb; it does not check that every season is on disk.
- No poster overlays (Goobs: off). Collection posters are Plex's and Emby's own collages.
