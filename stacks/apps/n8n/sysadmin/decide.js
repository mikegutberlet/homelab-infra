// Decide: takes one evaluator's issue list, updates the issue store (first/last seen,
// resolved), and emits what should happen now: at most one critical email and the fixes the
// policy allows. With FIXES_ENABLED false the fixes are logged as "would run" instead.
const sd = $getWorkflowStaticData('global');
sd.issues = sd.issues || {}; sd.resolved = sd.resolved || []; sd.fixlog = sd.fixlog || []; sd.wouldfix = sd.wouldfix || [];
const S = SETTINGS;
const { host, scope, issues } = $input.first().json;
if (!scope) return [];
const out = [];
const active = new Set(issues.map((i) => i.key));

// Resolved: keys of this scope that were open and are gone now.
for (const [key, it] of Object.entries(sd.issues)) {
  if (it.host === scope && !active.has(key)) {
    sd.resolved.push({ key, title: it.title, sev: it.sev, first: it.first, resolved_at: now });
    delete sd.issues[key];
  }
}
sd.resolved = sd.resolved.slice(-200);

// Upsert the active ones.
const toEmail = [];
for (const i of issues) {
  const prev = sd.issues[i.key];
  const it = prev ? { ...prev, ...i, first: prev.first, count: (prev.count || 0) + 1, last: now } : { ...i, first: now, last: now, count: 1 };
  if (i.sev === 'critical' && (!it.emailed_at || now - it.emailed_at > S.EMAIL_REPEAT_H * H)) { toEmail.push(it); it.emailed_at = now; }
  sd.issues[i.key] = it;
}

// Fix policy: one per target per hour, three per day; crash loops escalate from restart to stop.
sd.fixlog = sd.fixlog.filter((f) => now - f.t < 7 * 24 * H);
const planned = [];
for (const i of issues) {
  if (!i.fix) continue;
  let { verb, target } = i.fix;
  if (verb === 'stop' && i.fix.escalate) {
    // First two crash-loop rounds try a restart; the third stops it.
    const tried = sd.fixlog.filter((f) => f.host === host && f.target === target && f.verb === 'restart' && now - f.t < 24 * H).length;
    if (tried < 2) verb = 'restart';
  }
  const tkey = `${host}:${verb}:${target}`;
  const lastHour = sd.fixlog.filter((f) => f.tkey === tkey && now - f.t < H).length;
  const lastDay = sd.fixlog.filter((f) => f.tkey === tkey && now - f.t < 24 * H).length;
  if (lastHour >= S.FIX_PER_HOUR || lastDay >= S.FIX_PER_DAY) { sd.issues[i.key].fix_held = `limit reached (${lastDay} today)`; continue; }
  const cmd = `${verb} ${target}`;
  const entry = { t: now, host, verb, target, tkey, cmd, key: i.key, title: i.title, status: S.FIXES_ENABLED ? 'started' : 'would-run' };
  if (S.FIXES_ENABLED) {
    sd.fixlog.push(entry);
    planned.push({ kind: 'fix', host, ip: S.HOST_IP[host], cmd, key: i.key, title: i.title, then: i.fix.then || null });
  } else {
    sd.wouldfix.push(entry); sd.wouldfix = sd.wouldfix.slice(-300);
  }
  sd.issues[i.key].fix_planned = cmd;
}

// One email for all new criticals of this run.
if (toEmail.length) {
  const lines = toEmail.map((it) => {
    const plan = it.fix_planned ? (S.FIXES_ENABLED ? `\n    Doing now: ${it.fix_planned}` : `\n    Would do (fixes off): ${it.fix_planned}`) : '';
    return `- ${it.title}\n    ${it.detail}\n    since ${fmtT(it.first)}${plan}`;
  });
  const subject = toEmail.length === 1 ? `[Homelab] ${toEmail[0].title}` : `[Homelab] ${toEmail.length} critical issues (${scope})`;
  const text = `${lines.join('\n')}\n\nOpen issues right now: ${Object.keys(sd.issues).length}. Fixes ${S.FIXES_ENABLED ? 'enabled' : 'in report-only mode'}.\nThe morning digest has the full picture.`;
  out.push({ json: { kind: 'email', subject, text } });
}
for (const p of planned) out.push({ json: p });
return out;
