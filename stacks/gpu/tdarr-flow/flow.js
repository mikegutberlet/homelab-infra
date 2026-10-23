// Tdarr flow logic (Custom JS Function): H.264 movie -> 8-bit HEVC (Quick Sync) in MKV.
// Lives at /app/configs/hevc/flow.js on the gpu VM; the flow's custom function just requires it.
// Rules agreed with Goobs 2026-10-21:
//   - only Radarr's current file for a movie (radarr_map.json), not the review/multi-video folders
//   - only 8-bit H.264 above ~5.5 Mbps; HEVC/AV1/everything else is left alone
//   - video: hevc_qsv Main, global_quality 22; audio: DTS/DTS-HD/TrueHD -> EAC3 640k, rest copied
//   - keep English + the movie's original language + und/untagged + forced subs; drop other
//     dubs and commentary (never drop the last audio track)
//   - replace only if the result is at least 20% smaller and passes a full decode check
//   - same file name (extension becomes .mkv); every original is moved to the NAS holding folder
//     (to-delete/tdarr-originals/<date>/...), never deleted here: Goobs empties it after checking
//   - stops encoding while the NAS has less than 3 TB free (originals pile up until emptied)
//   - Radarr rescans the movie afterwards
const fs = require('fs');
const path = require('path');
const http = require('http');
const { spawnSync } = require('child_process');

const CFG = '/app/configs/hevc';
const HOLD_ROOT = '/mnt/nfs/to-delete/tdarr-originals';
const MIN_FREE = 3e12;
const MIN_BPS = 5.5e6;
const LOG = '/app/logs/hevc-run.tsv';

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

function rescanRadarr(movieId) {
  const key = fs.readFileSync(`${CFG}/radarr-api-key`, 'utf8').trim();
  const body = JSON.stringify({ name: 'RescanMovie', movieIds: [movieId] });
  return new Promise((resolve) => {
    const req = http.request({
      host: '192.168.1.51', port: 7878, path: '/api/v3/command', method: 'POST', timeout: 30000,
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

  const map = readJson(`${CFG}/radarr_map.json`, {});
  const excluded = readJson(`${CFG}/excluded_folders.json`, []);
  const movie = map[src];
  if (!movie) return skip('not Radarr\'s current file');
  if (excluded.some((d) => src.startsWith(`${d}/`))) return skip('excluded folder');

  const df = spawnSync('df', ['-B1', '--output=avail', '/mnt/nfs/Media/Movies'], { encoding: 'utf8' });
  const free = parseInt((df.stdout || '').trim().split('\n').pop(), 10);
  if (!(free > MIN_FREE)) { log([src, '', '', '', `paused: NAS free ${Math.round(free / 1e12 * 10) / 10} TB`]); throw new Error('NAS has less than 3 TB free; empty to-delete/tdarr-originals'); }

  // Fail in seconds, not after a 30-minute encode, if the movie folder isn't writable.
  const probeFile = path.join(path.dirname(src), `.tdarr-write-test-${process.pid}`);
  try { fs.writeFileSync(probeFile, ''); fs.unlinkSync(probeFile); } catch (e) {
    log([src, '', '', '', `FAILED: folder not writable (${e.code})`]);
    throw new Error(`folder not writable: ${path.dirname(src)} (${e.code})`);
  }

  const info = probe(src);
  const streams = info.streams || [];
  const v = streams.find((s) => s.codec_type === 'video' && !(s.disposition || {}).attached_pic);
  if (!v || v.codec_name !== 'h264') return skip(`video is ${v ? v.codec_name : 'missing'}`);
  if (!['yuv420p', 'yuvj420p'].includes(v.pix_fmt)) return skip(`pixel format ${v.pix_fmt}`);
  if (v.field_order && !['progressive', 'unknown'].includes(v.field_order)) return skip(`interlaced (${v.field_order})`);
  const dur = parseFloat((info.format || {}).duration || 0);
  const size = fs.statSync(src).size;
  if (!dur) return skip('no duration');
  if (size * 8 / dur < MIN_BPS) return skip(`bitrate ${(size * 8 / dur / 1e6).toFixed(1)} Mbps`);

  // Tracks to keep
  const keepLangs = new Set(['eng', 'en', 'und', '', ...(LANGS[movie.lang] || [])]);
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
  enc.push('-c:v', 'hevc_qsv', '-profile:v', 'main', '-preset', 'slow', '-global_quality', '22', '-look_ahead_depth', '40');
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
    [...hw, '-hwaccel', 'qsv', '-hwaccel_output_format', 'qsv', '-c:v', 'h264_qsv', '-i', src, ...enc],
    [...hw, '-i', src, '-vf', 'format=nv12,hwupload=extra_hw_frames=64', ...enc], // software decode fallback
  ];
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
  const held = path.join(HOLD_ROOT, day, path.relative('/mnt/nfs/Media/Movies', src));
  fs.mkdirSync(path.dirname(held), { recursive: true });
  if (spawnSync('cp', ['-p', src, held]).status !== 0 || fs.statSync(held).size !== size) {
    fs.unlinkSync(tmp); throw new Error('holding copy failed');
  }
  fs.unlinkSync(src);
  fs.appendFileSync(path.join(HOLD_ROOT, day, 'MANIFEST.tsv'), `${size}\t${src}\t${dest}\n`);
  const fate = `original held at ${held}`;
  fs.renameSync(tmp, dest);
  const rs = await rescanRadarr(movie.id);
  log([src, size, newSize, secs, `ok (${Math.round(100 - newSize * 100 / size)}% smaller); ${fate}; radarr rescan ${rs}; dest ${dest}`]);
  args.jobLog(`done: ${size} -> ${newSize} bytes in ${secs}s; ${fate}`);
  return { outputFileObj: { _id: dest }, outputNumber: 1, variables: args.variables };
};
