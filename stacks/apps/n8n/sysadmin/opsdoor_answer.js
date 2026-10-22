// Ops door sub-workflow, last step: one item back to the caller. A lookup gets the door's
// output as text for the model; a fix keeps stdout/stderr/error for Chat after fix.
const j = $input.first().json;
const req = $('Parse').first().json;
if (req.error) return [{ json: { host: req.host, error: req.error, output: req.error } }];
const err = j.error ? String(j.error.message || JSON.stringify(j.error)).slice(0, 300) : '';
const stdout = String(j.stdout || '');
let output = err ? `ssh to ${req.host} failed: ${err}` : stdout;
// The door answers one JSON line; show logs as plain text so the model can read them.
try { const o = JSON.parse(stdout.trim().split('\n').pop()); if (o && o.log) output = `${o.container}:\n${o.log}`; else if (o && o.result) output = `${o.result}: ${o.detail}`; } catch (e) { /* plain output */ }
if (output.length > 12000) output = `...(cut)\n${output.slice(-12000)}`;
return [{ json: { host: req.host, cmd: req.cmd, output, stdout, stderr: String(j.stderr || ''), error: err || undefined } }];
