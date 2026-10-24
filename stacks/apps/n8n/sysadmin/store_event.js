// Store one homelab event for the 08:00 digest. POST /webhook/homelab/<diun|kuma|scrutiny|backup|other>;
// the source is the name of the webhook node that fired (fixed paths: n8n prefixes
// paths that contain :params with the webhook id).
const sd = $getWorkflowStaticData('global');
sd.events = sd.events || [];
const q = $json.query || {};
const b = $json.body || {};
const source = $prevNode.name || 'other';
let line;
if (source === 'diun') {
  // Diun sends JSON only with the Content-Type header set (compose: DIUN_NOTIF_WEBHOOK_HEADERS_CONTENT-TYPE);
  // without it n8n hands over an empty body.
  const ctn = ((b.metadata || {}).ctn_names || '').replace(/^\//, '');
  line = b.image ? `Image update: ${b.image} (${b.status || 'new'}) on ${b.hostname || 'unknown host'}${ctn ? `, used by ${ctn}` : ''}` : `Diun sent something unreadable (Content-Type ${($json.headers || {})['content-type'] || 'missing'})`;
} else if (source === 'kuma') {
  // Kuma's own msg already starts with "[monitor] [status]"; the heartbeat's msg is the plain reason.
  const hb = b.heartbeat || {};
  const st = { 0: 'DOWN', 1: 'UP', 2: 'PENDING', 3: 'MAINTENANCE' }[hb.status] || '';
  line = `${(b.monitor && b.monitor.name) || 'monitor'} ${st}: ${hb.msg || b.msg || ''}`.trim();
} else {
  line = String(b.message || b.title || JSON.stringify(b)).replace(/\s+/g, ' ').slice(0, 300);
}
sd.events.push({ t: new Date().toISOString(), source, line });
if (sd.events.length > 500) sd.events = sd.events.slice(-500);
return [{ json: { stored: true } }];
