// Tool lab_overview: what the sysadmin knows right now (no input).
const S = SETTINGS;
const sd = $getWorkflowStaticData('global');
const order = { critical: 0, warning: 1, info: 2 };
const issues = Object.values(sd.issues || {}).sort((a, b) => order[a.sev] - order[b.sev] || a.first - b.first)
  .map((i) => ({ host: i.host, sev: i.sev, title: i.title, detail: i.detail, since: `${fmtT(i.first)} (${ago(i.first)} ago)`, fix_planned: i.fix_planned || undefined, fix_held: i.fix_held || undefined }));
const reports = {};
for (const h of S.HOSTS) {
  const r = (sd.last || {})[h];
  reports[h] = r ? `reported ${ago(r.received)} ago` : 'no report yet';
}
const infra = ((sd.trends || {}).infra || []).slice(-1)[0];
const day = (sd.fixlog || []).filter((f) => now - f.t < 24 * H);
return toolText({
  now: fmtT(now),
  automatic_fixes: S.FIXES_ENABLED ? 'on' : 'report-only (planned, not run)',
  chat_fixes: S.CHAT_FIXES_ENABLED ? 'on, after Goobs confirms' : 'off: propose only',
  open_issues: issues,
  vm_reports: reports,
  deepdata_free: infra && infra.pool_free ? tb(infra.pool_free) : 'unknown',
  fixes_last_24h: day.length,
  would_have_fixed_last_24h: (sd.wouldfix || []).filter((f) => now - f.t < 24 * H).length,
  resolved_since_digest: (sd.resolved || []).length,
});
