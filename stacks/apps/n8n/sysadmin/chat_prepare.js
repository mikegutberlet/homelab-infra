// Chat prepare: one request from the chat page. Confirm and Cancel are decided here, in code,
// never by the model; a question goes to the agent with its session id (chat memory).
const S = SETTINGS;
const sd = $getWorkflowStaticData('global');
const req = $input.first().json;
const body = req.body || {};
// who names the asker in the fix log: the Authentik user from the chat page, "telegram" for the
// Telegram bot (stacks/apps/sysadmin-telegram, which sends X-Chat-Via: telegram), else "chat".
const hdr = req.headers || {};
const who = hdr['x-authentik-username'] || (hdr['x-chat-via'] === 'telegram' ? 'telegram' : 'chat');
const sessionId = String(body.sessionId || '').replace(/[^A-Za-z0-9_-]/g, '').slice(0, 64) || 'default';

if (body.confirm != null || body.cancel != null) {
  const n = String(body.confirm != null ? body.confirm : body.cancel);
  const chat = chatStore(sd);
  const p = chat.pending[sessionId];
  if (!p || String(p.n) !== n) return reply(`Proposal ${n} is gone (already used, cancelled or replaced). Ask again if it is still needed.`);
  delete chat.pending[sessionId];
  if (body.cancel != null) return reply(`Cancelled: ${p.cmd} on ${p.host}. Nothing was run.`);
  if (!S.CHAT_FIXES_ENABLED) return reply('Chat fixes are switched off (CHAT_FIXES_ENABLED in settings.js), so nothing was run.');
  if (now - p.t > S.CHAT_CONFIRM_MIN * 60e3) return reply(`Proposal ${n} expired after ${S.CHAT_CONFIRM_MIN} minutes. Ask again and I will re-check first.`);
  const fix = checkFix(sd, p.fix);
  if (fix.error) return reply(`Not run: ${fix.error}.`);
  sd.fixlog = sd.fixlog || [];
  const fixid = `chat-${n}-${now}`;
  sd.fixlog.push({ t: now, host: fix.host, verb: fix.verb, target: fix.target, tkey: fix.tkey, cmd: fix.cmd, key: 'chat', title: `chat (${who}): ${p.reason}`, status: 'started', source: 'chat', id: fixid });
  return [{ json: { route: 'fix', host: fix.host, cmd: fix.cmd, fixid } }];
}

const message = String(body.message || '').trim().slice(0, 2000);
if (!message) return reply('Ask me something about the lab.');
return [{ json: { route: 'agent', chatInput: message, sessionId, who, t: now } }];
