// Ops door sub-workflow, step 1: turn the caller's item into {host, cmd}. The agent's
// ops_lookup tool sends a query string "host status" or "host logs <service> [lines]";
// a confirmed fix from the chat arrives as {route: 'fix', host, cmd} already checked by code.
// The ops user on each VM accepts nothing but its fixed verbs anyway.
const S = SETTINGS;
const j = $input.first().json;
if (j.route === 'fix' && j.host && j.cmd) return [{ json: { host: j.host, cmd: j.cmd } }];
const words = String(j.query || '').trim().toLowerCase().split(/\s+/).filter(Boolean);
const [host, verb, service, lines] = words;
if (!S.HOSTS.includes(host || '')) return [{ json: { host: null, error: `unknown VM "${host || ''}"; use one of ${S.HOSTS.join(', ')}` } }];
if (verb === 'status') return [{ json: { host, cmd: 'status' } }];
if (verb === 'logs' && /^[a-z0-9-]+$/.test(service || '')) {
  const n = Math.min(Math.max(parseInt(lines, 10) || 80, 10), 200);
  return [{ json: { host, cmd: `logs ${service} ${n}` } }];
}
return [{ json: { host: null, error: 'query must be "<vm> status" or "<vm> logs <service> [lines]"' } }];
