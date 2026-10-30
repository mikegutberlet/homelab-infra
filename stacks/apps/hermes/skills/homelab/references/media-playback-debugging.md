# Debugging "it won't play" in Plex (gpu VM)

Read-only through `ssh gpu`. Report the cause plus client-side fixes; change nothing without Goobs.

## Where things are

- Container `gpu-plex-1`; media mounted ro at `/mnt/nfs/Media` (Movies, TVShows).
- Logs: `L="/opt/appdata/plex/Library/Application Support/Plex Media Server/Logs"`
  - `$L/Plex Media Server.log` (+ `.1`..`.5`, rotated on every restart, so old sessions vanish fast)
  - `$L/Plex Transcoder Statistics.log`: last session's chosen streams and segment list.
- `docker logs gpu-plex-1` holds almost nothing about playback; use the log files.

## Steps

1. Confirm the file: `ssh gpu 'find /mnt/nfs/Media/Movies -iname "*<word>*"'` (size, folder name).
2. Find the title's decision: `ssh gpu "grep -i '<title word>' \"$L/Plex Media Server.log\"" | tail -40`.
   Key lines: `MDE: ... Direct Play is disabled`, `limitation applies: 6 > 2`, `There is an audio enhancement`,
   `Reached Decision`, and the `Job running: ... Plex Transcoder` command (look at `-codec:1`, `ochl=`).
3. Identify the client: `grep 'Request: \[<ip>'` around the play time; the profile line names it
   (`using augmented profile Roku-7.x`).
4. Tell client-gave-up from server-crash: `Killing job` + exit `-9` right after a client `transcode/universal/stop`
   means the client aborted; then `/:/timeline ... state=stopped ... playbackTime=0` and a `postplay` request
   mean it never rendered a frame (user sees "loads, then nothing"). A transcoder error would show in
   `[Transcoder]`/ERROR lines instead.
5. Compare with a title that just played on the same client (`timeline ... state=playing ... timeToFirstFrame`)
   and diff its transcoder command. The difference is the cause.

## Known cause (diagnosed from logs 2026-10-29; the client fix is not yet confirmed)

- Roku + "Boost voices" (`boostDialog=1`) + 5.1 source: Plex re-encodes audio to 5.1 E-AC3 via
  EasyAudioEncoder (`eac3_eae`, `ochl='5.1(side)'`) despite the Roku's 2-channel limit; the Roku stops at 0.
  Stereo sources get stereo AAC and play. Fix on the client: Boost voices off, or re-enable Direct Play in
  the Roku Plex app (it was off, forcing every title to transcode), or set Roku audio output to stereo.

## Side checks worth a line in the answer

- `ssh gpu 'journalctl -t homelab-ops --since -2h'`: a recent Plex restart by the sysadmin explains rotated logs.
- Folder not in Radarr's `Title (Year)` naming (raw release name, `.1` suffix) means a manual or duplicate
  import; offer to check Radarr, not a playback cause. Radarr's DB is readable read-only:
  `ssh media 'grep -a -o -E ".{0,120}<Release.Name>.{0,160}" /opt/appdata/radarr/radarr.db' | tr -c '[:print:]\n' .`
  shows its stored path, import and grab dates, and the naming format (`StandardMovieFormat`, `MovieFolderFormat`).
  `docker logs media-radarr-1` keeps only recent lines, so it rarely names an old import.
