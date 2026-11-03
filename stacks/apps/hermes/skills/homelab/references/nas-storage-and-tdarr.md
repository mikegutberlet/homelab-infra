# NAS space, the Tdarr HEVC run, Recyclarr scores, OldData

The pool is `DeepData` (RAIDZ1, no snapshots, so every deletion is final). The NAS dataset already has
lz4 compression and recordsize 1M, and dedup is off.

## Planning space savings: order and rules

1. Measure first: `df -h /mnt/nfs` on any VM, then per-folder sizes with the chunked `find -printf` method
   (SKILL.md, door section). TV, not Movies, holds most of the pool. Rank the work by bytes returned.
2. Re-encoding to 8-bit HEVC (Tdarr) is the only big lever on video. Target and limits are in
   `docs/wiki/06-media-library.md` (8-bit HEVC Main, no 10-bit/AV1, no 4K).
3. Duplicates: match by title through Radarr/Sonarr for media (copies are different encodes, never
   byte-identical). Use byte-identical matching (size, then SHA-256) only for OldData and loose files.
4. Never recommend ZFS dedup: the DDT costs RAM the 20 GB TrueNAS VM doesn't have, the ratio on video is
   about 1.0x, and it only applies to new writes.
5. Compression and recordsize only affect new writes. Tdarr rewrites every file it converts, so those pick up
   whatever the dataset is set to.
6. ISOs: game discs (PS2/PSP) shrink as CHD/CSO; Windows/Linux installer ISOs are already compressed inside
   (skip them); a DVD/BD ISO in Movies should be remuxed or re-grabbed.
7. Space from Tdarr only shows up when its held originals are deleted: `tdarr-upkeep` does that after
   14 days. Moving anything to `to-delete` frees nothing until the folder is emptied.

## Tdarr: how it runs and how to check it

- Two nodes: `gpu-vm` (server, gpu VM) and `transcode-vm` (transcode VM). Both run the same flow;
  libraries `Movies` and `TVShows` (`tvShowsHevc01`). 17:00-23:00 PT is a scheduled pause, so no jobs
  in that window is normal.
- Truth is the run logs, not the container logs: gpu `/opt/appdata/tdarr/logs/hevc-run.tsv`, transcode
  `/opt/appdata/tdarr-node/logs/hevc-run.tsv` (time, path, size, new size, secs, result). Count results per
  day and per library (`grep '/TVShows/' ... | cut -c1-10`, then `sort | uniq -c` on your side). A library
  that stops appearing has been switched off: nothing records that in the repo.
- Switching a library: `sudo python3 /opt/stacks/gpu/tdarr-flow/tdarr_setup.py --tv=on|off` on gpu (Goobs's;
  the door's never-list blocks Python and anything touching Tdarr). Jobs that failed in 0 s (missing
  render node) sit in the library's Error/Cancelled tab until requeued in the UI.
- The flow reads Radarr/Sonarr file maps; `tdarr-upkeep` on media writes them daily to
  `Media/.tdarr-staging/maps`. A file missing from the map is skipped, and Tdarr never revisits a skipped file:
  before turning a library on, check the maps' mtime (`ls -l /mnt/nfs/Media/.tdarr-staging/maps`).
- `flow.js` is not deployed by the stacks play: after a change, Goobs copies it to
  `/opt/appdata/tdarr/configs/hevc/` on gpu and `/opt/appdata/tdarr-node/configs/hevc/` on transcode
  (`docs/wiki/operations/portalgun-moves.md`). Verify with a `grep -c` for the new code on both.
- Held originals: `to-delete/tdarr-originals/<day>/` with `MANIFEST.tsv`; `tdarr-upkeep` deletes a day after
  14 days only where the replacement is in place, and lists each file in `PURGED.tsv`. This is the one
  automatic NAS deletion Goobs allowed (D18 amendment). Don't widen it without his explicit yes.

## Recyclarr: when an unwanted release was grabbed

1. Find the grab: `grep -r -h 'Report sent to' /opt/appdata/sonarr/logs/` (Radarr's log wording differs).
2. Check which profile the series/movie is on. Sonarr also has stock profiles Recyclarr doesn't manage; every
   custom format scores 0 there, so the 10-bit penalty and x265 bonus don't apply. Fix: move the series to the
   managed profile and delete the stock ones (Goobs, in the UI).
3. Only then look at scores. The best H.264 WEB-DL scores about 1850 (WEB Tier 01 1700 + service 75 +
   HD Streaming Boost 75); a bonus must exceed that to win. Foreign profiles end upgrades at score 2000 (a
   dub), so a bonus of 2000 there would make a subtitles-only release final.
4. Recyclarr applies changes at its 00:00 PT run; its debug log under `/opt/appdata/recyclarr/logs/cli/`
   shows "up to date" or what it changed.

## OldData

Goobs keeps everything in OldData except byte-identical duplicates of something kept elsewhere.
`docs/olddata_dedupe.py` finds and deletes those; it runs on TrueNAS as root, dry run first
(`ssh root@192.168.1.209 'python3 -' < docs/olddata_dedupe.py`, then `'python3 - --delete'`). Running it is
Goobs's. Twins only in `to-delete`, `Backups`, `sabnzb` or `.tdarr-staging` don't count as kept elsewhere.
