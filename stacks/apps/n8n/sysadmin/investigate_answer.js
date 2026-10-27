// Investigator sub-workflow, last step: one item back to the caller, {status, answer, proposal}.
// status: ok, timeout, max_turns, max_budget_usd (answered, maybe partly), or busy, budget, limit,
// error, off (no investigation; the chat's own model answers instead). Chat finish passes the answer
// and proposal to Goobs unchanged; the alert run emails the answer.
const req = $('Investigate request').first().json;
const j = $input.first().json || {};
if (req.route === 'skip') return [{ json: { status: req.status, answer: req.answer, proposal: null } }];
if (j.error || typeof j.answer !== 'string') {
  const why = j.error ? String(j.error.message || j.error).slice(0, 200) : 'no answer';
  return [{ json: { status: 'error', answer: `The investigator did not answer (${why}).`, proposal: null } }];
}
const p = j.proposal && typeof j.proposal === 'object' ? j.proposal : null;
return [{ json: { status: String(j.status || 'ok'), answer: j.answer, proposal: p, cost_usd: j.cost_usd, case: j.case, subject: req.subject } }];
