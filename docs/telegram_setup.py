#!/usr/bin/env python3
"""Connect the sysadmin Telegram bot (stacks/apps/sysadmin-telegram) to Goobs's private chat.

Goobs runs this on the Mac from ~/HomeLab/homelab-infra with the system Python (standard library
only, Python 3.9 is enough; no Ansible venv), after the stacks play has deployed the service:

    python3 docs/telegram_setup.py                 # find your chat, write telegram.env, start the bot
    python3 docs/telegram_setup.py --chat-id 123   # use this chat id (e.g. from an "ignored update" log line)
    python3 docs/telegram_setup.py --dry-run       # every check and lookup, nothing written on the VM

Before the first run: @BotFather /newbot, copy the token, then (keeps it out of shell history)
    (umask 077; mkdir -p ~/.config/homelab && pbpaste > ~/.config/homelab/telegram-bot-token)
Keep Telegram at hand: the script shows a one-time code to send to the bot.

What it does, in order:
  1. Reads the token from ~/.config/homelab/telegram-bot-token (refused unless only you can read
     it) and asks Telegram who the bot is (getMe). The token is never printed: it is redacted
     from every error, and it reaches the VM only on ssh's stdin, never on a command line.
  2. Checks the apps VM over ssh: /opt/stacks/apps/compose.yaml has the sysadmin-telegram service
     (else it prints the stacks play command), the chat token exists, PUID/PGID for the data folder.
  3. Stops the bot container (two pollers on one token get 409 Conflict), deletes any webhook,
     prints a one-time code and asks you to send "/start <code>" to the bot, then reads the
     pending updates (getUpdates without an offset confirms nothing) and takes the private chat
     that sent the code (not seen yet: Enter looks again). Anyone can find a bot and message it,
     and a name can be copied, so a chat without the code is never offered, nor is a group. With
     --chat-id it asks Telegram about that chat instead (getChat) and skips the code.
  4. Writes /opt/appdata/sysadmin-telegram/telegram.env (root, 600: TELEGRAM_BOT_TOKEN,
     TELEGRAM_ALLOWED_CHAT_IDS), creates data/ owned by the storage user, recreates the container,
     waits for /healthz and sends "Homelab sysadmin bot is connected" to your chat.
If it stops after step 3 (nothing chosen, Ctrl-C), a bot that was running is started again as it was.
"""
import argparse
import json
import os
import pathlib
import re
import secrets
import shlex
import ssl
import stat
import subprocess
import sys
import time
import traceback
import urllib.error
import urllib.parse
import urllib.request

APPS = "goobs@192.168.1.53"
COMPOSE = "/opt/stacks/apps/compose.yaml"
DC = f"sudo docker compose -f {COMPOSE}"
SERVICE = "sysadmin-telegram"
APPDATA = "/opt/appdata/sysadmin-telegram"
ENV_FILE = f"{APPDATA}/telegram.env"
DATA_DIR = f"{APPDATA}/data"
CHAT_TOKEN_ENV = "/opt/appdata/sysadmin-chat/token.env"  # CHAT_TOKEN, made by docs/sysadmin_deploy.py
# The storage user every app runs as (ansible/inventory/group_vars/docker_vms.yml app_uid/app_gid);
# the VM's /opt/stacks/apps/.env PUID/PGID win when they are set.
APP_UID = APP_GID = 3000
TOKEN_FILE = pathlib.Path.home() / ".config/homelab/telegram-bot-token"
TOKEN_RE = re.compile(r"[0-9]+:[A-Za-z0-9_-]{30,}")
API = os.environ.get("TELEGRAM_API", "https://api.telegram.org").rstrip("/")  # tests point it at a mock
HEALTH = "curl -sS -m 5 -w '\\n%{http_code}' http://127.0.0.1:3004/healthz"
HEALTH_WAIT = 120  # seconds; the bot's first long poll can take 50 s when nothing is pending
TEST_TEXT = "Homelab sysadmin bot is connected. Ask me anything, or /help."
STACKS_CMD = ("cd ansible && ~/.local/share/homelab-ansible/bin/ansible-playbook -i inventory/hosts.yml "
              "playbooks/site.yml --tags stacks --limit apps")

SECRETS = []  # the token (and its secret half) once read; redact() removes them from any text
STOPPED = {"bot": False}  # True while a bot that was running is stopped by us


def redact(text):
    text = str(text)
    for s in SECRETS:
        text = text.replace(s, "<token>")
    return text


def die(msg, code=1):
    print(redact(msg), file=sys.stderr)
    restore_bot()
    sys.exit(code)


def _excepthook(exc_type, exc, tb):
    sys.stderr.write(redact("".join(traceback.format_exception(exc_type, exc, tb))))


sys.excepthook = _excepthook


def clean(s, limit=64):
    """Names come from any Telegram user: no control characters (terminal escapes) on screen."""
    return "".join(c for c in str(s or "") if c.isprintable())[:limit]


# ---------------------------------------------------------------- ssh (mirrors docs/sysadmin_deploy.py)

def ssh(cmd, stdin=None, check=True):
    # stdin "" rather than the terminal, so ssh never swallows what you type at a prompt.
    r = subprocess.run(["ssh", "-o", "BatchMode=yes", APPS, cmd], input=stdin if stdin is not None else "",
                       capture_output=True, text=True)
    if check and r.returncode != 0:
        die(f"ssh failed: {cmd[:80]}...\n{r.stderr.strip()[-500:]}")
    return r.stdout


def restore_bot():
    if STOPPED["bot"]:
        STOPPED["bot"] = False
        print("Starting the bot again with its old settings...", file=sys.stderr)
        ssh(f"{DC} start {SERVICE}", check=False)


# ---------------------------------------------------------------- Telegram Bot API

class TgError(Exception):
    """A Bot API failure; code is Telegram's error_code (0 for network errors). Never holds the token."""

    def __init__(self, method, code, desc):
        super().__init__(f"{method}: {code or 'network error'} {desc}")
        self.code, self.desc = code, desc


def ssl_context():
    ctx = ssl.create_default_context()
    # A python.org Python on macOS has no CA certificates until "Install Certificates.command" runs;
    # the system bundle at /etc/ssl/cert.pem covers it. Verification stays on either way.
    if os.path.exists("/etc/ssl/cert.pem"):
        try:
            ctx.load_verify_locations("/etc/ssl/cert.pem")
        except (OSError, ssl.SSLError):
            pass
    return ctx


def check_api():
    p = urllib.parse.urlsplit(API)
    local = p.hostname in ("127.0.0.1", "localhost", "::1")
    if p.scheme != "https" and not (p.scheme == "http" and local):
        sys.exit(f"TELEGRAM_API={API} is not https: refusing to send the bot token there.")
    if API != "https://api.telegram.org":
        print(f"Note: TELEGRAM_API is set; using {API} instead of Telegram.")


def tg(token, method, params=None, timeout=20):
    url = f"{API}/bot{token}/{method}"
    data = json.dumps(params or {}).encode()
    for attempt in range(2):
        req = urllib.request.Request(url, data=data, headers={"Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=timeout, context=ssl_context()) as resp:
                status, body = resp.status, resp.read()
        except urllib.error.HTTPError as e:
            status = e.code
            try:
                body = e.read()
            except Exception:
                body = b""
        except Exception as e:  # URLError, timeouts, TLS: the URL (and so the token) is dropped
            msg = redact(f"{type(e).__name__}: {e}")[:300]
            if "CERTIFICATE_VERIFY_FAILED" in msg:
                msg += ("\nPython could not verify Telegram's certificate. With a python.org Python, run "
                        "'Install Certificates.command' in /Applications/Python 3.x once, or use /usr/bin/python3.")
            raise TgError(method, 0, msg) from None
        try:
            j = json.loads(body)
        except ValueError:
            j = {}
        if not isinstance(j, dict):
            j = {}
        if status == 200 and j.get("ok"):
            return j.get("result")
        code = j.get("error_code") or status
        params_ = j.get("parameters") if isinstance(j.get("parameters"), dict) else {}
        retry = params_.get("retry_after")
        if code == 429 and isinstance(retry, int) and retry <= 30 and attempt == 0:
            print(f"  Telegram asks to wait {retry}s...")
            time.sleep(retry + 0.5)
            continue
        raise TgError(method, code, redact(j.get("description") or f"HTTP {status}")[:200])


# ---------------------------------------------------------------- local checks

def read_token(path):
    shown = shlex.quote(str(path))
    if not path.exists():
        sys.exit(f"{path} not found. Get the token from @BotFather (/newbot, or /mybots > API Token), "
                 f"copy it, then on the Mac:\n    (umask 077; mkdir -p {shlex.quote(str(path.parent))} "
                 f"&& pbpaste > {shown})")
    st = path.stat()
    if not stat.S_ISREG(st.st_mode):
        sys.exit(f"{path} is not a regular file.")
    if st.st_mode & 0o077:
        sys.exit(f"{path} has mode {stat.S_IMODE(st.st_mode):03o}: other users on this Mac could read "
                 f"the bot token. Make it yours only, then re-run:\n    chmod 600 {shown}")
    try:
        token = path.read_text().strip()
    except (OSError, UnicodeDecodeError) as e:
        sys.exit(f"Cannot read {path}: {type(e).__name__}")
    if not TOKEN_RE.fullmatch(token):
        sys.exit(f"{path} does not hold a bot token: expected exactly what @BotFather shows, "
                 f"<digits>:<30+ letters, digits, _ or ->, on one line and nothing else.")
    SECRETS.extend([token, token.split(":", 1)[1], urllib.parse.quote(token, safe="")])
    return token


def full_name(chat):
    return clean(" ".join(str(x) for x in (chat.get("first_name"), chat.get("last_name")) if x))


def private_chats(updates, code):
    """Private chats in a getUpdates result whose own text holds the one-time code, newest first,
    plus the ids of the other chats seen: groups and channels, and private chats without the code.
    Anyone can find the bot and send /start, and names are free text anyone can copy, so only the
    code (printed on this Mac a moment ago) shows that a chat is Goobs's."""
    chats, groups, uncoded = {}, set(), set()
    has_code = re.compile(rf"(?<![0-9a-z]){re.escape(code)}(?![0-9a-z])")
    for u in updates if isinstance(updates, list) else []:
        if not isinstance(u, dict):
            continue
        for key in ("message", "edited_message", "my_chat_member", "callback_query"):
            obj = u.get(key)
            if key == "callback_query" and isinstance(obj, dict):
                obj = obj.get("message")  # the bot's own message: its text proves nothing
            chat = obj.get("chat") if isinstance(obj, dict) else None
            if not isinstance(chat, dict) or type(chat.get("id")) is not int:
                continue
            if chat.get("type") != "private":
                groups.add(chat["id"])
                continue
            text = obj.get("text") if key in ("message", "edited_message") else None
            if not isinstance(text, str) or not has_code.search(text.lower()):
                uncoded.add(chat["id"])
                continue
            date = obj.get("date") if type(obj.get("date")) is int else 0
            if chat["id"] not in chats or date >= chats[chat["id"]]["date"]:
                chats[chat["id"]] = {"id": chat["id"], "date": date,
                                     "name": full_name(chat),
                                     "username": clean(chat.get("username"), 40)}
    return sorted(chats.values(), key=lambda c: -c["date"]), groups, uncoded - set(chats)


def describe(c):
    who = c["name"] or "(no name)"
    if c["username"]:
        who += f" (@{c['username']})"
    when = time.strftime(", last message %Y-%m-%d %H:%M", time.localtime(c["date"])) if c.get("date") else ""
    return f"{who}, chat id {c['id']}{when}"


def ask(prompt):
    try:
        return input(prompt).strip().lower()
    except EOFError:  # no terminal (piped, cron): treat as "no"
        print()
        return "n"


def choose(chats):
    """The chat id Goobs picks, or None. Every chat offered sent the code, so one is taken as it
    is; several (the code sent from two accounts) need its number."""
    if len(chats) == 1:
        print(f"  {describe(chats[0])} sent the code.")
        return chats[0]["id"]
    print("  More than one chat sent the code:")
    for i, c in enumerate(chats, 1):
        print(f"  {i}. {describe(c)}")
    ans = ask(f"Which one is yours? [1-{len(chats)}, Enter = none] ")
    if ans.isdigit() and 1 <= int(ans) <= len(chats):
        return chats[int(ans) - 1]["id"]
    return None


def parse_kv(text):
    out = {}
    for line in text.splitlines():
        k, sep, v = line.strip().partition("=")
        if sep:
            out[k] = v.strip().strip("'\"")
    return out


# ---------------------------------------------------------------- the run

def preflight(dry):
    """Read-only look at the apps VM. Returns a dict (empty when ssh failed in a dry run)."""
    script = "; ".join([
        f"f={COMPOSE}",
        f"if sudo grep -qE '^[[:space:]]+{SERVICE}:' $f; then echo service=yes; else echo service=no; fi",
        f"if sudo test -f /opt/stacks/apps/{SERVICE}/bot.py; then echo bot=yes; else echo bot=no; fi",
        f"if sudo test -s {CHAT_TOKEN_ENV}; then echo chat_token=yes; else echo chat_token=no; fi",
        "sudo grep -E '^(PUID|PGID)=' /opt/stacks/apps/.env 2>/dev/null",
        f"sudo grep -E '^TELEGRAM_ALLOWED_CHAT_IDS=' {ENV_FILE} 2>/dev/null",
        f"echo running=$({DC} ps -q --status running {SERVICE} 2>/dev/null | head -c 12)",
        "true",
    ])
    r = subprocess.run(["ssh", "-o", "BatchMode=yes", APPS, script], input="", capture_output=True, text=True)
    if r.returncode != 0 or "service=" not in r.stdout:
        msg = f"Cannot check the apps VM over ssh ({APPS}): {r.stderr.strip()[-300:]}"
        if dry:
            print(f"WARNING: {msg}\n  (dry run: carrying on without the VM checks)")
            return {}
        die(msg)
    vm = parse_kv(r.stdout)
    missing = [what for key, what in (("service", f"the {SERVICE} service in {COMPOSE}"),
                                      ("bot", f"/opt/stacks/apps/{SERVICE}/bot.py")) if vm.get(key) != "yes"]
    if missing:
        msg = (f"The apps VM does not have {' or '.join(missing)} yet: the stacks play has not run since "
               f"the Telegram bot was added. From ~/HomeLab/homelab-infra run\n    {STACKS_CMD}\nthen re-run this.")
        if not dry:
            die(msg)
        print(f"WARNING: {msg}\n  (dry run: carrying on)")
    if vm.get("chat_token") != "yes":
        msg = (f"{CHAT_TOKEN_ENV} is missing on the apps VM, so the bot could not reach n8n. "
               "Run python3 docs/sysadmin_deploy.py first (it makes the chat token), then re-run this.")
        if not dry:
            die(msg)
        print(f"WARNING: {msg}")
    return vm


def ids_from(vm):
    """PUID/PGID from the VM's .env (what the container runs as), else the storage user's 3000:3000."""
    uid, gid = vm.get("PUID", ""), vm.get("PGID", "")
    if uid.isdigit() and gid.isdigit():
        return int(uid), int(gid)
    return APP_UID, APP_GID


def lookup_chat(token, chat_id, bot):
    try:
        chat = tg(token, "getChat", {"chat_id": chat_id}) or {}
    except TgError as e:
        if e.code in (400, 403):
            die(f"@{bot} cannot see chat {chat_id} ({e.desc}). From that Telegram account, open @{bot}, "
                "send /start (unblock it if needed), then re-run.")
        die(f"getChat failed: {e}")
    if chat.get("type") != "private":
        die(f"Chat {chat_id} is a {clean(chat.get('type'), 20) or 'non-private'} chat: only a private chat can be allowed.")
    c = {"id": chat_id, "date": 0, "name": full_name(chat),
         "username": clean(chat.get("username"), 40)}
    print(f"  {describe(c)}")
    return chat_id


def find_chat(token, bot, dry, vm, code):
    """Ask Goobs to send the one-time code, then getUpdates (no offset: confirms nothing) and take
    the private chat that sent it."""
    link = f"\n  (or open https://t.me/{bot}?start={code} and press Start)" if re.fullmatch(r"\w{5,32}", bot) else ""
    print(f"To show which chat is yours, send this to @{bot} from your Telegram account now:\n"
          f"    /start {code}{link}")
    prompt = "Press Enter once you have sent it (q to quit): "
    while True:
        if ask(prompt) not in ("", "y", "yes"):
            break
        try:
            updates = tg(token, "getUpdates", {"timeout": 0, "limit": 100})
        except TgError as e:
            if e.code == 409:
                if dry and "webhook" in e.desc.lower():
                    die("Telegram answers 409: a webhook is set on the bot, so it refuses getUpdates. The real run "
                        "deletes the webhook first; re-run without --dry-run.")
                if dry and vm.get("running"):
                    die("Telegram answers 409 Conflict: the bot on the apps VM is polling, and a dry run does not "
                        "stop it. Re-run without --dry-run, or with --chat-id <id>.")
                die(f"Telegram answers 409 Conflict ({e.desc}): another copy of the bot polls this token "
                    "(an old container elsewhere, or a test run). Stop it, then re-run.")
            die(f"getUpdates failed: {e}")
        chats, groups, uncoded = private_chats(updates, code)
        if groups:
            print(f"  (ignoring {len(groups)} group or channel chat(s): only a private chat can be allowed)")
        if uncoded:
            print(f"  (ignoring {len(uncoded)} private chat(s) that did not send the code)")
        if chats:
            chat_id = choose(chats)
            if chat_id is None:
                die("Nothing chosen, nothing written. Re-run and pick your chat, or pass --chat-id <id>.")
            return chat_id
        # In a real run the bot on the VM is stopped now, so the message waits for us here.
        prompt = f"  not seen yet. Send /start {code} to @{bot}, then press Enter to look again (q to quit): "
    old = clean(vm.get("TELEGRAM_ALLOWED_CHAT_IDS"), 40)
    hint = f" To keep the chat the VM allows now, re-run with --chat-id {old}." if old.isdigit() else ""
    if dry and vm.get("running"):
        hint += (" The bot on the VM is running during a dry run, so it may read your message before this "
                 "script can; a real run stops it first.")
    die(f"Nothing chosen, nothing written: no private chat sent /start {code} to @{bot}. Re-run and send "
        f"/start <the code it shows> when it asks, or pass --chat-id <id> (the id of a chat the bot has "
        f"ignored is in its log: ssh {APPS} \"{DC} logs {SERVICE} | grep 'ignored update'\").{hint}")


def wait_healthy():
    print(f"Waiting for the bot's first poll (up to {HEALTH_WAIT // 60} min)...")
    deadline, body = time.time() + HEALTH_WAIT, ""
    while time.time() < deadline:
        out = ssh(HEALTH, check=False).rstrip("\n").split("\n")
        code, body = out[-1].strip(), "\n".join(out[:-1]).strip()
        if code == "200":
            return True, body
        if body.startswith("not configured"):
            break  # telegram.env or token.env did not reach the container; waiting will not help
        time.sleep(3)
    return False, body


def main(argv=None):
    ap = argparse.ArgumentParser(description="Connect the sysadmin Telegram bot to your private chat "
                                             "(writes telegram.env on the apps VM and starts the bot).")
    ap.add_argument("--chat-id", type=int, help="allow this private chat id instead of picking from the "
                                                "chats that wrote to the bot")
    ap.add_argument("--dry-run", action="store_true", help="do every check and lookup, write nothing on the VM "
                                                           "and send nothing")
    ap.add_argument("--token-file", type=pathlib.Path, default=TOKEN_FILE, help=argparse.SUPPRESS)  # tests
    ap.add_argument("--pair-code", help=argparse.SUPPRESS)  # tests: a fixed one-time code
    args = ap.parse_args(argv)
    if args.chat_id is not None and args.chat_id <= 0:
        sys.exit("--chat-id must be a private chat id (a positive number); group chats cannot be allowed.")
    if args.pair_code is not None and not re.fullmatch(r"[0-9a-f]{6,32}", args.pair_code):
        sys.exit("--pair-code must be 6 to 32 lowercase hex digits.")
    code = args.pair_code or secrets.token_hex(3)  # 6 hex digits, new on every run
    dry = args.dry_run
    check_api()

    token = read_token(args.token_file)
    try:
        me = tg(token, "getMe") or {}
    except TgError as e:
        if e.code in (401, 404):
            sys.exit(f"Telegram rejects the token in {args.token_file} ({e.code} {e.desc}): it was revoked or "
                     "mistyped. Get the current one from @BotFather (/mybots > your bot > API Token).")
        sys.exit(f"getMe failed: {e}")
    bot = clean(me.get("username"), 40) or "your bot"
    print(f"Bot: @{bot} (id {me.get('id')})")

    print("Checking the apps VM...")
    vm = preflight(dry)
    uid, gid = ids_from(vm)

    if args.chat_id is None and not dry:
        print("Stopping the bot on the apps VM while we read its messages...")
        STOPPED["bot"] = bool(vm.get("running"))  # before the stop: a Ctrl-C during it still restarts the bot
        ssh(f"{DC} stop {SERVICE}", check=False)  # fine when it never ran
    if dry:
        hook = tg(token, "getWebhookInfo") or {}
        if hook.get("url"):
            host = urllib.parse.urlsplit(hook["url"]).hostname
            print(f"  A webhook is set (to {clean(host)}); the real run deletes it (the bot only long-polls).")
    else:
        tg(token, "deleteWebhook", {"drop_pending_updates": False})

    if args.chat_id is not None:
        print(f"Looking up chat {args.chat_id}...")
        chat_id = lookup_chat(token, args.chat_id, bot)
    else:
        chat_id = find_chat(token, bot, dry, vm, code)
    print(f"Allowing chat id {chat_id}.")
    old = vm.get("TELEGRAM_ALLOWED_CHAT_IDS")
    if old and old != str(chat_id):
        print(f"  (replaces the allowed chat(s) {clean(old)})")

    if dry:
        print(f"Dry run, nothing written. A real run would write {ENV_FILE} (root, 600) with "
              f"TELEGRAM_BOT_TOKEN=<token> and TELEGRAM_ALLOWED_CHAT_IDS={chat_id}, create {DATA_DIR} "
              f"owned by {uid}:{gid}, recreate {SERVICE}, wait for /healthz and send a test message.")
        return

    print(f"Writing {ENV_FILE} on the apps VM...")
    ssh(f"sudo install -d -m 750 -o root -g root {APPDATA} && "
        f"sudo sh -c 'umask 077; cat > {ENV_FILE}.tmp && chmod 600 {ENV_FILE}.tmp && mv -f {ENV_FILE}.tmp {ENV_FILE}'",
        stdin=f"TELEGRAM_BOT_TOKEN={token}\nTELEGRAM_ALLOWED_CHAT_IDS={chat_id}\n")
    # Docker creates a missing bind-mount folder as root, which the bot (storage user) cannot write.
    ssh(f"sudo install -d -m 750 -o {uid} -g {gid} {DATA_DIR} && sudo chown -R {uid}:{gid} {DATA_DIR}")
    print("Starting the bot...")
    ssh(f"{DC} up -d --no-deps --force-recreate {SERVICE}")
    STOPPED["bot"] = False
    ok, body = wait_healthy()
    if not ok:
        logs = ssh(f"{DC} logs --no-color --tail 30 {SERVICE}", check=False)
        die(f"/healthz is not ok after the wait: {body or 'no answer'}\nLast log lines:\n{logs.rstrip()}\n"
            "See docs/wiki/operations/sysadmin.md > Telegram bot > Troubleshooting.")
    print(f"  {clean(body, 200)}")

    print("Sending a test message...")
    try:
        tg(token, "sendMessage", {"chat_id": chat_id, "text": TEST_TEXT})
    except TgError as e:
        die(f"The bot runs, but the test message failed ({e}). If you blocked the bot, unblock it in Telegram.")
    print("Done. The bot answers that chat now: try /status. If you have not yet since this change, run\n"
          "    python3 docs/sysadmin_deploy.py   (critical alerts pushed to Telegram)\n"
          "    ~/.local/share/homelab-ansible/bin/python docs/kuma_setup.py   (adds the 'Sysadmin Telegram bot' monitor)")


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\nInterrupted.", file=sys.stderr)
        restore_bot()
        sys.exit(130)
    except TgError as e:
        die(f"Telegram call failed: {e}")
    except Exception:  # an unexpected bug: restart a bot we stopped, then show the (redacted) traceback
        restore_bot()
        raise
