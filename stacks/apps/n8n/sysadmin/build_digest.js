// Build digest: the 08:00 email. Open issues, what was fixed (or would have been), what
// resolved itself, trends over 24 h and 7 d, the legacy webhook events, and what is quiet.
const sd = $getWorkflowStaticData('global');
const S = SETTINGS;
const issues = Object.values(sd.issues || {});
const day = new Date().toLocaleDateString('en-US', { timeZone: S.TZ, dateStyle: 'medium' });
const order = { critical: 0, warning: 1, info: 2 };
const since = now - 24 * H;
let text = '';
const section = (title, lines) => { if (lines.length) text += `${title}\n${lines.map((l) => `  ${l}`).join('\n')}\n\n`; };

// Needs you: open criticals and warnings.
const open = issues.filter((i) => i.sev !== 'info').sort((a, b) => order[a.sev] - order[b.sev] || a.first - b.first);
section(`NEEDS YOU (${open.length})`, open.map((i) => `[${i.sev.toUpperCase()}] ${i.title}\n      ${i.detail}\n      since ${fmtT(i.first)}${i.fix_held ? `; fix held: ${i.fix_held}` : ''}${i.fix_planned && !S.FIXES_ENABLED ? `; would run: ${i.fix_planned}` : ''}`));

// Fixed overnight.
const fixed = (sd.fixlog || []).filter((f) => f.t > since);
section(`FIXED IN THE LAST 24 H (${fixed.length})`, fixed.map((f) => `${fmtT(f.t)}  ${f.host}: ${f.cmd} -> ${f.status}${f.detail ? ` (${f.detail})` : ''}  [${f.title}]`));
const would = (sd.wouldfix || []).filter((f) => f.t > since);
if (!S.FIXES_ENABLED) section(`WOULD HAVE FIXED, REPORT-ONLY MODE (${would.length})`, would.map((f) => `${fmtT(f.t)}  ${f.host}: ${f.cmd}  [${f.title}]`));
sd.wouldfix = (sd.wouldfix || []).filter((f) => f.t > since);

// Resolved since the last digest.
const resolved = sd.resolved || [];
section(`RESOLVED SINCE YESTERDAY (${resolved.length})`, resolved.map((r) => `${r.title} (open ${ago(r.first)}, cleared ${fmtT(r.resolved_at)})`));
sd.resolved = [];

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
  if (d1 && a.restarts != null && d1.restarts != null) parts.push(`${Math.max(0, a.restarts - d1.restarts)} container restarts in 24 h`);
  if (a.backup) parts.push(`backup ${gb(a.backup)}`);
  if (a.tdarr_done != null) parts.push(`Tdarr ${d1 && d1.tdarr_done != null ? a.tdarr_done - d1.tdarr_done : a.tdarr_done} files done in 24 h, ${a.tdarr_queued} queued`);
  if (a.stuck) parts.push(`${a.stuck} stuck arr items`);
  tl.push(`${h}: ${parts.join(', ')}`);
}
section('TRENDS', tl);

// Legacy webhook events (Kuma digest monitors, Diun, Scrutiny, backup script).
const ev = sd.events || []; sd.events = [];
const names = { kuma: 'App status (non-critical)', diun: 'Image updates available', scrutiny: 'Disk health', backup: 'Backups', other: 'Other' };
const groups = {};
for (const e of ev) (groups[e.source] = groups[e.source] || []).push(e);
for (const [src, items] of Object.entries(groups)) section(`${(names[src] || src).toUpperCase()} (${items.length})`, items.map((e) => `${fmtT(Date.parse(e.t))}  ${e.line}`));

// Quiet.
const reporting = S.HOSTS.filter((h) => sd.last && sd.last[h] && now - sd.last[h].received < S.SILENT_AFTER_MIN * 60e3);
text += `QUIET\n  ${reporting.length} of ${S.HOSTS.length} VMs reporting (${reporting.join(', ') || 'none'}); fixes ${S.FIXES_ENABLED ? 'enabled' : 'report-only'}.\n`;

const nothing = !open.length && !fixed.length && !would.length && !resolved.length && !ev.length;
const subject = nothing ? `Homelab digest ${day}: all quiet` : `Homelab digest ${day}: ${open.filter((i) => i.sev === 'critical').length} critical, ${open.filter((i) => i.sev === 'warning').length} warnings, ${fixed.length} fixed`;
return [{ json: { subject, text } }];
