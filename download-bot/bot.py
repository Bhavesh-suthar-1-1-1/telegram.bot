#!/usr/bin/env python3
"""Anonymous relay bot for Telegram (text only).

Anyone may DM the bot in private. Their text message is copied to the
owner (OWNER_ID) with a header saying who it is from. The owner replies
to that message and the bot copies the reply back to the sender — the
sender only ever talks to the bot and never sees the owner's profile
or username. The bot's replies are always its own messages, never
forwards, so no personal account is ever exposed.

Text only: no downloads, no files. A visitor sending anything but text
gets a short "text only" note. Owner commands (reply to a relayed
message): /block, /unblock. Owner commands: /id.

Config: BOT_TOKEN env var or token.txt next to this file.
Optional: ALLOWED_IDS env var (comma-separated Telegram user IDs),
OWNER_ID env var (who the messages go to).
Run: python3 bot.py   (long-polling, no public port needed)
"""
import os, sys, json, time

BASE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(BASE, "pylibs"))

import requests


def get_token():
    tok = os.environ.get("BOT_TOKEN", "").strip()
    if tok:
        return tok
    p = os.path.join(BASE, "token.txt")
    if os.path.exists(p):
        return open(p).read().strip()
    sys.exit("No BOT_TOKEN env var and no token.txt — cannot start.")


TOKEN = get_token()
API = f"https://api.telegram.org/bot{TOKEN}"
ALLOWED = {int(x) for x in os.environ.get("ALLOWED_IDS", "").replace(" ", "").split(",") if x}
OWNER_ID = int(os.environ.get("OWNER_ID", "7861615131"))
RELAY_TARGET = OWNER_ID
MAP_FILE = os.path.join(BASE, "relay_map.json")
BLOCK_FILE = os.path.join(BASE, "blocked.json")


def _load_json(path, default):
    try:
        with open(path) as f:
            return json.load(f)
    except Exception:
        return default


def _save_json(path, data):
    try:
        tmp = path + ".tmp"
        with open(tmp, "w") as f:
            json.dump(data, f)
        os.replace(tmp, path)
    except Exception as e:
        print(f"[relay] save {path} failed: {e}", flush=True)


relay_map = _load_json(MAP_FILE, {})       # str(owner msg id) -> sender chat id
blocked = set(_load_json(BLOCK_FILE, []))  # sender user ids
_flood = {}  # user_id -> [timestamps]


def tg(method, **kwargs):
    try:
        r = requests.post(f"{API}/{method}", json=kwargs.get("json", {}), timeout=60)
        return r.json()
    except Exception as e:
        print(f"[tg] {method} failed: {e}", flush=True)
        return {"ok": False, "description": str(e)}


def send_msg(chat_id, text, reply_to=None):
    payload = {"chat_id": chat_id, "text": text}
    if reply_to:
        payload["reply_to_message_id"] = reply_to
    return tg("sendMessage", json=payload)


def _flooded(user_id):
    now = time.time()
    ts = [t for t in _flood.get(user_id, []) if now - t < 30]
    ts.append(now)
    _flood[user_id] = ts
    return len(ts) > 5


def _sender_label(msg):
    frm = msg.get("from") or {}
    name = " ".join(x for x in (frm.get("first_name"), frm.get("last_name")) if x) or "?"
    uname = frm.get("username")
    who = f"@{uname}" if uname else name
    return f"\U0001f4e9 From {who} ({name}) \u2022 id {frm.get('id')}"


def _record(owner_msg_id, sender_chat_id):
    relay_map[str(owner_msg_id)] = sender_chat_id
    if len(relay_map) > 1000:  # prune oldest
        for k in list(relay_map)[: len(relay_map) - 1000]:
            relay_map.pop(k, None)
    _save_json(MAP_FILE, relay_map)


def _copy(chat_id, from_chat_id, message_id):
    return tg("copyMessage", json={"chat_id": chat_id,
                                   "from_chat_id": from_chat_id,
                                   "message_id": message_id})


def relay_to_owner(msg):
    """Copy a visitor's private text message to the owner; remember who it was from."""
    chat_id = (msg.get("chat") or {}).get("id")
    user_id = (msg.get("from") or {}).get("id")
    if user_id in blocked or _flooded(user_id):
        return
    text = msg.get("text") or ""
    if not text:
        send_msg(chat_id, "Please send text only — I can't forward photos, files or links as downloads.")
        return
    r = send_msg(RELAY_TARGET, f"{_sender_label(msg)}\n\n{text}")
    mid = (r.get("result") or {}).get("message_id")
    if mid:
        _record(mid, chat_id)


def send_back(owner_msg):
    """Owner replied (text) to a relayed message: send it to the sender as the bot."""
    reply = (owner_msg.get("reply_to_message") or {}).get("message_id")
    sender = relay_map.get(str(reply))
    if not sender:
        return False
    text = owner_msg.get("text") or ""
    if not text:
        send_msg(owner_msg["chat"]["id"],
                 "I only send text back — send your reply as text.")
        return True
    send_msg(sender, text)
    return True


VISITOR_WELCOME = ("Hi! Send a message here and it will reach Bhavesh — "
                   "he replies here, in this chat, through the bot.\n\n"
                   "Text only. You never see his personal account. He only "
                   "sees your name and username, nothing else about your account.")

OWNER_WELCOME = ("Relay bot — text only.\n\n"
                 "Anyone who DMs me gets their message copied to you with "
                 "their name on it. Reply to that message and I send your "
                 "reply back as the bot — they never see your account.\n\n"
                 "Commands: /block and /unblock (reply to a person's "
                 "message), /id — your Telegram ID.")


def main():
    me = tg("getMe").get("result", {})
    print(f"Bot live: @{me.get('username')} (id {me.get('id')})", flush=True)
    tg("deleteWebhook", json={})
    offset = 0
    while True:
        try:
            r = requests.get(f"{API}/getUpdates",
                             params={"offset": offset, "timeout": 50},
                             timeout=70)
            data = r.json()
            if not data.get("ok"):
                time.sleep(3)
                continue
            for upd in data["result"]:
                offset = upd["update_id"] + 1
                msg = upd.get("message") or upd.get("channel_post") or {}
                chat_id = (msg.get("chat") or {}).get("id")
                user_id = (msg.get("from") or {}).get("id")
                text = msg.get("text") or ""
                if chat_id is None:
                    continue
                chat_type = (msg.get("chat") or {}).get("type", "private")
                is_owner = (not ALLOWED) or (user_id in ALLOWED)
                if not is_owner and chat_type != "private":
                    send_msg(chat_id, "This is a private bot.")
                    continue
                if not is_owner:
                    # visitor in a private chat -> anonymous relay, text only
                    if text.startswith("/start") or text.startswith("/help"):
                        send_msg(chat_id, VISITOR_WELCOME)
                    else:
                        relay_to_owner(msg)
                    continue
                # owner from here on
                if text.startswith("/block") or text.startswith("/unblock"):
                    rep = (msg.get("reply_to_message") or {}).get("message_id")
                    sender = relay_map.get(str(rep)) if rep else None
                    if not sender:
                        send_msg(chat_id, "Reply to the person's message with /block or /unblock.")
                    elif text.startswith("/block"):
                        blocked.add(sender)
                        _save_json(BLOCK_FILE, sorted(blocked))
                        send_msg(chat_id, f"Blocked {sender}. They can't message through the bot anymore.")
                    else:
                        blocked.discard(sender)
                        _save_json(BLOCK_FILE, sorted(blocked))
                        send_msg(chat_id, f"Unblocked {sender}.")
                    continue
                rep_id = (msg.get("reply_to_message") or {}).get("message_id")
                if rep_id is not None and str(rep_id) in relay_map:
                    if send_back(msg):
                        continue
                if text.startswith("/start") or text.startswith("/help"):
                    send_msg(chat_id, OWNER_WELCOME)
                elif text.startswith("/id"):
                    send_msg(chat_id, f"Your user ID: {user_id}\nThis chat ID: {chat_id}")
                elif text:
                    send_msg(chat_id, "This bot only relays messages. Reply to a person's message to answer them.")
        except Exception as e:
            print(f"[loop] {e}", flush=True)
            time.sleep(5)


if __name__ == "__main__":
    main()
