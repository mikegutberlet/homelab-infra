// Tool propose_fix: the model asks for a fix (input {host, verb, args, reason}). Checked by code here
// so the model hears at once whether it is allowed; nothing is written or run. After the turn,
// Chat finish takes the last allowed proposal from the agent's steps and Chat state registers it
// for this chat session (checked again) until Goobs presses Confirm.
const S = SETTINGS;
const sd = $getWorkflowStaticData('global');
const p = proposalInput(query);
const fix = checkFix(sd, p);
if (fix.error) return `Not allowed: ${fix.error}. Tell Goobs why and, if useful, how to do it by hand.`;
return `${PROPOSAL_OK} "${fix.cmd}" on ${fix.host}. It is NOT running. Goobs sees a Confirm button; it runs only if Goobs presses it${S.CHAT_FIXES_ENABLED ? '' : ' (chat fixes are currently switched off, so even then it will not run)'}. Tell Goobs what it does and that it waits for their confirmation.`;
