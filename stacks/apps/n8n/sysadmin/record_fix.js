// Record fix result: the run that executed a report's fixes posts the ops door's answers back here
// (homelab/fixresult), so the slow SSH run never saves static data. Updates each fix log entry by
// id, keeps a stopped service on hold, and hands any email and follow-up restart to homelab/act.
// A failed fix emails only for a critical issue; a warning's failed fix waits for the digest.
// A fix confirmed in chat comes here too (Chat after fix), after the chat has told Goobs the result.
const sd = $getWorkflowStaticData('global');
sd.fixlog = sd.fixlog || []; sd.held = sd.held || {}; sd.issues = sd.issues || {};
const S = SETTINGS;
const b = $input.first().json.body || {};
const out = [];
for (const j of b.results || []) {
  if (!j || !j.id) continue;
  let answer = null;
  try { answer = JSON.parse(String(j.stdout || '').trim().split('\n').pop()); } catch (e) { answer = null; }
  const ok = !j.error && answer && answer.result === 'ok';
  const detail = answer ? `${answer.result}: ${answer.detail}` : (j.error ? `ssh failed: ${String(j.error).slice(0, 300)}` : String(j.stderr || j.stdout || 'no answer').slice(0, 300));
  let entry = sd.fixlog.find((f) => f.id === j.id);
  if (!entry) {
    // The planning run had not saved yet (or this is a stray post): log what came back anyway.
    entry = { id: j.id, t: now, host: j.host, cmd: j.cmd, verb: String(j.cmd || '').split(' ')[0], target: String(j.cmd || '').split(' ').slice(1).join(' '), key: j.key, title: j.title || j.cmd, status: 'started' };
    entry.tkey = `${entry.host}:${entry.verb}:${entry.target}`;
    // Chat state names chat fixes chat-<n>-<time>: they count toward CHAT_FIX_PER_DAY and are held as "from chat".
    if (String(j.id).startsWith('chat-')) entry.source = 'chat';
    sd.fixlog.push(entry);
  }
  entry.status = ok ? 'ok' : answer && answer.result === 'refused' ? 'refused' : 'failed';
  entry.detail = detail; entry.done = now;
  const { host, verb, target } = entry;
  // Confirmed in chat: Goobs already has the answer there, so no email and no follow-up. Chat state
  // held a stop when it was claimed (and Evaluate report holds an ok one, as "from chat", if that
  // write was lost); a failed one drops any hold, like an automatic stop.
  if (entry.source === 'chat') {
    if (verb === 'stop' && !ok) delete sd.held[`${host}:${target}`];
    continue;
  }
  const it = sd.issues[entry.key] || null;
  const sev = (it && it.sev) || entry.sev || 'warning';
  const about = it ? `${it.title}\n    ${it.detail}\n    open since ${fmtT(it.first)}` : entry.title;

  if (verb === 'stop') {
    if (ok) {
      out.push({ kind: 'email', investigate: true, subject: `[Homelab] stopped ${target} on ${host}: it kept crashing`, text: `${about}\n\nTwo restarts in the last 24 h did not help, so I stopped ${target}. It stays stopped: I will not start it again, and the digest lists it until you do.\n\n${answer.detail}` });
    } else delete sd.held[`${host}:${target}`];
  }
  if (!ok) {
    const tries = sd.fixlog.filter((f) => f.tkey === entry.tkey && now - f.t < 24 * H).length;
    const plan = entry.status === 'refused'
      ? 'The ops door refused it, so I will not try it again today. This one needs you.'
      : tries < S.FIX_PER_DAY ? `Try ${tries} of ${S.FIX_PER_DAY} today. I try again in an hour if it is still open.` : `That was try ${tries} of ${S.FIX_PER_DAY}; no more tries today. This one needs you.`;
    if (sev === 'critical') out.push({ kind: 'email', investigate: true, subject: `[Homelab] fix failed (${sev}): ${it ? it.title : entry.title}`, text: `${about}\n\nTried: ${entry.cmd} on ${host}\nAnswer: ${detail}\n\n${plan}` });
  } else if (entry.then) {
    // gluetun -> qbittorrent: the follow-up is a fix of its own (logged, limited, held like any other).
    const tkey = `${host}:restart:${entry.then}`;
    const day = sd.fixlog.filter((f) => f.tkey === tkey && now - f.t < 24 * H);
    const held = sd.held[`${host}:${entry.then}`];
    if (!held && day.filter((f) => now - f.t < H).length < S.FIX_PER_HOUR && day.length < S.FIX_PER_DAY) {
      const id = `${entry.id}-then`;
      sd.fixlog.push({ id, t: now, host, verb: 'restart', target: entry.then, tkey, cmd: `restart ${entry.then}`, key: entry.key, title: `${entry.title} (follow-up)`, sev: entry.sev, then: null, status: 'started' });
      out.push({ kind: 'fix', id, host, cmd: `restart ${entry.then}`, key: entry.key, title: `${entry.title} (follow-up)`, delay: true });
    }
  }
}
return out.map((json) => ({ json }));
