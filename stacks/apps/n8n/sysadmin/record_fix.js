// Record fix result: the ops door answered (or SSH failed). Updates the fix log entry and
// emails at once if a fix failed, since a failed fix means the issue is still there.
const sd = $getWorkflowStaticData('global');
sd.fixlog = sd.fixlog || [];
const out = [];
for (const item of $input.all()) {
  const j = item.json;
  let fix = null;
  try { fix = $('Route fix by host').item.json; } catch (e) { fix = null; }
  const host = (fix && fix.host) || ($prevNode.name || '').replace('ops ', '') || '?';
  let answer = null;
  try { answer = JSON.parse((j.stdout || '').trim().split('\n').pop()); } catch (e) { answer = null; }
  const ok = !j.error && answer && answer.result === 'ok';
  const detail = answer ? `${answer.result}: ${answer.detail}` : (j.error ? `ssh failed: ${j.error.message || JSON.stringify(j.error).slice(0, 200)}` : (j.stderr || j.stdout || 'no answer').toString().slice(0, 300));
  // Newest log entry for this host and command.
  const entry = [...sd.fixlog].reverse().find((f) => f.host === host && (!fix || f.cmd === fix.cmd) && f.status === 'started');
  if (entry) { entry.status = ok ? 'ok' : 'failed'; entry.detail = detail; entry.done = now; }
  if (!ok) out.push({ json: { kind: 'email', subject: `[Homelab] fix failed on ${host}: ${fix ? fix.cmd : 'unknown command'}`, text: `${fix ? fix.title : ''}\n\nTried: ${fix ? fix.cmd : '?'}\nAnswer: ${detail}\n\nUndo or next step: see the ops door's output above; nothing else was changed.` } });
  else if (fix && fix.then) out.push({ json: { kind: 'fix', host, ip: fix.ip, cmd: `restart ${fix.then}`, key: fix.key, title: fix.title, then: null, delay: true } });
}
return out;
