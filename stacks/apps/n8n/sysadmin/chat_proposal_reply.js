// Proposal reply: Chat state's answer to "Register proposal". The page and the Telegram bot get the
// agent's text plus the numbered proposal (Confirm and Cancel buttons); when it could not be
// registered (refused on the re-check, or the call failed), the text says so and has no buttons.
const S = SETTINGS;
const f = $('Chat finish').first().json;
const j = $input.first().json || {};
if (Number.isInteger(j.n) && j.host && j.cmd) {
  return reply(f.text, { proposal: { n: j.n, host: j.host, cmd: j.cmd, reason: j.reason, expires_min: S.CHAT_CONFIRM_MIN, enabled: S.CHAT_FIXES_ENABLED } });
}
// A refusal from Chat state is a string; a failed call leaves the HTTP node's error object.
const why = typeof j.error === 'string' ? j.error
  : `the sysadmin's store did not answer${j.error && j.error.message ? ` (${String(j.error.message).slice(0, 120)})` : ''}`;
return reply(`${f.text}\n\n${notRegistered(why)}`);
