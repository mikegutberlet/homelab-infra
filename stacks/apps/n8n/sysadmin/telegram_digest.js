// Telegram digest?: the 08:00 digest also goes to the Telegram bot while TELEGRAM_DIGEST is on
// (off by default: the digest is long and the email is the record). Same push as the alerts.
if (!SETTINGS.TELEGRAM_DIGEST) return [];
const TG_MAX = 12000; // characters of text; the bot splits it into several messages, the email has it all
return $input.all()
  .map((it) => it.json || {})
  .filter((j) => j.subject || j.text)
  .map((j) => {
    const text = String(j.text || '');
    return { json: {
      subject: String(j.subject || 'Homelab digest').slice(0, 200),
      text: text.length > TG_MAX ? `${text.slice(0, TG_MAX)}\n\n(cut here; the email has the rest)` : text,
    } };
  });
