// Fix answer: the ops door's replies to the fixes this run carried (each SSH output item is paired
// with the Act item it came from), posted in one go to homelab/fixresult for Record fix result.
// No static data here: this run waited on SSH.
const results = $input.all().map((it, i) => {
  const fix = $('Act').itemMatching(i).json;
  const j = it.json;
  const err = j.error ? String(j.error.message || JSON.stringify(j.error)).slice(0, 300) : '';
  return { id: fix.id, host: fix.host, cmd: fix.cmd, key: fix.key, title: fix.title, stdout: String(j.stdout || '').slice(-4000), stderr: String(j.stderr || '').slice(-2000), error: err };
});
return [{ json: { results } }];
