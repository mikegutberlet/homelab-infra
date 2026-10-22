// Chat finish: the agent answered. Attach the proposal it registered during this run (if any)
// so the page can show Confirm and Cancel buttons.
const S = SETTINGS;
const sd = $getWorkflowStaticData('global');
const st = $('Chat prepare').first().json;
const out = $input.first().json;
let text = typeof out.output === 'string' ? out.output.trim() : (out.error ? `The agent failed: ${String(out.error.message || out.error).slice(0, 200)}` : '(no answer)');
const p = chatStore(sd).pending[st.sessionId];
if (p && p.t >= st.t - 1000) {
  return reply(text, { proposal: { n: p.n, host: p.host, cmd: p.cmd, reason: p.reason, expires_min: S.CHAT_CONFIRM_MIN, enabled: S.CHAT_FIXES_ENABLED } });
}
return reply(text);
