// Investigate alert?: a new critical alert (Decide), a failed fix of a critical issue or a stopped
// crash loop (Record fix result) goes to the investigator once, after the email and the fixes of this
// run. Its note follows as a second email (and Telegram push). The 6-hourly repeat emails of a
// critical that stays open are not investigated again. The investigator's AUTO_PER_DAY caps it too.
const S = SETTINGS;
if (!S.INVESTIGATOR_ENABLED || !S.AUTO_INVESTIGATE) return [];
return $input.all().map((it) => it.json || {}).filter((j) => j.investigate && j.subject).slice(0, 1).map((j) => ({ json: {
  source: 'auto', subject: j.subject,
  query: `${j.subject}\n\n${String(j.text || '').slice(0, 4000)}\n\nFind the cause. The automatic fixes listed above may already have run (look at their effect). Say what Goobs should do, if anything.`,
} }));
