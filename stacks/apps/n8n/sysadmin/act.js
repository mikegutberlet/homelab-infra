// Act: the emails and fixes of one Decide (or Record fix result) run, posted by "Hand off" to
// homelab/act as {items: [...]}. Nothing in this run reads or writes static data: it waits on
// SMTP and SSH, and a run that saved static data late would overwrite newer state. One report's
// fixes all go to its own VM, so they run one after the other and come back to homelab/fixresult
// in one post. Only these two shapes go on.
const S = SETTINGS;
const b = $input.first().json.body || {};
const out = [];
for (const x of Array.isArray(b.items) ? b.items : []) {
  if (x && x.kind === 'email' && x.subject) out.push({ kind: 'email', subject: String(x.subject).slice(0, 300), text: String(x.text || ''), investigate: x.investigate === true });
  else if (x && x.kind === 'fix' && x.id && S.HOSTS.includes(x.host) && /^[a-z-]+( [A-Za-z0-9 _.:-]+)?$/.test(String(x.cmd || '')))
    out.push({ kind: 'fix', id: String(x.id), host: x.host, cmd: x.cmd, key: x.key || null, title: x.title || x.cmd, delay: x.delay === true });
}
return out.map((json) => ({ json }));
