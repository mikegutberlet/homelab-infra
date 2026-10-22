# Subtitles: always available, accurate

Started 2026-10-22. Goal (Goobs): every movie and show has accurate subtitles. Bazarr on the
media VM does the work; this page records the settings that live in Bazarr's own config
(`/opt/appdata/bazarr`, backed up nightly) and the parts that are in this repo.

## What the audit found (2026-10-22, before changes)

- Bazarr 1.6.2 healthy, linked to Sonarr and Radarr; providers OpenSubtitles.com, Subdl,
  Addic7ed, Jimaku, YIFY, none throttled.
- One language profile ("Eng/Japanese Subs", en + ja) and default profiles **off**: 1,977 of
  3,227 movies and 316 of 549 series had no profile, so Bazarr never searched for them.
- Wanted on the profiled titles: 4,392 episodes and 323 movies, almost all missing Japanese.
- Machine/AI-translated subtitles accepted; movie minimum score 70 (series 90); subsync off;
  embedded PGS/VobSub counted as satisfying the language; stale bind IP 192.168.1.14.

## Target settings (Bazarr UI, Settings pages)

Languages
- Enabled languages: English, Japanese.
- Profiles: **English + Japanese** (en then ja, HI: no, forced: no) and **English** (en, same
  options). Goobs chose Japanese on everything (2026-10-22), so the default profile for series
  and movies is English + Japanese; English is only for titles tagged `en-only` in Sonarr/Radarr.
- The old "Eng/Japanese Subs" profile stays until nothing uses it, then is deleted.

Providers
- OpenSubtitles.com and Subdl: machine- and AI-translated subtitles stay **allowed**: they are
  the only Japanese source for most Western titles. Bazarr cannot allow them per language.
- Added, no login needed: Gestdown and Embedded Subtitles (extracts text subtitles already
  inside the video to a .srt; it cannot read PGS images). Podnapisi is gone from Bazarr 1.6.2;
  Subsource needs an account and API key (optional, Goobs's call).
- Addic7ed kept (frequent timeouts; Gestdown covers the same shows).

Subtitles
- Minimum score: series 90, movies 70 (both unchanged; a higher movie floor would reject most
  Japanese matches, which are the right film but not the exact release).
- Automatic synchronization **on**, ffsubsync, audio-based, with the score thresholds on at
  their defaults (series 96, movies 86): only subtitles that scored below that get re-timed.
- Upgrade previously downloaded subtitles: on, 7 days, every 12 h (unchanged).
- Later, after the first backfill: *Ignore embedded PGS/VobSub subtitles* on, so Roku-friendly
  text subtitles get fetched for titles that only have image subtitles.

General
- Bind address 0.0.0.0 (was 192.168.1.14 from the old VM); backup folder `/config/backup`
  (the old path was unwritable, so Bazarr's own backups never worked; the nightly VM backup
  covers it anyway). Form login for the UI: Goobs sets it (Settings, General, Security).

## In this repo

- `stacks/media/subtitle-profiles/assign_profiles.py` (service `subtitle-profiles`): daily,
  gives every title in Bazarr that has no profile, or still sits on "Eng/Japanese Subs", the
  English + Japanese profile, or English when the title carries the tag `en-only` in
  Sonarr/Radarr. Hand-picked profiles are never touched.
  Needs `BAZARR_API_KEY` in `secrets.sops.env`. `DRY_RUN=true ONCE=true` prints the plan.

## How the backfill runs

Bazarr's own wanted-search task does it, one provider at a time, so it is light on the NAS;
no manual "search all". OpenSubtitles.com free accounts allow 20 downloads a day (VIP:
1,000), so a backlog in the thousands takes weeks on the free tier. Provider throttling is
normal and clears by itself.

## Later

- Whisper (speech-to-text) as a last-resort provider for titles no source has, on CPU on
  the apps or ai VM (no spare GPU; the iGPU is Tdarr's).
- Plex and Emby pick up new subtitle files only on a library scan over NFS; add an hourly
  refresh or Bazarr's Plex hook.
- Review the Japanese subtitles already downloaded onto non-Japanese movies (about 870
  files); move to a holding folder only with Goobs's OK.
