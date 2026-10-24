// Tool recent_fixes: what the sysadmin ran, planned in report-only mode, or saw resolve (no input).
// Newest first, each with its age, plus "now", so "in the last hour" can be answered without guessing.
const sd = $getWorkflowStaticData('global');
const line = (f) => `${ago(f.t)} ago (${fmtT(f.t)}) ${f.host}: ${f.cmd} -> ${f.status}${f.detail ? ` (${f.detail})` : ''} [${f.source === 'chat' ? 'chat, confirmed by Goobs' : 'automatic'}; ${f.title}]`;
const ran = (sd.fixlog || []).slice(-40).reverse();
// The same planned fix repeats every 15 minutes while its issue is open: one line per host+command, with a count.
const planned = [];
for (const f of (sd.wouldfix || []).slice().reverse()) {
  const same = planned.find((q) => q.host === f.host && q.cmd === f.cmd);
  if (same) same.n += 1; else planned.push({ ...f, n: 1 });
}
return toolText({
  now: fmtT(now),
  fixes_that_ran_last_7_days: ran.length ? ran.map(line) : 'none',
  fixes_that_ran_last_hour: ran.filter((f) => now - f.t < H).length,
  planned_but_NOT_run_report_only_mode: planned.map((f) => `${f.n > 1 ? `${f.n}x, last ` : ''}${ago(f.t)} ago ${f.host}: ${f.cmd} [${f.title}]`),
  resolved_since_digest: (sd.resolved || []).slice(-40).map((r) => `${r.title} (open ${dur(r.resolved_at - r.first)}, cleared ${fmtT(r.resolved_at)})`),
});
