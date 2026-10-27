// Decide: takes one evaluator's issue list, updates the issue store (first/last seen,
// resolved), and emits what should happen now: at most one critical email and the fixes the
// policy allows. With FIXES_ENABLED false the fixes are logged as "would run" instead.
// The emails and fixes leave through "Hand off" (homelab/act), so this run is over in about a
// second: n8n saves the whole static data when a run ends, and a run that waited on SSH would
// overwrite whatever the other VMs' reports wrote meanwhile.
const sd = $getWorkflowStaticData('global');
sd.issues = sd.issues || {}; sd.resolved = sd.resolved || []; sd.fixlog = sd.fixlog || []; sd.wouldfix = sd.wouldfix || [];
sd.recent = sd.recent || {}; sd.held = sd.held || {};
const S = SETTINGS;
const { host, scope, issues, partial } = $input.first().json;
if (!scope) return [];
const out = [];
const active = new Set(issues.map((i) => i.key));

// Resolved: an open key of this scope that this run did not raise counts a miss; after
// CLEAR_AFTER_RUNS misses in a row it resolves, as of the first miss. A partial report (the agent
// could not build one) counts no misses. Issues of a VM that is not in HOSTS and has stopped
// reporting resolve with the next infra run.
const fresh = (h) => sd.last && sd.last[h] && now - sd.last[h].received < S.SILENT_AFTER_MIN * 60e3;
for (const [key, it] of Object.entries(sd.issues)) {
  const gone = scope === 'infra' && it.host !== 'infra' && !S.HOSTS.includes(it.host) && !fresh(it.host);
  if (!gone) {
    if (it.host !== scope || active.has(key) || partial) continue;
    it.miss = (it.miss || 0) + 1;
    if (!it.miss_at) it.miss_at = now;
    if (it.miss < S.CLEAR_AFTER_RUNS) continue;
  }
  const rec = { key, title: it.title, sev: it.sev, first: it.first, resolved_at: it.miss_at || now, flaps: it.flaps || 0, emailed_at: it.emailed_at || null };
  sd.resolved.push(rec);
  sd.recent[key] = rec;
  delete sd.issues[key];
}
sd.resolved = sd.resolved.slice(-200);
for (const [key, r] of Object.entries(sd.recent)) if (now - r.resolved_at > S.FLAP_WINDOW_H * H) delete sd.recent[key];
// Chat proposals whose Confirm window passed: pruned here, so a chat question writes nothing.
if (sd.chat && sd.chat.pending) for (const [k, p] of Object.entries(sd.chat.pending)) if (now - p.t > S.CHAT_CONFIRM_MIN * 60e3) delete sd.chat.pending[k];

// Upsert the active ones. fix_planned and fix_held describe this run only. An issue that resolved
// less than FLAP_WINDOW_H ago keeps its first-seen time and its last email, and counts one more flap.
const toEmail = [];
let freshCritical = false; // a critical emailed for the first time (not a 6-hourly repeat): the investigator looks at it
for (const i of issues) {
  const prev = sd.issues[i.key];
  const back = prev ? null : sd.recent[i.key];
  const it = prev
    ? { ...prev, ...i, first: prev.first, count: (prev.count || 0) + 1, last: now, miss: 0, miss_at: null, fix_planned: null, fix_held: null }
    : { ...i, first: back ? back.first : now, last: now, count: 1, flaps: back ? (back.flaps || 0) + 1 : 0, emailed_at: back ? back.emailed_at : null };
  if (back) delete sd.recent[i.key];
  if (i.sev === 'critical' && (!it.emailed_at || now - it.emailed_at > S.EMAIL_REPEAT_H * H)) { freshCritical = freshCritical || !it.emailed_at; toEmail.push(it); it.emailed_at = now; }
  sd.issues[i.key] = it;
}

// Fix policy: one per target per hour, three per day; crash loops escalate from restart to stop.
// A service stopped on purpose (sd.held: set here, and by Evaluate report for a stop confirmed in
// chat) is not restarted until someone starts it again; a fix the ops door refused is not retried
// for a day.
sd.fixlog = sd.fixlog.filter((f) => now - f.t < 7 * 24 * H);
const planned = [];
for (const i of issues) {
  if (!i.fix) continue;
  const it = sd.issues[i.key];
  let { verb, target } = i.fix;
  if (verb === 'stop' && i.fix.escalate) {
    // First two crash-loop rounds try a restart; the third stops it.
    const tried = sd.fixlog.filter((f) => f.host === host && f.target === target && f.verb === 'restart' && now - f.t < 24 * H).length;
    if (tried < 2) verb = 'restart';
  }
  const held = sd.held[`${host}:${target}`];
  if (verb === 'restart' && held) { it.fix_held = `stopped ${held.by === 'chat' ? 'from chat' : 'by the sysadmin'} at ${fmtT(held.t)}; it stays down until you start it`; continue; }
  const tkey = `${host}:${verb}:${target}`;
  const log = sd.fixlog.filter((f) => f.tkey === tkey && now - f.t < 24 * H);
  const refused = log.filter((f) => f.status === 'refused').pop();
  if (refused) { it.fix_held = `the ops door refused it at ${fmtT(refused.t)} (${refused.detail || 'no detail'}); not retrying today`; continue; }
  const lastHour = log.filter((f) => now - f.t < H);
  if (log.length >= S.FIX_PER_DAY) { it.fix_held = `gave up for today after ${log.length} tries; next try after ${fmtT(log[0].t + 24 * H)}`; continue; }
  if (lastHour.length >= S.FIX_PER_HOUR) { it.fix_held = `tried at ${fmtT(lastHour[lastHour.length - 1].t)}; next try after ${fmtT(lastHour[0].t + H)}`; continue; }
  const cmd = `${verb} ${target}`;
  const then = i.fix.then || null;
  const id = `auto-${now}-${i.key}`;
  const entry = { id, t: now, host, verb, target, tkey, cmd, key: i.key, title: i.title, sev: i.sev, then, status: S.FIXES_ENABLED ? 'started' : 'would-run' };
  if (S.FIXES_ENABLED) {
    sd.fixlog.push(entry);
    planned.push({ kind: 'fix', id, host, cmd, key: i.key, title: i.title });
    // Hold it from now on, so the missing-service rule in this same run cannot plan a restart.
    if (verb === 'stop') sd.held[`${host}:${target}`] = { t: now, by: 'sysadmin', id };
  } else {
    sd.wouldfix.push(entry); sd.wouldfix = sd.wouldfix.slice(-300);
  }
  it.fix_planned = then ? `${cmd}, then restart ${then}` : cmd;
}

// One email for all new criticals of this run.
if (toEmail.length) {
  const lines = toEmail.map((it) => {
    const plan = it.fix_planned ? (S.FIXES_ENABLED ? `\n    Doing now: ${it.fix_planned}` : `\n    Would do (fixes off): ${it.fix_planned}`)
      : it.fix_held ? `\n    Not retrying now: ${it.fix_held}` : '';
    const tried = sd.fixlog.filter((f) => f.key === it.key && now - f.t < 24 * H && f.status !== 'started');
    const triedLine = tried.length ? `\n    Tried in the last 24 h: ${tried.map((f) => `${f.cmd} (${f.status}, ${fmtT(f.t)})`).join('; ')}` : '';
    return `- ${it.title}\n    ${it.detail}\n    since ${fmtT(it.first)}${it.flaps ? `, came and went ${it.flaps}x` : ''}${plan}${triedLine}`;
  });
  const subject = toEmail.length === 1 ? `[Homelab] ${toEmail[0].title}` : `[Homelab] ${toEmail[0].title} (+${toEmail.length - 1} more)`;
  const text = `${lines.join('\n')}\n\nOpen issues right now: ${Object.keys(sd.issues).length}. Fixes ${S.FIXES_ENABLED ? 'enabled' : 'in report-only mode'}.\nThe morning digest has the full picture.`;
  out.push({ json: { kind: 'email', subject, text, investigate: freshCritical } });
}
for (const p of planned) out.push({ json: p });
return out;
