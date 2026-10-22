// Tool recent_fixes: what the sysadmin ran, planned in report-only mode, or saw resolve (no input).
const sd = $getWorkflowStaticData('global');
return toolText({
  fixes_last_7_days: (sd.fixlog || []).slice(-40).map((f) => `${fmtT(f.t)} ${f.host}: ${f.cmd} -> ${f.status}${f.detail ? ` (${f.detail})` : ''} [${f.source === 'chat' ? 'chat' : 'automatic'}; ${f.title}]`),
  would_have_fixed: (sd.wouldfix || []).slice(-40).map((f) => `${fmtT(f.t)} ${f.host}: ${f.cmd} [${f.title}]`),
  resolved_since_digest: (sd.resolved || []).slice(-40).map((r) => `${r.title} (open ${ago(r.first)}, cleared ${fmtT(r.resolved_at)})`),
});
