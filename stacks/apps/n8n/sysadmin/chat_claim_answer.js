// Claim answer: Chat state's answer to a Confirm or Cancel (homelab/chatstate, via "Claim"). Only a
// fix that Chat state claimed, re-checked and logged as started goes on to the ops door; an answer
// that is missing or unreadable (n8n restarting, a timeout) runs nothing.
const S = SETTINGS;
const j = $input.first().json || {};
if (j.route === 'fix' && j.fixid && S.HOSTS.includes(j.host) && /^[a-z-]+( [A-Za-z0-9 _.:-]+)?$/.test(String(j.cmd || ''))) {
  return [{ json: { route: 'fix', host: j.host, cmd: String(j.cmd), label: String(j.label || j.cmd), fixid: String(j.fixid), title: String(j.title || j.cmd) } }];
}
if (j.route === 'reply' && typeof j.text === 'string' && j.text) return reply(j.text);
return reply('I could not reach the sysadmin\'s store, so nothing was run. Try again in a minute.');
