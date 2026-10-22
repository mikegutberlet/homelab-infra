// Tool propose_fix: the model asks for a fix (input {host, verb, args, reason}). Checked by code,
// parked for this chat session until Goobs presses Confirm; nothing runs here.
const S = SETTINGS;
const sd = $getWorkflowStaticData('global');
const p = { host: String((query && query.host) || '').trim(), verb: String((query && query.verb) || '').trim(), args: (query && query.args) || [], reason: String((query && query.reason) || '').slice(0, 200) };
const fix = checkFix(sd, p);
if (fix.error) return `Not allowed: ${fix.error}. Tell Goobs why and, if useful, how to do it by hand.`;
let sessionId = 'default';
try { sessionId = $('Chat prepare').first().json.sessionId || 'default'; } catch (e) { /* keep default */ }
const chat = chatStore(sd);
const n = chat.next++;
chat.pending[sessionId] = { n, t: now, host: fix.host, cmd: fix.cmd, fix: { host: p.host, verb: p.verb, args: p.args }, reason: p.reason };
return `Proposal #${n} registered: "${fix.cmd}" on ${fix.host}. It is NOT running. Goobs sees a Confirm button; it runs only if Goobs presses it${S.CHAT_FIXES_ENABLED ? '' : ' (chat fixes are currently switched off, so even then it will not run)'}. Tell Goobs what it does and that it waits for their confirmation.`;
