// Tdarr flow logic (Custom JS Function): movie or episode -> 8-bit HEVC (Quick Sync) in MKV.
// Lives at /app/configs/hevc/flow.js on the gpu VM; the flow's custom function just requires it.
// Rules agreed with Goobs 2026-10-21 (movies), extended to TV 2026-10-23:
//   - only Radarr's / Sonarr's current file (radarr_map.json / sonarr_map.json, see media_map.py),
//     not the review/multi-video folders
//   - 8-bit H.264 above ~5.5 Mbps; any other codec (10-bit HEVC, AV1, VC-1, ...) only when it is over
//     the grab caps from PR #45 (movies 70 MB/min, episodes 65 MB/min); 4K and HDR are left alone
//   - video: hevc_qsv Main, global_quality 22, preset faster (2026-10-23: 3x the speed of slow on the
//     Skylake iGPU, same SSIM, ~5% bigger); audio: DTS/DTS-HD/TrueHD -> EAC3 640k, rest copied
//   - keep English + the title's original language + und/untagged + forced subs; drop other
//     dubs and commentary (never drop the last audio track)
//   - replace only if the result is at least 20% smaller and passes a full decode check
//   - same file name (extension becomes .mkv); every original is moved to the NAS holding folder
//     (to-delete/tdarr-originals/<date>/...), never deleted here: Goobs empties it after checking
//   - stops encoding while the NAS has less than 3 TB free (originals pile up until emptied)
//   - Radarr rescans the movie / Sonarr rescans the series afterwards
const fs = require('fs');
const path = require('path');
const http = require('http');
const { spawnSync } = require('child_process');

const CFG = '/app/configs/hevc';
const HOLD_ROOT = '/mnt/nfs/to-delete/tdarr-originals';
const MIN_FREE = 3e12;
const MIN_BPS = 5.5e6;
const LOG = '/app/logs/hevc-run.tsv';
const LIBS = {
  movie: { root: '/mnt/nfs/Media/Movies', hold: '', map: 'radarr_map.json', capMbMin: 70,
    arr: { port: 7878, key: 'radarr-api-key', cmd: (id) => ({ name: 'RescanMovie', movieIds: [id] }) } },
  tv: { root: '/mnt/nfs/Media/TVShows', hold: 'TVShows', map: 'sonarr_map.json', capMbMin: 65,
    arr: { port: 8989, key: 'sonarr-api-key', cmd: (id) => ({ name: 'RescanSeries', seriesId: id }) } },
};

const LANGS = {
  English: ['eng', 'en'], Japanese: ['jpn', 'ja'], Korean: ['kor', 'ko'], German: ['ger', 'deu', 'de'],
  French: ['fre', 'fra', 'fr'], Spanish: ['spa', 'es'], Chinese: ['chi', 'zho', 'zh', 'cmn', 'yue'],
  Russian: ['rus', 'ru'], Norwegian: ['nor', 'nob', 'nno', 'no'], Swedish: ['swe', 'sv'],
  Italian: ['ita', 'it'], Danish: ['dan', 'da'], Thai: ['tha', 'th'], Turkish: ['tur', 'tr'],
  Polish: ['pol', 'pl'], Indonesian: ['ind', 'id'], Latvian: ['lav', 'lv'], Hebrew: ['heb', 'he'],
  Serbian: ['srp', 'scc', 'sr'], Dutch: ['dut', 'nld', 'nl'], Finnish: ['fin', 'fi'],
  Portuguese: ['por', 'pt'], Hindi: ['hin', 'hi'], Cantonese: ['yue', 'chi', 'zho'],
};

const readJson = (f, d) => { try { return JSON.parse(fs.readFileSync(f, 'utf8')); } catch (e) { return d; } };
const log = (row) => fs.appendFileSync(LOG, `${[new Date().toISOString(), ...row].join('\t')}\n`);

function rescan(arr, id) {
  const key = fs.readFileSync(`${CFG}/${arr.key}`, 'utf8').trim();
  const body = JSON.stringify(arr.cmd(id));
  return new Promise((resolve) => {
    const req = http.request({
      host: '192.168.1.51', port: arr.port, path: '/api/v3/command', method: 'POST', timeout: 30000,
      headers: { 'X-Api-Key': key, 'Content-Type': 'application/json', 'Content-Length': Buffer.byteLength(body) },
    }, (res) => { res.resume(); resolve(res.statusCode); });
    req.on('error', (e) => resolve(`error ${e.message}`));
    req.end(body);
  });
}

function probe(file) {
  const r = spawnSync('ffprobe', ['-v', 'error', '-print_format', 'json', '-show_format', '-show_streams', file],
    { encoding: 'utf8', maxBuffer: 64 * 1024 * 1024 });
  return JSON.parse(r.stdout || '{}');
}

module.exports = async (args) => {
  const src = args.inputFileObj._id;
  const skip = (why) => { args.jobLog(`skip: ${why}`); log([src, '', '', '', `skip: ${why}`]); return { outputFileObj: args.inputFileObj, outputNumber: 2, variables: args.variables }; };

  const kind = Object.keys(LIBS).find((k) => src.startsWith(`${LIBS[k].root}/`));
  if (!kind) return skip('not under Movies or TVShows');
  const lib = LIBS[kind];
  const map = readJson(`${CFG}/${lib.map}`, {});
  const excluded = readJson(`${CFG}/excluded_folders.json`, []);
  const item = map[src]; // {id, lang, title}: the movie, or the episode's series
  if (!item) return skip(`not ${kind === 'tv' ? 'Sonarr' : 'Radarr'}'s current file`);
  if (excluded.some((d) => src.startsWith(`${d}/`))) return skip('excluded folder');

  const df = spawnSync('df', ['-B1', '--output=avail', lib.root], { encoding: 'utf8' });
  const free = parseInt((df.stdout || '').trim().split('\n').pop(), 10);
  if (!(free > MIN_FREE)) { log([src, '', '', '', `paused: NAS free ${Math.round(free / 1e12 * 10) / 10} TB`]); throw new Error('NAS has less than 3 TB free; empty to-delete/tdarr-originals'); }

  // Fail in seconds, not after a 30-minute encode, if the folder isn't writable.
  const probeFile = path.join(path.dirname(src), `.tdarr-write-test-${process.pid}`);
  try { fs.writeFileSync(probeFile, ''); fs.unlinkSync(probeFile); } catch (e) {
    log([src, '', '', '', `FAILED: folder not writable (${e.code})`]);
    throw new Error(`folder not writable: ${path.dirname(src)} (${e.code})`);
  }

  const info = probe(src);
  const streams = info.streams || [];
  const v = streams.find((s) => s.codec_type === 'video' && !(s.disposition || {}).attached_pic);
  if (!v) return skip('video is missing');
  if (v.field_order && !['progressive', 'unknown'].includes(v.field_order)) return skip(`interlaced (${v.field_order})`);
  if ((v.width || 0) > 1920 || (v.height || 0) > 1080) return skip(`${v.width}x${v.height} (4K is left for review)`);
  if (['smpte2084', 'arib-std-b67'].includes(v.color_transfer)) return skip(`HDR (${v.color_transfer})`);
  const dur = parseFloat((info.format || {}).duration || 0);
  const size = fs.statSync(src).size;
  if (!dur) return skip('no duration');
  const mbMin = size / 1e6 / (dur / 60);
  // Fast path: 8-bit H.264 decoded on the iGPU. Everything else is decoded in software (the Skylake
  // iGPU has no 10-bit HEVC or AV1 decoder) and only worth it above the grab cap.
  const fast = v.codec_name === 'h264' && ['yuv420p', 'yuvj420p'].includes(v.pix_fmt);
  if (fast) {
    if (size * 8 / dur < MIN_BPS) return skip(`bitrate ${(size * 8 / dur / 1e6).toFixed(1)} Mbps`);
  } else if (mbMin <= lib.capMbMin) return skip(`${v.codec_name} ${v.pix_fmt} at ${Math.round(mbMin)} MB/min (cap ${lib.capMbMin})`);

  // Tracks to keep
  const keepLangs = new Set(['eng', 'en', 'und', '', ...(LANGS[item.lang] || [])]);
  const lang = (s) => ((s.tags || {}).language || '').toLowerCase();
  const title = (s) => ((s.tags || {}).title || '').toLowerCase();
  const audio = streams.filter((s) => s.codec_type === 'audio');
  let keepA = audio.filter((s) => keepLangs.has(lang(s)) && !/commentary/.test(title(s)));
  if (!keepA.length) keepA = audio.slice(0, 1);
  const subs = streams.filter((s) => s.codec_type === 'subtitle');
  const keepS = subs.filter((s) => (keepLangs.has(lang(s)) || (s.disposition || {}).forced) && !/commentary/.test(title(s))
    && ['subrip', 'ass', 'ssa', 'hdmv_pgs_subtitle', 'dvd_subtitle', 'mov_text', 'webvtt'].includes(s.codec_name));

  const enc = [];
  enc.push('-map', `0:${v.index}`);
  keepA.forEach((s) => enc.push('-map', `0:${s.index}`));
  keepS.forEach((s) => enc.push('-map', `0:${s.index}`));
  enc.push('-map', '0:t?');
  enc.push('-c:v', 'hevc_qsv', '-profile:v', 'main', '-preset', 'faster', '-global_quality', '22', '-look_ahead_depth', '40');
  keepA.forEach((s, i) => {
    const prof = (s.profile || '').toLowerCase();
    if (s.codec_name === 'truehd' || (s.codec_name === 'dts')) {
      enc.push(`-c:a:${i}`, 'eac3', `-b:a:${i}`, '640k');
      if ((s.channels || 2) > 6) enc.push(`-ac:a:${i}`, '6');
    } else enc.push(`-c:a:${i}`, 'copy');
    if (prof && s.codec_name === 'dts') args.jobLog(`audio ${i}: dts ${prof} -> eac3`);
  });
  keepS.forEach((s, i) => enc.push(`-c:s:${i}`, ['mov_text', 'webvtt'].includes(s.codec_name) ? 'srt' : 'copy'));
  enc.push('-c:t', 'copy', '-map_metadata', '0', '-map_chapters', '0', '-max_muxing_queue_size', '4096',
    '-disposition:a:0', 'default');

  const base = path.basename(src, path.extname(src));
  const work = path.join(args.workDir, `${base}.mkv`);
  const hw = ['-init_hw_device', 'qsv=hw', '-filter_hw_device', 'hw'];
  const runs = [
    [...hw, '-i', src, '-vf', 'format=nv12,hwupload=extra_hw_frames=64', ...enc], // software decode (also 10 -> 8 bit)
  ];
  if (fast) runs.unshift([...hw, '-hwaccel', 'qsv', '-hwaccel_output_format', 'qsv', '-c:v', 'h264_qsv', '-i', src, ...enc]);
  const t0 = Date.now();
  let ok = false;
  for (const r of runs) {
    const res = spawnSync('nice', ['-n', '10', 'ffmpeg', '-hide_banner', '-nostdin', '-y', '-v', 'error', ...r, work],
      { encoding: 'utf8', maxBuffer: 64 * 1024 * 1024 });
    if (res.status === 0 && fs.existsSync(work)) { ok = true; break; }
    args.jobLog(`ffmpeg failed: ${(res.stderr || '').slice(-800)}`);
  }
  const secs = Math.round((Date.now() - t0) / 1000);
  if (!ok) { log([src, size, '', secs, 'FAILED encode']); throw new Error('encode failed'); }

  // Health check: same length, a video stream, all kept audio, and a clean full decode.
  const out = probe(work);
  const oDur = parseFloat((out.format || {}).duration || 0);
  const oA = (out.streams || []).filter((s) => s.codec_type === 'audio').length;
  const dec = spawnSync('nice', ['-n', '10', 'ffmpeg', '-v', 'error', '-hwaccel', 'qsv', '-c:v', 'hevc_qsv', '-i', work, '-map', '0:v:0', '-f', 'null', '-'],
    { encoding: 'utf8', maxBuffer: 64 * 1024 * 1024 });
  // The Intel driver prints "libva info: ..." lines on stderr even with -v error; those aren't errors.
  const decErr = (dec.stderr || '').split('\n').filter((l) => l.trim() && !/^libva info:/.test(l)).join('\n');
  const newSize = fs.statSync(work).size;
  const problem = Math.abs(oDur - dur) > 2 ? `duration ${oDur} vs ${dur}`
    : oA !== keepA.length ? `audio tracks ${oA} vs ${keepA.length}`
      : dec.status !== 0 || decErr ? `decode errors: ${decErr.slice(0, 300)}`
        : newSize > 0.8 * size ? `only ${Math.round(100 - newSize * 100 / size)}% smaller` : '';
  if (problem) {
    fs.unlinkSync(work);
    log([src, size, newSize, secs, `kept original: ${problem}`]);
    return skip(problem);
  }

  if (args.dryRun) { log([src, size, newSize, secs, `dry run ok; output left at ${work}`]); return { outputFileObj: { _id: work }, outputNumber: 3, variables: args.variables }; }

  // Replace: copy next to the original, then retire the original, then rename into place.
  const dest = path.join(path.dirname(src), `${base}.mkv`);
  const tmp = `${dest}.tdarr-tmp`;
  if (spawnSync('cp', [work, tmp]).status !== 0 || fs.statSync(tmp).size !== newSize) throw new Error('copy to NAS failed');
  fs.unlinkSync(work);
  // Retire the original into the holding folder (copy, verify size, then remove the source).
  const day = new Date().toLocaleDateString('en-CA', { timeZone: 'America/Los_Angeles' });
  const held = path.join(HOLD_ROOT, day, lib.hold, path.relative(lib.root, src));
  fs.mkdirSync(path.dirname(held), { recursive: true });
  if (spawnSync('cp', ['-p', src, held]).status !== 0 || fs.statSync(held).size !== size) {
    fs.unlinkSync(tmp); throw new Error('holding copy failed');
  }
  fs.unlinkSync(src);
  fs.appendFileSync(path.join(HOLD_ROOT, day, 'MANIFEST.tsv'), `${size}\t${src}\t${dest}\n`);
  const fate = `original held at ${held}`;
  fs.renameSync(tmp, dest);
  const rs = await rescan(lib.arr, item.id);
  log([src, size, newSize, secs, `ok (${Math.round(100 - newSize * 100 / size)}% smaller); ${fate}; ${kind === 'tv' ? 'sonarr' : 'radarr'} rescan ${rs}; dest ${dest}`]);
  args.jobLog(`done: ${size} -> ${newSize} bytes in ${secs}s; ${fate}`);
  return { outputFileObj: { _id: dest }, outputNumber: 1, variables: args.variables };
};
