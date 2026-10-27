"""Telegram front end for the homelab sysadmin chat (Goobs, 2026-10-23).

A thin client for the n8n "Homelab sysadmin" chat webhook, the same one the sysadmin chat page
(stacks/apps/sysadmin-chat) uses. It long-polls the Telegram Bot API (getUpdates), so nothing is
exposed to the internet: no public webhook, no Tailscale Funnel. Nothing is decided here: every
question, Confirm and Cancel goes to n8n, which runs the agent, keeps its memory and decides fixes.

- Only chats in TELEGRAM_ALLOWED_CHAT_IDS (Goobs's private chat) are answered. Everything else is
  dropped and logged as "ignored update from chat id X" (no content), which is also how to find
  a chat id. An empty list answers nobody.
- Questions: POST {"message", "sessionId"} to CHAT_URL with X-Chat-Token (CHAT_TOKEN, from the
  chat page's token.env) and X-Chat-Via: telegram. "typing..." shows while the agent works (up
  to ~5 min). The agent's light Markdown (**bold**, `code`, "- " bullets, ``` blocks) becomes
  Telegram HTML; a chunk Telegram cannot parse is resent as plain text.
- Session ids are "tg-<chat id>-<k>"; /new bumps k, so the agent starts with an empty memory.
- One question at a time per chat: a second one while the first runs gets "Still working on your
  last question" and is NOT queued (the agent's memory is per session and ordered, and a queue of
  stale questions after a slow answer helps nobody). Commands, /new and that lock are handled in
  the poll thread in update order; the agent calls run in worker threads. Button presses never
  wait behind a question.
- A proposed fix becomes its own message with Confirm/Cancel buttons (callback data "c:<n>" /
  "x:<n>"; only Dismiss while chat fixes are switched off). The session that made the proposal
  and the message that shows it are stored with it in state.json, so Confirm still works after
  /new; n8n keeps one pending proposal per session and rejects anything stale. A newer proposal
  in the same session retires the older message's buttons. A press with no stored entry for that
  message (older than a day, state lost, or n reused after a workflow re-import) only removes the
  buttons ("This proposal is gone; ask again") and is never sent to n8n. A button counts only in
  an allowed chat (in a group chat, if one is ever allowed, the presser's user id must be listed
  too); any other press gets "Not allowed" and nothing else.
- /status and /fixes ask the agent the two common questions; /help lists the commands. Only
  Telegram's command syntax counts as a command, so "/var/log/syslog is huge" is a question.

HTTP on HEALTH_PORT (0.0.0.0):
  GET /healthz  200 "ok ..." while configured and the last successful getUpdates is < 3 min old,
                else 503 with the reason (for Uptime Kuma): "not configured: TELEGRAM_BOT_TOKEN /
                CHAT_TOKEN is not set", "cannot write .../state.json", "no successful getUpdates
                yet: <error>", "last successful getUpdates N min ago: <error>".
  POST /notify  X-Chat-Token: CHAT_TOKEN, JSON {"subject", "text"} (max 64 KB): sends
                "subject\\n\\ntext" as plain text to every allowed chat. 200 {"sent": n, "failed": 0},
                502 when Telegram refused any of it, 401 bad token, 503 not configured. n8n uses it
                to push critical alerts (and the digest if TELEGRAM_DIGEST is on).

STATE_DIR/state.json (atomic writes) keeps the getUpdates offset, so a restart neither replays
nor loses updates (on the very first start, messages older than 2 minutes are skipped), the bot's
id (state from another bot's token is reset, offset included), the per-chat session counters, the
open proposals (dropped after 24 hours) and a mark for each question or Confirm in flight: an
update is confirmed to Telegram once dispatched, so after a restart that cut one off, the chat is
told to ask again (or to check /fixes) instead of waiting for an answer that never comes.

Secrets: the bot token is part of every Bot API URL, so every log line, error and traceback goes
through redact(), and Telegram errors are re-raised without the urllib exception attached.
Message contents are never logged, only chat ids, update types and lengths. Without
TELEGRAM_BOT_TOKEN the bot does not crash-loop: it serves /healthz 503 "not configured" and idles.
Python 3.12 standard library only.
"""

import hmac
import html
import http.server
import json
import os
import re
import signal
import sys
import threading
import time
import traceback
import urllib.error
import urllib.parse
import urllib.request

TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()
CHAT_TOKEN = os.environ.get("CHAT_TOKEN", "").strip()
CHAT_URL = os.environ.get("CHAT_URL", "http://n8n:5678/webhook/homelab/chat")
TG_API = os.environ.get("TELEGRAM_API", "https://api.telegram.org").rstrip("/")
STATE_DIR = os.environ.get("STATE_DIR", "/data")
HEALTH_PORT = int(os.environ.get("HEALTH_PORT", "3004"))

POLL_TIMEOUT = 50          # seconds Telegram holds a getUpdates call open
AGENT_TIMEOUT = 490        # the agent can take minutes (Gemini plus its fallback, or an investigation of up to 4 min)
TYPING_EVERY = 4           # Telegram shows "typing..." for about 5 s per sendChatAction
CHUNK = 3500               # characters per message (Telegram's limit is 4096)
NOTIFY_MAX_BODY = 64 * 1024
NOTIFY_MAX_CHUNKS = 6      # n8n cuts alert text to 8-12k characters; this is a backstop
HEALTH_STALE = 180         # /healthz fails when the last good getUpdates is older than this
FIRST_START_SKIP = 120     # first start without state: skip messages older than this
PROPOSAL_KEEP = 24 * 3600  # open proposals are forgotten after a day (n8n expires them in 15 min)
MAX_QUESTION = 2000        # the webhook cuts questions to this length

KNOWN_COMMANDS = ("start", "help", "new", "status", "fixes")
STATUS_Q = "What needs my attention right now?"
FIXES_Q = "What fixes ran in the last 24 hours?"
COMMANDS = [
    {"command": "status", "description": "What needs my attention right now?"},
    {"command": "fixes", "description": "Fixes that ran in the last 24 hours"},
    {"command": "new", "description": "Start a new conversation"},
    {"command": "help", "description": "What I can do"},
]
HELP = (
    "I am the homelab's junior sysadmin, the same agent as the chat page. Ask me anything about the lab "
    "in plain words; an answer can take a minute or two.\n\n"
    "/status - what needs my attention right now\n"
    "/fixes - fixes that ran in the last 24 hours\n"
    "/new - start a new conversation (I forget the earlier one)\n"
    "/help - this message\n\n"
    "When a fix would help I show it with Confirm and Cancel buttons. Nothing runs unless you press "
    "Confirm, and n8n still checks it against the safe-fix list and limits."
)
# Message kinds that get the "text only" reply; service messages (joins, pins...) are ignored.
NON_TEXT = ("photo", "document", "voice", "audio", "video", "video_note", "sticker", "animation",
            "location", "contact", "poll", "venue", "dice")


# ---------------------------------------------------------------- logging without secrets

# Longest first, so the whole token is replaced before its secret half.
SECRETS = sorted({s for s in (TOKEN, urllib.parse.quote(TOKEN, safe=""), TOKEN.partition(":")[2], CHAT_TOKEN) if len(s) >= 8},
                 key=len, reverse=True)


def redact(text):
    text = str(text)
    for s in SECRETS:
        text = text.replace(s, "<redacted>")
    return text


def log(msg):
    print(f"{time.strftime('%Y-%m-%d %H:%M:%S')} {redact(msg)}", flush=True)


def _excepthook(exc_type, exc, tb):
    print(redact("".join(traceback.format_exception(exc_type, exc, tb))), file=sys.stderr, flush=True)


sys.excepthook = _excepthook
threading.excepthook = lambda a: _excepthook(a.exc_type, a.exc_value, a.exc_traceback)


def short_error(e):
    """A loggable one-liner for any exception (type plus a redacted, cut message)."""
    return f"{type(e).__name__}: {redact(e)[:200]}"


def parse_ids(raw):
    """TELEGRAM_ALLOWED_CHAT_IDS: comma-separated ints (group chats are negative)."""
    ids = set()
    for part in raw.replace(";", ",").split(","):
        part = part.strip()
        if re.fullmatch(r"-?\d{1,20}", part):
            ids.add(int(part))
        elif part:  # the value itself is not logged: it could be a pasted secret
            log("TELEGRAM_ALLOWED_CHAT_IDS: ignoring an entry that is not a chat id")
    return ids


ALLOWED = parse_ids(os.environ.get("TELEGRAM_ALLOWED_CHAT_IDS", ""))


# ---------------------------------------------------------------- Telegram Bot API

class TgError(Exception):
    """A Bot API failure. code is Telegram's error_code (0 for network errors). The message never
    holds the token: the urllib exception is dropped (raise ... from None) after redaction."""

    def __init__(self, method, code, desc, retry_after=None):
        super().__init__(f"{method}: {code or 'network'} {desc}")
        self.code, self.desc, self.retry_after = code, desc, retry_after


def tg(method, params=None, timeout=30, retries_429=2, max_wait=60):
    """Call a Bot API method with a JSON body and return its "result". A 429 is retried after
    Telegram's retry_after while that is at most max_wait seconds; anything else raises TgError."""
    url = f"{TG_API}/bot{TOKEN}/{method}"
    data = json.dumps(params or {}).encode()
    for attempt in range(retries_429 + 1):
        try:
            req = urllib.request.Request(url, data=data, headers={"Content-Type": "application/json"})
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                status, body = resp.status, resp.read()
        except urllib.error.HTTPError as e:
            status = e.code
            try:
                body = e.read()
            except Exception:
                body = b""
        except Exception as e:  # URLError, timeouts, resets, a bad TELEGRAM_API value...
            raise TgError(method, 0, short_error(e)) from None
        try:
            j = json.loads(body)
        except ValueError:
            j = {}
        if not isinstance(j, dict):
            j = {}
        if status == 200 and j.get("ok"):
            return j.get("result")
        code = j.get("error_code") or status
        desc = redact(j.get("description") or f"HTTP {status}")[:200]
        retry = (j.get("parameters") or {}).get("retry_after") if isinstance(j.get("parameters"), dict) else None
        retry = int(retry) if isinstance(retry, (int, float)) else None
        if code == 429 and retry is not None and retry <= max_wait and attempt < retries_429:
            log(f"Telegram rate limit on {method}: waiting {retry}s")
            time.sleep(retry + 0.5)
            continue
        raise TgError(method, code, desc, retry)


def try_tg(method, params, **kw):
    """Best-effort call (chat actions, button edits): log and carry on when it fails."""
    try:
        return tg(method, params, **kw)
    except TgError as e:
        if "message is not modified" not in e.desc:
            log(f"{method} failed: {e}")
        return None


class Typing:
    """Keeps "typing..." showing in a chat while the with-block runs."""

    def __init__(self, chat_id):
        self.chat_id, self.stop = chat_id, threading.Event()

    def _run(self):
        while not self.stop.is_set():
            try:
                tg("sendChatAction", {"chat_id": self.chat_id, "action": "typing"}, timeout=10, retries_429=0)
            except TgError:
                pass
            self.stop.wait(TYPING_EVERY)

    def __enter__(self):
        threading.Thread(target=self._run, daemon=True).start()
        return self

    def __exit__(self, *exc):
        self.stop.set()


# ---------------------------------------------------------------- formatting

BOLD = re.compile(r"\*\*(?=\S)(.+?)(?<=\S)\*\*")
ITALIC = re.compile(r"(?<![\w*])\*(?=[^\s*])([^*\n]+?)(?<=[^\s*])\*(?![\w*])")
# Only a bare ``` line (optionally with a language) opens or closes a block; "```cmd```" on one
# line is inline code.
FENCE = re.compile(r"\s*```[\w+.-]*\s*")
# Telegram's command syntax; "/var/log/syslog is huge" is a question, not a command.
COMMAND = re.compile(r"/([A-Za-z0-9_]{1,32})(?:@(\w+))?")


def esc(s):
    return html.escape(str(s), quote=False)


def inline(line):
    """One escaped line: `code` spans are set aside first (left as they are inside), then **bold**
    and *italic* run over the whole line, so bold around a code span works too."""
    line = esc(line).replace("\x00", "")
    m = re.match(r"^(\s*)[-*•]\s+(.*)$", line)
    if m:
        line = f"{m[1]}• {m[2]}"
    else:
        m = re.match(r"^#{1,6}\s+(.*)$", line)
        if m:
            line = f"**{m[1].strip('* ')}**" if m[1].strip("* ") else ""
    line = re.sub(r"```([^`]+)```", r"`\1`", line)
    codes = []

    def keep(m):
        codes.append(m[1])
        return f"\x00{len(codes) - 1}\x00"

    line = ITALIC.sub(r"<i>\1</i>", BOLD.sub(r"<b>\1</b>", re.sub(r"`([^`]+)`", keep, line)))
    return re.sub(r"\x00(\d+)\x00", lambda m: f"<code>{codes[int(m[1])]}</code>", line)


def md_to_html(text):
    """The agent's light Markdown as Telegram HTML. Everything is escaped before any tag is added,
    so nothing the agent (or a log line it quotes) writes can become markup of its own."""
    out, pre, in_pre = [], [], False
    for line in text.split("\n"):
        if FENCE.fullmatch(line):
            if in_pre and any(x.strip() for x in pre):
                out.append(f"<pre>{esc(chr(10).join(pre))}</pre>")
            pre, in_pre = [], not in_pre
        elif in_pre:
            pre.append(line)
        else:
            out.append(inline(line))
    if in_pre and any(x.strip() for x in pre):
        out.append(f"<pre>{esc(chr(10).join(pre))}</pre>")
    return "\n".join(out).strip()


def split_text(text, limit=CHUNK, fences=True):
    """Chunks of at most ~limit characters, cut on line boundaries (an over-long line is cut hard).
    With fences, a ``` block cut in two is closed at the end of one chunk and reopened in the next."""
    lines = []
    for line in text.split("\n"):
        while len(line) > limit:
            lines.append(line[:limit])
            line = line[limit:]
        lines.append(line)
    chunks, cur, size, in_fence = [], [], 0, False
    for line in lines:
        fence = fences and FENCE.fullmatch(line)
        # A closing fence that does not fit still goes on this chunk (a few characters over, far
        # below 4096) rather than starting a chunk of nothing but "```" lines.
        if cur and size + len(line) + 1 > limit and not (fence and in_fence and len(line) <= 64):
            chunks.append("\n".join(cur + (["```"] if in_fence else [])))
            cur, size = (["```"], 4) if in_fence else ([], 0)
        cur.append(line)
        size += len(line) + 1
        if fence:
            in_fence = not in_fence
    chunks.append("\n".join(cur))
    # Drop chunks with nothing but fence lines (an opening fence whose first line did not fit).
    return [c for c in chunks if any(x.strip() and not (fences and FENCE.fullmatch(x)) for x in c.split("\n"))]


def send_answer(chat_id, text, reply_to=None):
    """Send the agent's text as HTML, chunk by chunk; a chunk Telegram cannot parse (400) is sent
    again as plain text."""
    for i, chunk in enumerate(split_text(text) or ["(no answer)"]):
        params = {"chat_id": chat_id, "link_preview_options": {"is_disabled": True}}
        if i == 0 and reply_to:
            params["reply_parameters"] = {"message_id": reply_to, "allow_sending_without_reply": True}
        body = md_to_html(chunk)
        try:
            if not body:
                raise TgError("sendMessage", 400, "empty after formatting")
            tg("sendMessage", {**params, "text": body, "parse_mode": "HTML"})
        except TgError as e:
            if e.code != 400:
                raise
            log(f"chat {chat_id}: HTML refused ({e.desc[:80]}), resending as plain text")
            tg("sendMessage", {**params, "text": chunk})


def send_plain(chat_id, text):
    tg("sendMessage", {"chat_id": chat_id, "text": text, "link_preview_options": {"is_disabled": True}})


# ---------------------------------------------------------------- state.json

class Store:
    """{"bot_id": int|null, "offset": int|null, "chats": {"<chat id>": {"k": int}},
        "proposals": {"<chat id>:<n>": {"session", "msg", "html", "enabled", "t", "done"}},
        "busy": {"<chat id>:<tag>": "what was in flight"}}"""

    def __init__(self, directory):
        self.path = os.path.join(directory, "state.json")
        self.lock = threading.RLock()
        self.error = None  # last write error, shown by /healthz
        self.data = {"bot_id": None, "offset": None, "chats": {}, "proposals": {}, "busy": {}}
        try:
            with open(self.path, encoding="utf-8") as f:
                d = json.load(f)
            for key in ("bot_id", "offset"):
                if isinstance(d.get(key), int) and not isinstance(d.get(key), bool):
                    self.data[key] = d[key]
            for cid, c in (d.get("chats") or {}).items():
                if isinstance(c, dict) and isinstance(c.get("k"), int) and c["k"] >= 1:
                    self.data["chats"][str(cid)] = {"k": c["k"]}
            for key, p in (d.get("proposals") or {}).items():
                if isinstance(p, dict) and isinstance(p.get("session"), str):
                    self.data["proposals"][key] = p
            for key, what in (d.get("busy") or {}).items():
                if re.fullmatch(r"-?\d{1,20}:\w{1,20}", str(key)) and isinstance(what, str):
                    self.data["busy"][key] = what[:200]
        except FileNotFoundError:
            log(f"no {self.path} yet: first start")
        except (OSError, ValueError, AttributeError) as e:
            log(f"{self.path} unreadable ({short_error(e)}); starting with empty state")

    @property
    def offset(self):
        return self.data["offset"]

    def save(self):
        with self.lock:
            now = time.time()
            props = self.data["proposals"]
            for key in [k for k, p in props.items() if now - p.get("t", 0) > PROPOSAL_KEEP]:
                del props[key]
            tmp = self.path + ".tmp"
            try:
                with open(tmp, "w", encoding="utf-8") as f:
                    json.dump(self.data, f, indent=1)
                    f.flush()
                    os.fsync(f.fileno())
                os.replace(tmp, self.path)
                self.error = None
            except OSError as e:
                if not self.error:
                    log(f"cannot write {self.path}: {short_error(e)}")
                self.error = f"cannot write {self.path} ({type(e).__name__}; owned by the storage user?)"

    def set_offset(self, offset):
        with self.lock:
            if self.data["offset"] is None or offset > self.data["offset"]:
                self.data["offset"] = offset

    def check_bot(self, bot_id):
        """Tie the state to the bot (getMe id). Update ids count per bot, and Telegram drops every
        update below the offset, so another bot's saved offset could swallow all messages: when the
        bot changed, start like a first start (no offset, no open proposals). True when it changed."""
        if not isinstance(bot_id, int):
            return False
        with self.lock:
            old = self.data.get("bot_id")
            if old == bot_id:
                return False
            self.data["bot_id"] = bot_id
            if old is not None:
                self.data.update(offset=None, proposals={}, busy={})
            self.save()
            return old is not None

    def set_busy(self, chat_id, tag, what):
        """Mark work in flight (a question, a Confirm), so a restart in the middle can say so."""
        with self.lock:
            self.data["busy"][f"{chat_id}:{tag}"] = what
            self.save()

    def clear_busy(self, chat_id, tag):
        with self.lock:
            if self.data["busy"].pop(f"{chat_id}:{tag}", None) is not None:
                self.save()

    def take_busy(self):
        """{chat id: [what, ...]} still marked from before this start; the marks are cleared."""
        with self.lock:
            out = {}
            for key, what in self.data["busy"].items():
                out.setdefault(int(key.partition(":")[0]), []).append(what)
            if out:
                self.data["busy"] = {}
                self.save()
            return out

    def session(self, chat_id):
        with self.lock:
            k = self.data["chats"].get(str(chat_id), {}).get("k", 1)
        return f"tg-{chat_id}-{k}"

    def new_session(self, chat_id):
        with self.lock:
            c = self.data["chats"].setdefault(str(chat_id), {"k": 1})
            c["k"] += 1
            self.save()
        return self.session(chat_id)

    def add_proposal(self, chat_id, n, session, msg_id, html_text, enabled):
        with self.lock:
            self.data["proposals"][f"{chat_id}:{n}"] = {
                "session": session, "msg": msg_id, "html": html_text, "enabled": enabled,
                "t": int(time.time()), "done": False}
            self.save()

    def open_proposals(self, chat_id, session):
        """[(n, entry)] still showing buttons for this chat and session; they are marked done."""
        out = []
        with self.lock:
            for key, p in self.data["proposals"].items():
                cid, _, n = key.partition(":")
                if cid == str(chat_id) and p.get("session") == session and not p.get("done"):
                    p["done"] = "replaced"
                    out.append((n, p))
        return out

    def claim(self, chat_id, n, msg_id=None):
        """(entry or None, first): marks a proposal decided, so a double press is answered once.
        None: no entry (older than a day, or state lost). entry["done"] is True once decided,
        "replaced" when a newer proposal took its place, and "other" when n now belongs to a
        different message (n8n's counter restarts at 1 when the workflow is re-imported, so an old
        button must not confirm the newer proposal n)."""
        with self.lock:
            p = self.data["proposals"].get(f"{chat_id}:{n}")
            if p is None:
                return None, False
            if msg_id and p.get("msg") and p["msg"] != msg_id:
                return {"done": "other"}, False
            if p.get("done"):
                return p, False
            p["done"] = True
            self.save()
            return p, True


# ---------------------------------------------------------------- the chat webhook (n8n)

def ask_agent(body):
    """POST one request to the chat webhook. Returns (text, proposal dict or None). Every failure
    becomes a short message for the chat; nothing from n8n is trusted to be well formed."""
    if not CHAT_TOKEN:
        return ("I cannot reach the sysadmin: CHAT_TOKEN is missing (the bot reads "
                "/opt/appdata/sysadmin-chat/token.env on the apps VM).", None)
    req = urllib.request.Request(CHAT_URL, data=json.dumps(body).encode(), method="POST", headers={
        "Content-Type": "application/json", "X-Chat-Token": CHAT_TOKEN, "X-Chat-Via": "telegram"})
    try:
        with urllib.request.urlopen(req, timeout=AGENT_TIMEOUT) as resp:
            raw = resp.read(2_000_000)
    except urllib.error.HTTPError as e:
        log(f"chat webhook answered HTTP {e.code}")
        if e.code in (401, 403):
            return (f"The chat webhook refused my token (HTTP {e.code}). The token in n8n and the one in "
                    "/opt/appdata/sysadmin-chat/token.env may not match.", None)
        if e.code == 404:
            return "The chat webhook is not there (HTTP 404). Is the Homelab sysadmin workflow active in n8n?", None
        return f"n8n answered HTTP {e.code}. Try again in a minute.", None
    except Exception as e:
        reason = getattr(e, "reason", e)
        log(f"chat webhook failed: {short_error(e)}")
        if isinstance(e, TimeoutError) or isinstance(reason, TimeoutError):
            return "The sysadmin did not answer within 5 minutes. Try again, or ask something smaller.", None
        return "I could not reach n8n on the apps VM. Is it running?", None
    try:
        j = json.loads(raw)
    except ValueError:
        j = None
    if isinstance(j, list) and j and isinstance(j[0], dict):
        j = j[0]
    if not isinstance(j, dict):
        log(f"chat webhook sent something that is not a JSON object ({len(raw)} bytes)")
        return "n8n answered something I could not read. Try again in a minute.", None
    text = j.get("text")
    text = text.strip() if isinstance(text, str) and text.strip() else "(no answer)"
    if j.get("proposal") is None:
        return text, None
    proposal = parse_proposal(j["proposal"])
    if proposal is None:
        log("chat webhook sent an unreadable proposal")
        text += "\n\n(The agent proposed a fix, but I could not read it, so there is no Confirm button. Ask again.)"
    return text, proposal


def parse_proposal(p):
    if not isinstance(p, dict):
        return None
    n = p.get("n")
    if isinstance(n, str) and n.isdigit():
        n = int(n)
    if not isinstance(n, int) or isinstance(n, bool) or not 0 <= n < 10**12:
        return None
    try:
        expires = int(p.get("expires_min") or 15)
    except (TypeError, ValueError):
        expires = 15
    return {"n": n, "host": str(p.get("host") or "?")[:64], "cmd": str(p.get("cmd") or "?")[:200],
            "reason": str(p.get("reason") or "")[:300], "expires_min": expires,
            "enabled": p.get("enabled") is not False}


# ---------------------------------------------------------------- handlers (one thread per update)

store = None
BOT = {"username": None}
_chat_locks, _chat_locks_guard = {}, threading.Lock()


def chat_lock(chat_id):
    with _chat_locks_guard:
        return _chat_locks.setdefault(chat_id, threading.Lock())


def plan_message(m):
    """Runs in the poll thread, in update order, and makes no network call: it reads the command,
    applies /new and takes the chat's question lock, so updates that arrive in one batch (after
    downtime) keep their order ("/new" then a question uses the new session). Returns
    (name, work for a worker thread, lock the worker must release) or None."""
    cid, mid = m["chat"]["id"], m.get("message_id")
    text = m.get("text")
    if not isinstance(text, str):
        kind = next((k for k in NON_TEXT if k in m), None)
        log(f"chat {cid}: {kind or 'service'} message")
        if kind:
            return "reply", lambda: send_plain(cid, "I only read text messages. Type your question, or /help."), None
        return None
    text = text.strip()
    cm = COMMAND.fullmatch(text.split()[0]) if text.startswith("/") else None
    if cm:
        cmd, bot = cm[1].lower(), cm[2]
        if bot and BOT["username"] and bot.lower() != BOT["username"].lower():
            return None  # a command for another bot in a group
        log(f"chat {cid}: /{cmd}" if cmd in KNOWN_COMMANDS else f"chat {cid}: unknown command")
        if cmd in ("start", "help"):
            return "reply", lambda: send_plain(cid, HELP), None
        if cmd == "new":
            log(f"chat {cid}: new session {store.new_session(cid)}")
            return "reply", lambda: send_plain(cid, "New conversation started; I have forgotten the earlier one."), None
        if cmd not in ("status", "fixes"):
            return "reply", lambda: send_plain(cid, "I do not know that command. /help lists what I can do."), None
        text = STATUS_Q if cmd == "status" else FIXES_Q
    if not text:
        return None
    lock = chat_lock(cid)
    if not lock.acquire(blocking=False):
        log(f"chat {cid}: question while busy ({len(text)} chars), not queued")
        return "reply", lambda: send_plain(
            cid, "Still working on your last question. Send this one again once I have answered."), None
    sid = store.session(cid)
    return "ask", lambda: ask(cid, mid, text, sid), lock


def ask(cid, mid, question, sid):
    """One question to the agent; the caller holds the chat's lock."""
    cut = len(question) > MAX_QUESTION
    log(f"chat {cid}: question ({len(question)} chars, session {sid})")
    t0 = time.time()
    store.set_busy(cid, "q", "your last question; please ask it again")
    try:
        with Typing(cid):
            text, proposal = ask_agent({"message": question[:MAX_QUESTION], "sessionId": sid})
        if cut:
            text = f"(Only the first {MAX_QUESTION} characters of your message were sent.)\n\n{text}"
        send_answer(cid, text, reply_to=mid)
        log(f"chat {cid}: answered ({len(text)} chars, {time.time() - t0:.0f}s"
            + (f", proposal {proposal['n']})" if proposal else ")"))
        if proposal:
            send_proposal(cid, sid, proposal)
    finally:
        store.clear_busy(cid, "q")


def send_proposal(cid, sid, p):
    n = p["n"]
    lines = [f"<b>Proposed fix:</b> <code>{esc(p['cmd'])}</code> on <b>{esc(p['host'])}</b>"]
    if p["reason"]:
        lines.append(f"Reason: {esc(p['reason'])}")
    if p["enabled"]:
        lines.append(f"Expires in {p['expires_min']} min. It runs only if you press Confirm.")
        keyboard = [[{"text": "Confirm", "callback_data": f"c:{n}"}], [{"text": "Cancel", "callback_data": f"x:{n}"}]]
    else:
        lines.append("Chat fixes are switched off (CHAT_FIXES_ENABLED in settings.js), so this is a suggestion only.")
        keyboard = [[{"text": "Dismiss", "callback_data": f"x:{n}"}]]
    body = "\n".join(lines)
    # n8n keeps one pending proposal per session, so older buttons of this session are dead now.
    for _, old in store.open_proposals(cid, sid):
        if old.get("msg"):
            try_tg("editMessageReplyMarkup", {"chat_id": cid, "message_id": old["msg"]})
            try_tg("editMessageText", {"chat_id": cid, "message_id": old["msg"], "parse_mode": "HTML",
                                       "link_preview_options": {"is_disabled": True},
                                       "text": f"{old.get('html', '')}\n\n<i>Replaced by proposal {n}.</i>"})
    r = tg("sendMessage", {"chat_id": cid, "text": body, "parse_mode": "HTML",
                           "link_preview_options": {"is_disabled": True},
                           "reply_markup": {"inline_keyboard": keyboard}})
    store.add_proposal(cid, n, sid, (r or {}).get("message_id"), body, p["enabled"])


def callback_allowed(cq):
    """The button's chat must be allowed; outside a private chat the presser must be listed too."""
    chat = (cq.get("message") or {}).get("chat") or {}
    user = (cq.get("from") or {}).get("id")
    return chat.get("id") in ALLOWED and (chat.get("type") == "private" or user in ALLOWED)


def handle_callback(cq):
    qid, data = cq.get("id"), str(cq.get("data") or "")
    msg = cq.get("message") or {}
    cid = (msg.get("chat") or {}).get("id")
    if not callback_allowed(cq):
        log(f"ignored update from chat id {cid} (button press by user {(cq.get('from') or {}).get('id')})")
        return try_tg("answerCallbackQuery", {"callback_query_id": qid, "text": "Not allowed"}, retries_429=0)
    m = re.fullmatch(r"([cx]):(\d{1,12})", data)
    if not m:
        return try_tg("answerCallbackQuery", {"callback_query_id": qid, "text": "Unknown button"}, retries_429=0)
    confirm, n = m[1] == "c", int(m[2])
    entry, first = store.claim(cid, n, msg.get("message_id"))
    if entry is None or entry.get("done") == "other":
        # No entry of ours for this message (older than a day, or state lost), or n now belongs to
        # another message: never sent to n8n, where the same n could mean a fix this message never
        # showed. Entries outlive n8n's 15-minute expiry, so nothing live is lost.
        log(f"chat {cid}: press on proposal {n} that is not in state (or reused): buttons removed, nothing sent")
        try_tg("editMessageReplyMarkup", {"chat_id": cid, "message_id": msg.get("message_id")})
        return try_tg("answerCallbackQuery", {"callback_query_id": qid, "text": "This proposal is gone; ask again"},
                      retries_429=0)
    if not first:
        gone = "Replaced by a newer proposal" if entry.get("done") == "replaced" else "Already handled"
        return try_tg("answerCallbackQuery", {"callback_query_id": qid, "text": gone}, retries_429=0)
    try_tg("answerCallbackQuery", {"callback_query_id": qid, "text": "Confirming..." if confirm else "Cancelling..."},
           retries_429=0)
    sid = entry["session"]
    log(f"chat {cid}: {'confirm' if confirm else 'cancel'} proposal {n} (session {sid})")
    tag = f"{'c' if confirm else 'x'}{n}"
    store.set_busy(cid, tag, f"your Confirm of proposal {n}; /fixes shows whether the fix ran" if confirm
                   else f"your Cancel of proposal {n}; an unconfirmed proposal expires by itself")
    try:
        # Buttons off first (a second press cannot happen), then say what was pressed.
        if msg.get("message_id"):
            try_tg("editMessageReplyMarkup", {"chat_id": cid, "message_id": msg["message_id"]})
            if confirm:
                label = f"Confirmed at {time.strftime('%H:%M')}"
            else:
                label = "Cancelled" if entry.get("enabled", True) else "Dismissed"
            base = entry.get("html") or esc(msg.get("text") or "")
            try_tg("editMessageText", {"chat_id": cid, "message_id": msg["message_id"], "parse_mode": "HTML",
                                       "link_preview_options": {"is_disabled": True},
                                       "text": f"{base}\n\n<i>{label}</i>"})
        with Typing(cid):
            text, _ = ask_agent({"confirm" if confirm else "cancel": n, "sessionId": sid})
        send_answer(cid, text)
        log(f"chat {cid}: proposal {n} result sent ({len(text)} chars)")
    finally:
        store.clear_busy(cid, tag)


def run_handler(name, fn, cid, held=None):
    """Thread body: one bad update never takes the bot down, and the chat hears about a failure.
    held is the chat's question lock, taken in update order by plan_message."""
    try:
        try:
            fn()
        finally:  # free the chat before the error reply, so a question sent right after it is taken
            if held:
                held.release()
    except Exception as e:
        log(f"chat {cid}: {name} failed: {short_error(e)}")
        if cid is not None:
            try_tg("sendMessage", {"chat_id": cid, "text": "Something went wrong on my side; try again in a minute."})


def announce_restart():
    """Tell each chat about a question or Confirm that a restart (or crash) cut off: its update
    was already confirmed to Telegram, so it is not replayed."""
    for cid, whats in store.take_busy().items():
        if cid in ALLOWED:
            log(f"chat {cid}: telling it {len(whats)} request(s) were cut off by a restart")
            try_tg("sendMessage", {"chat_id": cid, "text": "I was restarted before I could answer:\n"
                                   + "\n".join(f"- {w}" for w in whats)})


# ---------------------------------------------------------------- polling

HEALTH = {"last_poll": None, "error": None, "started": time.time()}
_ignored_logged = {}


def log_ignored(cid, kind):
    """Log a dropped update (no content), at most once a minute per chat id."""
    now = time.time()
    if now - _ignored_logged.get(cid, 0) > 60:
        if len(_ignored_logged) > 1000:
            _ignored_logged.clear()
        _ignored_logged[cid] = now
        log(f"ignored update from chat id {cid} ({kind})")


def dispatch(u, skip_before):
    if isinstance(u.get("message"), dict):
        m = u["message"]
        cid = (m.get("chat") or {}).get("id")
        if cid not in ALLOWED:
            return log_ignored(cid, "message")
        if skip_before and m.get("date", 0) < skip_before:
            return log(f"chat {cid}: skipped a message from before the first start")
        work = plan_message(m)
        if work is None:
            return
        name, fn, held = work
    elif isinstance(u.get("callback_query"), dict):
        cq = u["callback_query"]
        cid = ((cq.get("message") or {}).get("chat") or {}).get("id")
        name, fn, held = "button press", lambda: handle_callback(cq), None  # never waits behind a question
    else:
        return log(f"ignored update of type {','.join(k for k in u if k != 'update_id')[:40]}")
    try:
        threading.Thread(target=run_handler, args=(name, fn, cid if cid in ALLOWED else None, held),
                         daemon=True).start()
    except BaseException:
        if held:
            held.release()
        raise


def startup():
    """getMe and deleteWebhook; True once both went through. setMyCommands (the command menu) is
    best-effort: its failure must not keep the bot from polling."""
    try:
        me = tg("getMe", timeout=20) or {}
        BOT["username"] = me.get("username")
        log(f"bot @{BOT['username']} (id {me.get('id')}); allowed chats: "
            + (", ".join(map(str, sorted(ALLOWED))) or "none (answering nobody)"))
        if store.check_bot(me.get("id")):
            log("state.json belongs to another bot: starting fresh (offset and open proposals cleared)")
        tg("deleteWebhook", {"drop_pending_updates": False}, timeout=20)
        try_tg("setMyCommands", {"commands": COMMANDS}, timeout=20)
        return True
    except TgError as e:
        HEALTH["error"] = str(e)
        log(f"startup call failed: {e}")
        if e.code in (401, 404):
            log("Telegram rejects the bot token: check TELEGRAM_BOT_TOKEN in telegram.env")
        return False


def poll_forever():
    # First start (no saved offset): skip messages older than FIRST_START_SKIP seconds.
    skip_before = time.time() - FIRST_START_SKIP if store.offset is None else 0
    backoff, ready, conflict_handled = 1, False, False
    while True:
        if not ready:
            ready = startup()
            if not ready:
                time.sleep(backoff)
                backoff = min(backoff * 2, 60)
                continue
            if store.offset is None and not skip_before:  # check_bot found another bot's state
                skip_before = time.time() - FIRST_START_SKIP
            announce_restart()
        params = {"timeout": POLL_TIMEOUT, "allowed_updates": ["message", "callback_query"]}
        if store.offset is not None:
            params["offset"] = store.offset
        try:
            updates = tg("getUpdates", params, timeout=POLL_TIMEOUT + 15, retries_429=0)
        except TgError as e:
            HEALTH["error"] = str(e)
            wait = backoff
            if e.code == 429:
                wait = max(e.retry_after or backoff, 1)
                log(f"getUpdates rate limited: waiting {wait}s")
            elif e.code == 409:
                log(f"getUpdates conflict: another poller or a webhook uses this bot token ({e.desc[:120]})")
                if not conflict_handled:
                    try_tg("deleteWebhook", {"drop_pending_updates": False})
                    conflict_handled = True
            elif e.code in (401, 404):
                log("Telegram rejects the bot token: check TELEGRAM_BOT_TOKEN in telegram.env")
                wait = 60
            else:
                log(f"getUpdates failed: {e}; retrying in {wait}s")
            backoff = min(backoff * 2, 60)
            time.sleep(wait)
            continue
        HEALTH["last_poll"], HEALTH["error"] = time.time(), None
        backoff, conflict_handled = 1, False
        if not isinstance(updates, list):
            continue
        for u in updates:
            if not isinstance(u, dict) or not isinstance(u.get("update_id"), int):
                continue
            try:
                dispatch(u, skip_before)
            except Exception as e:  # never die on one bad update
                log(f"update {u.get('update_id')}: dispatch failed: {short_error(e)}")
            store.set_offset(u["update_id"] + 1)
        if updates:
            store.save()  # handlers run on; a restart does not replay what was dispatched


# ---------------------------------------------------------------- HTTP: /healthz and /notify

def health():
    if not TOKEN:
        return False, "not configured: TELEGRAM_BOT_TOKEN is not set (/opt/appdata/sysadmin-telegram/telegram.env)"
    if not CHAT_TOKEN:
        return False, "not configured: CHAT_TOKEN is not set (/opt/appdata/sysadmin-chat/token.env)"
    if store and store.error:
        return False, store.error
    last, err = HEALTH["last_poll"], HEALTH["error"]
    if last is None:
        return False, "no successful getUpdates yet" + (f": {err}" if err else "")
    age = time.time() - last
    if age > HEALTH_STALE:
        return False, f"last successful getUpdates {age / 60:.0f} min ago" + (f": {err}" if err else "")
    who = f"@{BOT['username']}" if BOT["username"] else "bot"
    chats = f"{len(ALLOWED)} allowed chat(s)" if ALLOWED else "no allowed chats (TELEGRAM_ALLOWED_CHAT_IDS is empty)"
    return True, f"ok: {who}, last getUpdates {age:.0f}s ago, {chats}"


def notify(subject, text):
    """Send "subject\\n\\ntext" as plain text to every allowed chat. Returns (sent, failed)."""
    msg = f"{subject}\n\n{text}".strip() if subject else text.strip()
    chunks = split_text(msg, fences=False)
    if len(chunks) > NOTIFY_MAX_CHUNKS:
        chunks = chunks[:NOTIFY_MAX_CHUNKS]
        chunks[-1] += "\n\n(cut here)"
    sent = failed = 0
    for cid in sorted(ALLOWED):
        try:
            for chunk in chunks:
                tg("sendMessage", {"chat_id": cid, "text": chunk, "link_preview_options": {"is_disabled": True}},
                   timeout=10, retries_429=1, max_wait=8)  # n8n waits 30 s at most
            sent += 1
        except TgError as e:
            failed += 1
            log(f"notify to chat {cid} failed: {e}")
    log(f"notify: {len(msg)} chars in {len(chunks)} message(s) to {sent} chat(s), {failed} failed")
    return sent, failed


class Handler(http.server.BaseHTTPRequestHandler):
    server_version, sys_version = "sysadmin-telegram", ""
    timeout = 15  # socket timeout: an idle or stalled client cannot pin a thread (mem_limit 128m)

    def reply(self, code, body, ctype="text/plain; charset=utf-8"):
        data = (body if isinstance(body, str) else json.dumps(body)).encode()
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        if self.path.split("?")[0] != "/healthz":
            return self.reply(404, "not found\n")
        ok, text = health()
        self.reply(200 if ok else 503, redact(text) + "\n")

    def do_POST(self):
        if self.path.split("?")[0] != "/notify":
            return self.reply(404, "not found\n")
        js = "application/json"
        if not CHAT_TOKEN:
            return self.reply(503, {"error": "CHAT_TOKEN is not set"}, js)
        given = self.headers.get("X-Chat-Token", "").encode("utf-8", "replace")
        if not hmac.compare_digest(given, CHAT_TOKEN.encode()):
            return self.reply(401, {"error": "bad or missing X-Chat-Token"}, js)
        try:
            length = int(self.headers.get("Content-Length", ""))
        except ValueError:
            return self.reply(411, {"error": "Content-Length required"}, js)
        if length < 0 or length > NOTIFY_MAX_BODY:
            return self.reply(413, {"error": f"body over {NOTIFY_MAX_BODY} bytes"}, js)
        try:
            body = json.loads(self.rfile.read(length) or b"null")
        except ValueError:
            body = None
        if not isinstance(body, dict) or not isinstance(body.get("subject", ""), str) \
                or not isinstance(body.get("text", ""), str) or not (body.get("subject") or body.get("text")):
            return self.reply(400, {"error": 'expected JSON {"subject": str, "text": str}'}, js)
        if not TOKEN:
            return self.reply(503, {"error": "not configured: TELEGRAM_BOT_TOKEN is not set"}, js)
        sent, failed = notify(body.get("subject", "").strip(), body.get("text", ""))
        self.reply(502 if failed else 200, {"sent": sent, "failed": failed}, js)

    def log_message(self, *args):
        pass  # Kuma polls /healthz every minute; failures are logged where they happen


def _stop(*_):
    try:
        log("stopping")
    finally:  # exit even if the log line fails (a print interrupted mid-write raises "reentrant call")
        os._exit(0)


def main():
    global store
    signal.signal(signal.SIGTERM, _stop)  # PID 1 needs a handler
    server = http.server.ThreadingHTTPServer(("0.0.0.0", HEALTH_PORT), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    if not TOKEN:
        log(f"TELEGRAM_BOT_TOKEN is not set: idling; /healthz on :{HEALTH_PORT} says \"not configured\". "
            "Run docs/telegram_setup.py on the Mac to set the bot up.")
        threading.Event().wait()  # idle for good; a restart picks up telegram.env
    if not CHAT_TOKEN:
        log("CHAT_TOKEN is not set (sysadmin-chat token.env): questions get an error reply")
    if not ALLOWED:
        log("TELEGRAM_ALLOWED_CHAT_IDS is empty: answering nobody; ignored chat ids are logged")
    try:
        os.makedirs(STATE_DIR, exist_ok=True)
    except OSError as e:
        log(f"cannot create {STATE_DIR}: {short_error(e)}")
    store = Store(STATE_DIR)
    store.save()  # a /data the storage user cannot write shows up in /healthz now, not after a restart
    log(f"polling Telegram; chat webhook {CHAT_URL}; health on :{HEALTH_PORT}")
    while True:
        try:
            poll_forever()
        except Exception as e:  # a bug in the loop: log it (redacted) and carry on polling
            log(f"poll loop failed: {short_error(e)}; restarting it in 10s")
            time.sleep(10)


if __name__ == "__main__":
    main()
