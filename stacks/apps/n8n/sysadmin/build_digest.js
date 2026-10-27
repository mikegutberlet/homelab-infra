// Build digest: the 08:00 email. Open issues (new since yesterday first), the fix attempts of the
// last 24 h and whether they helped, what resolved, trends over 24 h and 7 d, the legacy webhook
// events, and what is quiet. Reads only: "Digest sent" clears events and resolved records once the
// email is out, up to the cutoff passed along here.
const sd = $getWorkflowStaticData('global');
const S = SETTINGS;
const all = Object.values(sd.issues || {});
const day = new Date().toLocaleDateString('en-US', { timeZone: S.TZ, dateStyle: 'medium' });
const order = { critical: 0, warning: 1, info: 2 };
const since = now - 24 * H;
const hm = (ms) => new Date(ms).toLocaleTimeString('en-US', { timeZone: S.TZ, hour: '2-digit', minute: '2-digit' });
let text = '';
const section = (title, lines) => { if (lines.length) text += `${title}\n${lines.map((l) => `  ${l}`).join('\n')}\n\n`; };

// Fix attempts: a started one with no answer after 10 minutes never came back.
const fixed = (sd.fixlog || []).filter((f) => f.t > since);
const st = (f) => (f.status === 'started' && now - f.t > 10 * 60e3 ? 'no answer' : f.status);
const tried = (i) => {
  const t = fixed.filter((f) => f.key === i.key);
  if (!t.length) return '';
  const l = t[t.length - 1];
  return `; tried ${[...new Set(t.map((f) => f.cmd))].join(' / ')} ${t.length}x, last ${st(l)} at ${hm(l.t)}`;
};

// A VM that went silent: its other issues are as of its last report, so they fold into that line.
// One that reports again is not silent, even while its silent issue waits out CLEAR_AFTER_RUNS.
const reporting = S.HOSTS.filter((h) => sd.last && sd.last[h] && now - sd.last[h].received < S.SILENT_AFTER_MIN * 60e3);
const silent = new Set(S.HOSTS.filter((h) => sd.issues && sd.issues[`infra:silent:${h}`] && !reporting.includes(h)));
const frozen = (h) => all.filter((i) => i.host === h).length;
const issues = all.filter((i) => !silent.has(i.host) && !(i.key.startsWith('infra:silent:') && reporting.includes(i.key.split(':')[2])));

// Needs you: criticals and warnings, the ones that are new since yesterday first.
const line = (i) => `[${i.sev.toUpperCase()}] ${i.title.slice(0, 200)}\n      ${i.detail}\n      since ${fmtT(i.first)}`
  + `${i.flaps ? `, came and went ${i.flaps}x` : ''}${i.miss ? ', not seen in the last check' : ''}${tried(i)}`
  + `${i.fix_held ? `; not retrying: ${i.fix_held}` : ''}${i.fix_planned && !S.FIXES_ENABLED ? `; would run: ${i.fix_planned}` : ''}`
  + `${i.key.startsWith('infra:silent:') && frozen(i.key.split(':')[2]) ? `; ${frozen(i.key.split(':')[2])} older issue(s) from its last report not listed` : ''}`;
const open = issues.filter((i) => i.sev !== 'info');
const fresh = open.filter((i) => i.first > since).sort((a, b) => order[a.sev] - order[b.sev] || b.first - a.first);
const old = open.filter((i) => i.first <= since).sort((a, b) => order[a.sev] - order[b.sev] || a.first - b.first);
section(`NEW SINCE YESTERDAY (${fresh.length})`, fresh.map(line));
section(`STILL OPEN (${old.length})`, old.map(line));

// What the sysadmin tried: one line per issue and command, and whether the issue is still open.
const groups = new Map();
for (const f of fixed) {
  const g = `${f.key === 'chat' || !f.key ? f.id || f.t : f.key}|${f.cmd}`;
  if (!groups.has(g)) groups.set(g, []);
  groups.get(g).push(f);
}
// Open and seen in the last check; one that missed a check is clearing (it resolves after CLEAR_AFTER_RUNS).
const stillOpen = (f) => f.key && f.key !== 'chat' && sd.issues && sd.issues[f.key] && !sd.issues[f.key].miss;
const clearing = (f) => f.key && f.key !== 'chat' && sd.issues && sd.issues[f.key] && sd.issues[f.key].miss > 0;
const attempts = [...groups.values()].map((fs) => {
  const l = fs[fs.length - 1];
  const detail = l.detail ? ` (${String(l.detail).replace(/^ok: /, '').slice(0, 200)})` : '';
  return `${l.host}: ${l.cmd}${fs.length > 1 ? ` ${fs.length}x` : ''} at ${fs.map((f) => `${hm(f.t)} ${st(f)}`).join(', ')}${detail}  [${l.title}]${stillOpen(l) ? '  STILL OPEN' : clearing(l) ? `  not seen since ${hm(sd.issues[l.key].miss_at)}` : ''}`;
});
section(`FIX ATTEMPTS IN THE LAST 24 H (${fixed.length})`, attempts);
const would = (sd.wouldfix || []).filter((f) => f.t > since);
if (!S.FIXES_ENABLED) section(`WOULD HAVE FIXED, REPORT-ONLY MODE (${would.length})`, would.map((f) => `${fmtT(f.t)}  ${f.host}: ${f.cmd}  [${f.title}]`));

// Resolved since the last digest: one line per issue, leaving out notes and issues open again.
const res = new Map();
for (const r of sd.resolved || []) {
  if (r.sev === 'info' || (sd.issues && sd.issues[r.key])) continue;
  const g = res.get(r.key) || { n: 0 };
  res.set(r.key, { ...r, n: g.n + 1 });
}
section(`RESOLVED SINCE YESTERDAY (${res.size})`, [...res.values()].map((r) => `${r.title} (open ${dur(r.resolved_at - r.first)}, cleared ${fmtT(r.resolved_at)}${r.n > 1 || r.flaps ? `; came and went ${Math.max(r.n, (r.flaps || 0) + 1)}x` : ''})`));

// Info-level notes.
const info = issues.filter((i) => i.sev === 'info');
section(`FOR INFORMATION (${info.length})`, info.map((i) => `${i.title}${i.detail ? `: ${i.detail}` : ''}`));

// Trends.
const tl = [];
const pick = (arr, msAgo) => { const t = now - msAgo; let best = null; for (const p of arr || []) if (!best || Math.abs(p.t - t) < Math.abs(best.t - t)) best = p; return best; };
const infra = (sd.trends || {}).infra || [];
if (infra.length) {
  const a = infra[infra.length - 1], d1 = pick(infra, 24 * H), d7 = pick(infra, 7 * 24 * H);
  tl.push(`DeepData free: ${tb(a.pool_free)} now${d1 ? `, ${tb(d1.pool_free)} yesterday` : ''}${d7 && d7 !== d1 ? `, ${tb(d7.pool_free)} a week ago` : ''}`);
}
for (const h of S.HOSTS) {
  const tr = (sd.trends || {})[h] || []; if (!tr.length) continue;
  const a = tr[tr.length - 1], d1 = pick(tr, 24 * H);
  const parts = [`mem ${a.mem}%`];
  // rs_total counts every restart Evaluate report saw (resets and recreates included).
  if (d1 && a.rs_total != null && d1.rs_total != null) parts.push(`${Math.max(0, a.rs_total - d1.rs_total)} container restarts in 24 h`);
  if (a.backup) parts.push(`backup ${size(a.backup)}`);
  if (a.tdarr_done != null) parts.push(`Tdarr ${d1 && d1.tdarr_done != null ? a.tdarr_done - d1.tdarr_done : a.tdarr_done} files done in 24 h, ${a.tdarr_queued} queued`);
  if (a.stuck) parts.push(`${a.stuck} stuck arr items`);
  tl.push(`${h}: ${parts.join(', ')}`);
}
section('TRENDS', tl);

// Legacy webhook events (Kuma digest monitors, Diun, Scrutiny, backup script).
const ev = (sd.events || []).filter((e) => Date.parse(e.t) <= now);
const names = { kuma: 'App status (non-critical)', diun: 'Image updates available', scrutiny: 'Disk health', backup: 'Backups', other: 'Other' };
const evGroups = {};
for (const e of ev) (evGroups[e.source] = evGroups[e.source] || []).push(e);
for (const [src, items] of Object.entries(evGroups)) section(`${(names[src] || src).toUpperCase()} (${items.length})`, items.map((e) => `${fmtT(Date.parse(e.t))}  ${e.line}`));

// The investigator's spend (GET /usage by "Investigator usage"; history holds the days before today).
let inv = null;
try { inv = $('Investigator usage').first().json; } catch (e) { inv = null; }
if (inv && typeof inv.usd === 'number') {
  const y = (inv.history || []).slice(-1)[0];
  const line = (d) => `${d.cases} case${d.cases === 1 ? '' : 's'} (${d.auto || 0} from alerts), $${Number(d.usd).toFixed(2)}`;
  section('INVESTIGATOR', [`${y ? `Yesterday ${line(y)}; ` : ''}today so far ${line(inv)}. Cap $${Number(inv.daily_usd).toFixed(2)} a day.`]);
}

// Quiet: which VMs report and have nothing open.
const clean = reporting.filter((h) => !open.some((i) => i.host === h));
text += `QUIET\n  ${reporting.length} of ${S.HOSTS.length} VMs reporting; nothing open on ${clean.join(', ') || 'none of them'}.\n`
  + `  Fixes ${S.FIXES_ENABLED ? `enabled (at most ${S.FIX_PER_HOUR} per target an hour, ${S.FIX_PER_DAY} a day)` : 'report-only'}.\n`;

// Subject: new and open counts; a fix counts as fixed once its issue is gone or clearing. Per-item
// stuck keys (<app>-stuck:<id>, before 2026-10-24) were dropped, not resolved, so they do not count.
const auto = [...groups.values()].map((fs) => fs[fs.length - 1])
  .filter((f) => f.source !== 'chat' && f.key && f.key !== 'chat' && !/:(sonarr|radarr)-stuck:\d+$/.test(f.key));
const done = new Set(auto.filter((f) => !stillOpen(f) && fixed.some((x) => x.key === f.key && x.status === 'ok')).map((f) => f.key)).size;
const notHelped = new Set(auto.filter((f) => stillOpen(f)).map((f) => f.key)).size;
const crit = open.filter((i) => i.sev === 'critical').length, warn = open.length - crit;
const nothing = !open.length && !fixed.length && !would.length && !res.size && !ev.length;
const subject = nothing ? `Homelab digest ${day}: all quiet`
  : `Homelab digest ${day}: ${fresh.length} new; ${crit} critical, ${warn} warnings open; ${done} fixed${notHelped ? `, ${notHelped} not helped by a fix` : ''}`;
return [{ json: { subject, text, cutoff: now } }];
