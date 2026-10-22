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
  line = `Image update: ${b.image || ''} (${b.status || 'new'}) on ${b.hostname || 'unknown host'}`;
} else if (source === 'kuma') {
  const st = b.heartbeat ? (b.heartbeat.status === 0 ? 'DOWN' : b.heartbeat.status === 1 ? 'UP' : '') : '';
  line = `${(b.monitor && b.monitor.name) || 'monitor'} ${st}: ${b.msg || ''}`.trim();
} else {
  line = b.message || b.title || JSON.stringify(b).slice(0, 300);
}
sd.events.push({ t: new Date().toISOString(), source, line });
if (sd.events.length > 500) sd.events = sd.events.slice(-500);
return [{ json: { stored: true } }];
