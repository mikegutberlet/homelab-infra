# Media library projects

Two projects were running on the library when this wiki was written: shrinking the movie library
with Tdarr (which turned into a Radarr clean-up first), and building live TV channels in ErsatzTV.
This page records the facts they established and the decisions Goobs made, so nobody has to
re-survey 18 TB.

## Playback requirements (hard constraints)

- Every file must stay playable by Plex and Emby on the **Roku TVs and the Pixel phone**, ideally as
  direct play.
- Target format: **8-bit HEVC (H.265) Main profile, MKV**, audio Dolby Digital / E-AC3 / AAC.
  No 10-bit, no AV1 (the Rokus play 10-bit HEVC, but the Skylake iGPU cannot decode 10-bit HEVC or
  AV1 in hardware, so any transcode of those hits the CPU).
- **No 4K anywhere**: too much storage, and the network and devices struggle with it. Sonarr and
  Radarr profiles are capped at 1080p. The 76 existing 4K files (0.48 TB) are candidates for
  conversion to 1080p HEVC later.
- No Roku plays DTS. The Lanikai Roku transcodes every subtitled play, even SRT, which is a
  "Burn Subtitles" setting in its Plex app, not a file problem. Makua and The Strand direct-play
  subtitled files.

## What the survey found (read-only, 2026-10-21)

| Fact | Value |
| --- | --- |
| Movie folders on the NAS | 3,889 folders, 18.2 TB; 1,194 of them empty |
| Tracked by Radarr before the project | 2,550 folders |
| Untracked folders | 1,339 (6.2 TB): 883 are movies Radarr listed as *missing* (4.1 TB; one "Search missing" click would have re-downloaded all of it), 227 are second copies (208 weaker = 1.07 TB, 15 better = 74 GB, 2 not allowed by the profile), 231 match nothing (161 are spelling variants of movies Radarr has) |
| Radarr entries pointing at the dead `/mnt/backupsmb` path | 21 |
| Codecs | H.264 2,387 files / 12.6 TB (1,020 over 6 Mbps = 7.45 TB; 482 over 10 Mbps = 4.1 TB); HEVC 1,431 (1,199 of them 10-bit); AV1 65 (all 10-bit). Nearly all 1080p |
| Seeding (hardlink count over 1) | none |
| Audio | 577 DTS, 76 DTS-HD MA, 39 TrueHD tracks; 760 non-English tracks and 311 commentaries total at most 0.28 TB |
| Subtitles | 4,316 PGS (image) tracks |
| Junk | 51 sample/trailer videos (2.79 GB), 140 scene `.nfo`, 79 `.sfv`, 55 `.txt`, 28 `.html`, 20 `.nzb`, 9 `.par2`, 6 `.url`, 5 `.exe`: 393 files, 3.17 GB |
| Needs Goobs's review | 35 `.aac` files (33 GB, look like split audio pieces); 8 multi-video folders (7.4 GB extra) |
| Posters and fanart | about 6,500 images, 3.2 GB, **kept** (Emby uses them) |
| Plex vs scan count gap | Plex says 3,368 movies, the first scan found 2,567 (12.2 TB): the difference is the untracked folders above |

Clip test (2-minute Blu-ray clip, 15.7 Mbps source, SSIM against the original):

| Encoder | Size | Speed | SSIM |
| --- | --- | --- | --- |
| QSV HEVC q21 | 13% | 79 fps | 0.979 |
| QSV HEVC q23 | 8.5% | 81 fps | 0.977 |
| QSV HEVC q25 | 6.2% | 83 fps | 0.976 |
| x265 crf20 (CPU) | 12.4% | 13 fps | 0.982 |
| x265 crf22 (CPU) | 7.8% | 20 fps | 0.981 |

Quick Sync at q22 takes about 35 minutes per movie; TrueHD to E-AC3 640k cuts audio to a third. The
whole H.264 backlog (1,666 tracked movies, 9.1 TB) is expected to take 1 to 2 months in the
background and return roughly 3.5 to 4.5 TB.

## Tdarr settings

Goobs chose "defaults" on 2026-10-21 22:48 UTC and later dropped the device test and the 5-movie
pilot (23:32 UTC) to go straight to the full run.

| Setting | Value |
| --- | --- |
| Library | `Media/Movies`; since 2026-10-23 also `Media/TVShows` (library `tvShowsHevc01`, created paused). Both share the one GPU worker. Code: `stacks/gpu/tdarr-flow/` (`flow.js` is copied to `/app/configs/hevc/flow.js`; `media_map.py` refreshes the Radarr/Sonarr maps; `tdarr_setup.py --movies=on --tv=on` sets up both libraries) |
| Candidates | 8-bit H.264 above about 2.5 GB per hour (about 5.5 Mbps); since 2026-10-23 also any other codec (10-bit HEVC, AV1, VC-1, ...) over the grab caps of PR #45 (movies 70 MB/min, episodes 65 MB/min). Skip 4K and HDR (awaiting review), other HEVC and AV1 files, files not in the Radarr/Sonarr map, seeding files, the 8 multi-video folders, anything under `to-delete`, and the 33 review-relink folders |
| Video | Quick Sync HEVC 8-bit Main, MKV, quality 22, preset `faster` since 2026-10-23 (was `slow`: on the Skylake iGPU `faster` runs about 8x real time instead of 2.8x, SSIM 0.958 vs 0.959, about 5% bigger). A second worker adds only ~45% at `faster` and nothing at `slow`, and would compete with Plex/Emby transcodes, so it stays at 1. Non-H.264 input is decoded in software (no 10-bit HEVC or AV1 decoder on the iGPU) |
| Audio | Keep English plus the title's original language; drop other dubs and commentary; DTS, DTS-HD and TrueHD converted to E-AC3 640k; AC3/AAC/E-AC3 copied |
| Subtitles | Keep English and original language; always keep forced subs and untagged (`und`) tracks |
| Replace only if | at least 20% smaller **and** the output passes a health check; filenames kept |
| How a file is replaced | The new file is copied to `Media/.tdarr-staging/` (outside every library folder, same share so the final rename works), the original is copied to the holding folder and removed, then the new file is renamed into place. Until 2026-10-25 the copy sat next to the original as `*.tdarr-tmp`; on 2026-10-23 one hung in a kernel NFS bug and froze Plex's scanner for two days. Leftover `*.tdarr-tmp` files from before are incomplete copies |
| Radarr / Sonarr | H.265 penalty off (PR #6); the flow triggers a Radarr movie or Sonarr series rescan per file; the first 5 are verified by hand (new file shown, no download queued) |
| Schedule | 1 worker, low priority, paused 17:00 to 23:00 Pacific |
| Safety nets | First day's originals (about 40 files) moved to `to-delete/2026-10-21/tdarr-originals` instead of deleted; SSIM spot-check on 3 early outputs; run log under `~/HomeLab/tdarr-review/run/` on the Mac |
| Mount | Tdarr's `/mnt/nfs/Media` is read-write and `/mnt/nfs/to-delete` is mounted for the holding folder (`stacks/gpu/compose.yaml`, PR #8 merged 2026-10-22); Unmanic stays read-only |

## Radarr clean-up (the bigger win)

Decision card 2026-10-21 23:16 UTC: **relink and sort duplicates**. Steps and state:

1. Radarr backup taken (`radarr_backup_v6.4.4.10685_2026.10.21_16.19.06.zip`).
2. Recycling bin pointed at the holding folder, auto-cleanup off.
3. Relink of 850 safe matches by path edit (no files moved) in batches of 50 with an undo log
   (`~/HomeLab/tdarr-review/relink/`): **660 relinked** as of 2026-10-22; 6 have no file at all
   (American History X, Big Hero 6, The Woman in Black, Sonic 3, Open Season, Hoppers); 33 need
   review. Radarr went from files for 2,550 to **3,227 of 3,732** movies.
4. 393 junk files moved to `to-delete/2026-10-21` with a manifest (undo script `~/holding-undo.sh`
   on the gpu VM).
5. Found during the relink: 149 movies have two or more raw folders, so 184 more second copies to
   sort in addition to the 210 weaker and 15 better copies already identified.
6. Goobs said "go" on 2026-10-22 01:25 UTC for: best copy per movie, the 15 better-copy imports,
   weaker copies to the holding folder, then the Tdarr run. **Running at the time of writing.**
7. Queued after it, by another thread: rename 337 Radarr movie folders from `Title2026` to
   `Title (2026)`; the Tdarr run is held until that finishes so the two never overlap.

The holding folder is emptied only after Goobs confirms, around **2026-11-04**.

Still open from the survey: the `.aac` files and multi-video folders (Goobs to review), the 231
unmatched folders, the 21 `/mnt/backupsmb` entries, the 76 4K files, and the "Wrong movie and TV
cover art" issue being handled in its own thread.

## ErsatzTV channels

Set up on 2026-10-22 in the "ErsatzTV channel curation" thread. Plex is the media source (only the
Movies and TV Shows libraries; never Courses or adult libraries). FFmpeg profile VAAPI on
`/dev/dri/renderD128` at 8 Mbps with AAC stereo. Goobs picked all 11 proposed channels (Cartoon
Network, Family Movies, Sitcoms, Action, Franchise Marathons, Sci-Fi, Fright Night, Throwback,
Crime & Procedurals, Discovery, Reality) and the build was in progress. Plex sees ErsatzTV as an
HDHomeRun tuner at `http://192.168.1.52:8409`. Inventory TSVs and an undo log are in
`~/HomeLab/ersatztv/` on the Mac. Keep the gpu VM's load light while Tdarr runs; never move media for
a channel.

## Download hygiene learned the hard way

- Failed imports for two months created a retry storm that got the GingaDADDY account capped. Fix:
  one app user, hardlinks on, and per-indexer grab limits in Prowlarr.
- Recyclarr with the wrong instance names rewrote Goobs's profiles once; restore from the app's own
  backup is quick, so take one before any profile change.
- Upgrades after a profile change arrive through RSS only; never trigger a library-wide search with a
  pool at 80%.
