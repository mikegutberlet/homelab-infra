// Digest sent: the 08:00 email went out, so clear what it reported (events and resolved records
// up to its cutoff). If the email fails this node never runs, and tomorrow's digest has both days.
const sd = $getWorkflowStaticData('global');
const cutoff = $('Build digest').first().json.cutoff || now;
sd.events = (sd.events || []).filter((e) => Date.parse(e.t) > cutoff);
sd.resolved = (sd.resolved || []).filter((r) => r.resolved_at > cutoff);
sd.wouldfix = (sd.wouldfix || []).filter((f) => f.t > cutoff - 24 * H);
return [{ json: { cleared_until: cutoff } }];
