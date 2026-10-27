// Chat after fix: the ops door answered a confirmed fix. Builds Goobs's reply (sent first, by "Fix
// respond") and the answer for Record fix result (homelab/fixresult, by "Post chat fix result"),
// which marks the fix log entry Chat state started. This run waited on SSH: no static data here.
const st = $('Claim answer').first().json;
const j = $input.first().json;
let answer = null;
try { answer = JSON.parse(String(j.stdout || '').trim().split('\n').pop()); } catch (e) { answer = null; }
const ok = !j.error && answer && answer.result === 'ok';
const detail = answer ? `${answer.result}: ${answer.detail}` : (j.error ? `ssh failed: ${String(j.error).slice(0, 300)}` : String(j.stderr || j.stdout || 'no answer').slice(0, 300));
const text = ok ? `Done: ${st.cmd} on ${st.host}.\n${answer.detail}` : `That did not work: ${st.cmd} on ${st.host}.\n${detail}\nNothing else was changed.`;
// Same shape as fix_answer.js posts for the automatic fixes.
const error = j.error ? String(typeof j.error === 'string' ? j.error : j.error.message || JSON.stringify(j.error)).slice(0, 300) : '';
const result = { id: st.fixid, host: st.host, cmd: st.cmd, key: 'chat', title: st.title, stdout: String(j.stdout || '').slice(-4000), stderr: String(j.stderr || '').slice(-2000), error };
return [{ json: { reply: { text }, results: [result] } }];
