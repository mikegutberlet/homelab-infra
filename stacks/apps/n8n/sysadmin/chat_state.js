// Chat state (homelab/chatstate): the only chat run that writes static data. The chat run waits on
// the model or the ops door, so it posts its two writes here instead, and this run is over in well
// under a second, like a report run (n8n saves the whole static data when a run ends, last writer
// wins). Called by "Register proposal" and "Claim" in the chat run, with the internal token:
//   {op: 'propose', sessionId, fix: {host, verb, args}, reason} -> {n, host, cmd, reason} or {error}
//   {op: 'claim', sessionId, n, action: 'confirm'|'cancel', who} -> {route: 'reply', text}
//                                                                 or {route: 'fix', host, cmd, fixid, title}
const S = SETTINGS;
const sd = $getWorkflowStaticData('global');
const b = $input.first().json.body || {};
const sessionId = chatSession(b.sessionId);
const answer = (json) => [{ json }];
const say = (text) => answer({ route: 'reply', text });

if (b.op === 'propose') {
  // Checked again here: the model's turn took a while, and the counters may have moved.
  const p = proposalInput({ ...(b.fix && typeof b.fix === 'object' ? b.fix : {}), reason: b.reason });
  const fix = checkFix(sd, p);
  if (fix.error) return answer({ error: fix.error });
  const chat = chatStore(sd);
  // n must never be handed out twice: a run that loaded static data before this one and saves after
  // it (a report, or the slower infra run) puts chat.next back, and the page keeps older cards'
  // buttons, so a reused n would let an older card's Confirm claim a newer, different fix. Seconds
  // since 2026-10-01 do not roll back; chat.next only matters for two proposals within one second.
  const n = Math.max(chat.next, Math.floor((now - Date.UTC(2026, 9, 1)) / 1000));
  chat.next = n + 1;
  // One pending proposal per session: a newer one replaces it, and its old Confirm finds it gone.
  chat.pending[sessionId] = { n, t: now, host: fix.host, cmd: fix.cmd, fix: { host: p.host, verb: p.verb, args: p.args }, reason: p.reason };
  return answer({ n, host: fix.host, cmd: fix.cmd, reason: p.reason });
}

if (b.op === 'claim' && (b.action === 'confirm' || b.action === 'cancel')) {
  const n = String(b.n == null ? '' : b.n).slice(0, 40);
  const who = String(b.who || '').replace(/[^\w.@ -]/g, '').slice(0, 64) || 'chat';
  // Read before writing: a stale button (proposal gone) leaves the static data untouched.
  const pend = sd.chat && sd.chat.pending;
  const p = pend && Object.prototype.hasOwnProperty.call(pend, sessionId) ? pend[sessionId] : null;
  if (!p || String(p.n) !== n) return say(`Proposal ${n} is gone (already used, cancelled or replaced). Ask again if it is still needed.`);
  delete sd.chat.pending[sessionId];
  if (b.action === 'cancel') return say(`Cancelled: ${p.cmd} on ${p.host}. Nothing was run.`);
  if (!S.CHAT_FIXES_ENABLED) return say('Chat fixes are switched off (CHAT_FIXES_ENABLED in settings.js), so nothing was run.');
  if (now - p.t > S.CHAT_CONFIRM_MIN * 60e3) return say(`Proposal ${n} expired after ${S.CHAT_CONFIRM_MIN} minutes. Ask again and I will re-check first.`);
  const fix = checkFix(sd, p.fix);
  if (fix.error) return say(`Not run: ${fix.error}.`);
  // Logged as started now, so the limits count it; Record fix result (homelab/fixresult) marks it
  // ok, refused or failed when the chat run posts the ops door's answer.
  sd.fixlog = sd.fixlog || [];
  const fixid = `chat-${n}-${now}`;
  const title = `chat (${who}): ${p.reason}`;
  sd.fixlog.push({ t: now, host: fix.host, verb: fix.verb, target: fix.target, tkey: fix.tkey, cmd: fix.cmd, key: 'chat', title, status: 'started', source: 'chat', id: fixid });
  // A stop is held from now on, as Decide does for its own, so the hold does not depend on the later
  // fixresult write surviving (Evaluate report holds only an 'ok' stop, and holds it again if a
  // report saw the service still running meanwhile and dropped this one). A failed stop drops it
  // (Record fix result).
  if (fix.verb === 'stop') { sd.held = sd.held || {}; sd.held[`${fix.host}:${fix.target}`] = { t: now, by: 'chat', id: fixid }; }
  return answer({ route: 'fix', host: fix.host, cmd: fix.cmd, fixid, title });
}

return answer({ error: `unknown request (op ${JSON.stringify(String(b.op || '')).slice(0, 40)})` });
