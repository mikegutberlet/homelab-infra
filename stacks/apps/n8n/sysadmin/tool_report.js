// Tool vm_report: one VM's latest homelab-agent report (input {host}).
const S = SETTINGS;
const sd = $getWorkflowStaticData('global');
const host = String((query && query.host) || '').trim();
if (!S.HOSTS.includes(host)) return `unknown VM "${host}"; one of ${S.HOSTS.join(', ')}`;
const r = (sd.last || {})[host];
if (!r) return `${host} has not reported yet`;
const out = { host, received: `${fmtT(r.received)} (${ago(r.received)} ago)` };
for (const k of Object.keys(r)) if (!['host', 'received', 'ts'].includes(k)) out[k] = r[k];
if (out.docker && out.docker.containers) {
  out.docker = { ...out.docker, containers: out.docker.containers.map((c) => `${c.service || c.name}: ${c.state}${c.health && c.health !== 'none' ? `/${c.health}` : ''}${c.restarts ? ` restarts=${c.restarts}` : ''}`) };
}
return toolText(out, 14000);
