// Telegram alert?: every critical email (from Decide, or a failed fix from Record fix result) also
// goes to the Telegram bot (stacks/apps/sysadmin-telegram) while TELEGRAM_ALERTS is on; "Telegram
// push" posts it to the bot's /notify, which sends "subject\n\ntext" to the allowed chats. The email
// is sent either way, and a Telegram failure never holds it up.
if (!SETTINGS.TELEGRAM_ALERTS) return [];
const TG_MAX = 8000; // characters of text; the bot splits it into several messages, the email has it all
return $input.all()
  .map((it) => it.json || {})
  .filter((j) => j.subject || j.text)
  .map((j) => {
    const text = String(j.text || '');
    return { json: {
      subject: String(j.subject || '[Homelab] critical issue').slice(0, 200),
      text: text.length > TG_MAX ? `${text.slice(0, TG_MAX)}\n\n(cut here; the email has the rest)` : text,
    } };
  });
