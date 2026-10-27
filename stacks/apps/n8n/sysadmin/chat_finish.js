// Chat finish: the agent answered. This run waited on the model, so it writes no static data: a fix
// the agent proposed goes to Chat state (homelab/chatstate, via "Register proposal"), which numbers
// and parks it, and "Proposal reply" then shows Goobs the Confirm and Cancel buttons.
// The proposal is the agent's last propose_fix call that passed the check. n8n 2.44 (Agent 3.1,
// ToolsAgent V3: the engine runs the tool nodes) returns, with returnIntermediateSteps on,
//   intermediateSteps: [{ action: { tool, toolInput, toolCallId, log, type, messageLog }, observation }]
// tool is the tool node's name made safe ("tool propose_fix" -> "tool_propose_fix"), toolInput the
// model's arguments, observation the JSON of the tool node's output items ('[{"response":"..."}]').
// Older shapes (plain name, the answer as a string, toolInput as a JSON string or under input /
// toolParameters) are read too. Only {text} (and a proposal) ever goes back to the page or bot.
// When the agent handed the question to the investigator (tool investigate), Goobs gets the
// investigator's own answer word for word, and its proposal (if any) is the one registered: the
// quick model's retelling would lose detail, and it never sees a script it could change.
const st = $('Chat prepare').first().json;
const out = $input.first().json;
const text = typeof out.output === 'string' ? out.output.trim() : (out.error ? `The agent failed: ${String(out.error.message || out.error).slice(0, 200)}` : '(no answer)');
const parse = (v) => { if (typeof v !== 'string') return v; try { return JSON.parse(v); } catch (e) { return v; } };
const said = (obs) => {
  const o = parse(obs);
  const one = (x) => (typeof x === 'string' ? x : x && typeof x === 'object' ? String(x.response ?? x.output ?? x.text ?? '') : '');
  return (Array.isArray(o) ? o.map(one).join('\n') : one(o)).trim();
};
const input = (v) => {
  let o = parse(v);
  for (const k of ['toolParameters', 'input', 'query']) if (o && typeof o === 'object' && !o.host && o[k]) o = parse(o[k]);
  return o && typeof o === 'object' && o.host ? proposalInput(o) : null;
};
let found = false, fix = null;
for (const s of Array.isArray(out.intermediateSteps) ? out.intermediateSteps : []) {
  const a = (s && s.action) || {};
  if (!/(^|_)propose_fix$/.test(String(a.tool || '')) || !said(s.observation).startsWith(PROPOSAL_OK)) continue;
  found = true;
  fix = input(a.toolInput);
}
// The investigator's answer: {status, answer, proposal}, inside the tool's item list.
const investigation = (obs) => {
  const seen = [];
  const walk = (v, depth) => {
    if (depth > 4) return;
    v = parse(v);
    if (Array.isArray(v)) v.forEach((x) => walk(x, depth + 1));
    else if (v && typeof v === 'object') {
      if (typeof v.answer === 'string') seen.push(v);
      else Object.values(v).forEach((x) => walk(x, depth + 1));
    }
  };
  walk(obs, 0);
  return seen.pop() || null;
};
let inv = null;
for (const s of Array.isArray(out.intermediateSteps) ? out.intermediateSteps : []) {
  const a = (s && s.action) || {};
  if (/(^|_)investigate$/.test(String(a.tool || ''))) inv = investigation(s.observation) || inv;
}
if (inv && !['busy', 'budget', 'limit', 'error', 'off'].includes(inv.status)) {
  const itext = inv.answer.trim() || text;
  const p = inv.proposal && typeof inv.proposal === 'object' ? inv.proposal : null;
  if (!p) return reply(itext);
  const pf = proposalInput(p);
  return [{ json: { route: 'register', text: itext, sessionId: st.sessionId, fix: { host: pf.host, verb: pf.verb, args: pf.args }, reason: pf.reason, undo: String(p.undo || '').slice(0, 300) } }];
}
if (!found) return reply(text);
if (!fix) return reply(`${text}\n\n${notRegistered('I could not read what the agent proposed')}`);
return [{ json: { route: 'register', text, sessionId: st.sessionId, fix: { host: fix.host, verb: fix.verb, args: fix.args }, reason: fix.reason } }];
