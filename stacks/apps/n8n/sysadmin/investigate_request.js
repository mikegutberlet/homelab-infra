// Investigator sub-workflow ("Homelab investigator"), step 1. The chat agent's investigate tool sends
// {query: "<question>"}; the alert run sends {query, source: 'auto', subject}. The investigator
// (stacks/apps/sysadmin-investigator) keeps its own spend caps; INVESTIGATOR_ENABLED false answers
// here at once and nothing is spent.
const S = SETTINGS;
const j = $input.first().json;
const question = String(j.query || j.question || '').trim().slice(0, 6000);
const skip = (status, answer) => [{ json: { route: 'skip', status, answer } }];
if (!S.INVESTIGATOR_ENABLED) return skip('off', 'The investigator is switched off (INVESTIGATOR_ENABLED in settings.js); answer from the quick tools.');
if (!question) return skip('error', 'No question was given to the investigator.');
return [{ json: { route: 'ask', question, source: j.source === 'auto' ? 'auto' : 'chat', subject: String(j.subject || '').slice(0, 300) } }];
