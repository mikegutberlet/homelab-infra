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
if (!found) return reply(text);
if (!fix) return reply(`${text}\n\n${notRegistered('I could not read what the agent proposed')}`);
return [{ json: { route: 'register', text, sessionId: st.sessionId, fix: { host: fix.host, verb: fix.verb, args: fix.args }, reason: fix.reason } }];
