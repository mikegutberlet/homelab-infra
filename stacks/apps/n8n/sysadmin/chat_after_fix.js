// Chat after fix: the ops door answered a confirmed fix. Record it in the fix log (the digest's
// "Fixed" section) and tell Goobs what happened.
const sd = $getWorkflowStaticData('global');
const st = $('Chat prepare').first().json;
const j = $input.first().json;
let answer = null;
try { answer = JSON.parse(String(j.stdout || '').trim().split('\n').pop()); } catch (e) { answer = null; }
const ok = !j.error && answer && answer.result === 'ok';
const detail = answer ? `${answer.result}: ${answer.detail}` : (j.error ? `ssh failed: ${String(j.error).slice(0, 300)}` : String(j.stderr || j.stdout || 'no answer').slice(0, 300));
const entry = (sd.fixlog || []).find((f) => f.id === st.fixid);
if (entry) { entry.status = ok ? 'ok' : 'failed'; entry.detail = detail; entry.done = now; }
return reply(ok ? `Done: ${st.cmd} on ${st.host}.\n${answer.detail}` : `That did not work: ${st.cmd} on ${st.host}.\n${detail}\nNothing else was changed.`);
