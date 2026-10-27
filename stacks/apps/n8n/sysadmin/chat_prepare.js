// Chat prepare: one request from the chat page. Confirm and Cancel are decided in code, never by
// the model, by Chat state (homelab/chatstate, via "Claim"); a question goes to the agent with its
// session id (chat memory). Reads nothing and writes nothing: this run may wait on the model or the
// ops door, and a run that wrote static data would save it whole when it ends.
// who names the asker in the fix log: the Authentik user from the chat page, "telegram" for the
// Telegram bot (stacks/apps/sysadmin-telegram, which sends X-Chat-Via: telegram), else "chat".
const req = $input.first().json;
const body = req.body || {};
const hdr = req.headers || {};
const who = hdr['x-authentik-username'] || (hdr['x-chat-via'] === 'telegram' ? 'telegram' : 'chat');
const sessionId = chatSession(body.sessionId);

if (body.confirm != null || body.cancel != null) {
  const n = String(body.confirm != null ? body.confirm : body.cancel);
  return [{ json: { route: 'claim', sessionId, n, action: body.cancel != null ? 'cancel' : 'confirm', who } }];
}

const message = String(body.message || '').trim().slice(0, 2000);
if (!message) return reply('Ask me something about the lab.');
return [{ json: { route: 'agent', chatInput: message, sessionId, who } }];
