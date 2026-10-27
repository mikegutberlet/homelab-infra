// Investigation email: the investigator's note on an alert, as a follow-up email (and Telegram push
// while TELEGRAM_ALERTS is on). Nothing is sent when it did not investigate (switched off, daily
// cap reached, busy): the alert itself already went out.
const j = $input.first().json || {};
if (!['ok', 'timeout', 'max_turns', 'max_budget_usd'].includes(j.status) || !String(j.answer || '').trim()) return [];
const p = j.proposal;
const fix = p ? `\n\nProposed fix (not run): ${p.verb === 'run' ? `a script on ${p.host}:\n${String(p.script || '').split('\n').map((l) => `    ${l}`).join('\n')}` : `${p.verb} ${(p.args || []).join(' ')} on ${p.host}`}\n${p.reason || ''}\nAsk the Sys Admin chat to run it (it re-checks first), or do it by hand.` : '';
const subject = `${String(j.subject || '[Homelab] alert').replace(/^\[Homelab\]\s*/, '[Homelab] Investigation: ')}`.slice(0, 250);
return [{ json: { subject, text: `${String(j.answer).trim()}${fix}\n\n(investigator case ${j.case || '?'}, $${Number(j.cost_usd || 0).toFixed(2)})` } }];
