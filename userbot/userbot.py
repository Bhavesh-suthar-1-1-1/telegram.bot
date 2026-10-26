#!/usr/bin/env python3
"""Telegram Auto-Reply Userbot (for Bhavesh).

Logs into Bhavesh's OWN Telegram account (session created by login.py) and
auto-replies to incoming PERSONAL (1:1) chats with AI-generated replies.

STRICT SCOPE — hard rules, do not loosen:
- Replies ONLY. It never starts a conversation with anyone.
- Private 1:1 chats with real users only. Never groups, channels, bots,
  service messages, or Telegram's own notification account.
- Owner controls it from Saved Messages: ".auto on" / ".auto off" / ".auto status".
- Default state is OFF. Nothing is sent until the owner enables it.
- Human pacing: waits 20-90 s after the last message of a burst, shows a
  typing action, obeys per-chat and daily caps, and backs off on FloodWait.
- Catch-up: on startup and on ".auto on" it scans recent private dialogs
  for bursts that arrived while it was down (Telethon does not replay
  missed messages as events) and answers them through the same pipeline.
- Serious topics (money, meetups, OTP/secrets, commitments) never get an AI
  answer: a vague deflection is sent and the chat is logged to flags.jsonl.

Config: config.json next to this file (api_id, api_hash, ai_api_key, ai_provider).
Persona: persona.txt next to this file.
Logs go to stdout (journald under systemd) — sender id + length only, never
message contents.
"""
import asyncio
import json
import os
import random
import re
import sys
import time
from collections import defaultdict, deque
from datetime import date, datetime, timezone

BASE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(BASE, "pylibs"))

import requests
from telethon import TelegramClient, events
from telethon.errors import FloodWaitError
from telethon.tl.types import User

CONFIG_PATH = os.path.join(BASE, "config.json")
PERSONA_PATH = os.path.join(BASE, "persona.txt")
STATE_PATH = os.path.join(BASE, "state.json")
FLAGS_PATH = os.path.join(BASE, "flags.jsonl")
SESSION_PATH = os.path.join(BASE, "session")  # Telethon appends ".session"

TELEGRAM_SERVICE_ID = 777000  # Telegram's own notification account — never reply

# ---------------------------------------------------------------------------
# Small helpers
# ---------------------------------------------------------------------------


def log(msg):
    print(f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] {msg}", flush=True)


def load_config():
    if not os.path.exists(CONFIG_PATH):
        sys.exit(
            "config.json is missing. Copy config.example.json to config.json, "
            "fill in api_id / api_hash (from https://my.telegram.org) and "
            "ai_api_key, then: chmod 600 config.json  — see README.md."
        )
    try:
        with open(CONFIG_PATH) as f:
            cfg = json.load(f)
    except Exception as e:
        sys.exit(f"config.json is not valid JSON: {e}")
    missing = [k for k in ("api_id", "api_hash", "ai_api_key") if not cfg.get(k)]
    if missing:
        sys.exit(f"config.json is missing required keys: {', '.join(missing)}")
    cfg.setdefault("ai_provider", "gemini")
    cfg.setdefault("ai_model", "gemini-2.0-flash")
    cfg.setdefault("groq_model", "openai/gpt-oss-120b")
    # Provider fallback chain, tried in order (see ai_reply). Defaults to
    # just the legacy single provider; config.json sets ["groq", "gemini"].
    cfg.setdefault("ai_chain", [cfg["ai_provider"]])
    cfg.setdefault("reply_delay_min", 20)
    cfg.setdefault("reply_delay_max", 90)
    cfg.setdefault("per_chat_min_interval", 60)
    cfg.setdefault("daily_cap", 60)
    return cfg


def load_persona():
    if not os.path.exists(PERSONA_PATH):
        sys.exit("persona.txt is missing — it holds the reply personality. See README.md.")
    with open(PERSONA_PATH) as f:
        return f.read().strip()


def load_state():
    try:
        with open(STATE_PATH) as f:
            st = json.load(f)
    except Exception:
        st = {}
    st.setdefault("auto", False)  # default OFF — owner enables via ".auto on"
    st.setdefault("day", str(date.today()))
    st.setdefault("count", 0)
    # chat_id (str) -> highest incoming message id answered via catch-up,
    # so the startup scan never answers the same burst twice.
    st.setdefault("catchup_handled", {})
    return st


def save_state(st):
    tmp = STATE_PATH + ".tmp"
    with open(tmp, "w") as f:
        json.dump(st, f)
    os.replace(tmp, STATE_PATH)


def flag_chat(chat_id, reason, length):
    """Append a 'serious topic' record for the owner's later review."""
    rec = {
        "ts": datetime.now(timezone.utc).isoformat(),
        "chat_id": chat_id,
        "reason": reason,
        "len": length,
    }
    with open(FLAGS_PATH, "a") as f:
        f.write(json.dumps(rec) + "\n")
    log(f"flagged chat={chat_id} reason={reason} len={length}")


# ---------------------------------------------------------------------------
# Serious-topic guard: never let the AI improvise on these.
# ---------------------------------------------------------------------------

SERIOUS_PATTERNS = [
    ("money", re.compile(
        r"(₹|\brs\.?\b|\binr\b|rupees?|paisa|paise|borrow|lend|loan|udhaar|udhar|"
        r"paytm|gpay|phonepe|\bupi\b|bank|account\s*number|ifsc|emi|salary)", re.I)),
    ("meetup", re.compile(
        r"(meet\s?(me|up)?|milne|milna|ghar\s*aa|address|location\s*bhej|"
        r"kab\s*aa\s*rah|pick\s*up|drop\s*kar|aaja\s|aa\s*jao)", re.I)),
    ("secret", re.compile(
        r"(\botp\b|password|passcode|\bpin\b|cvv|card\s*number|aadhaar|pan\s*card)", re.I)),
    ("commitment", re.compile(
        r"(promise|pakka\s*wala|shaadi|marriage|rishta|girlfriend|boyfriend|"
        r"love\s*you|commitment)", re.I)),
]

VAGUE_REPLIES = [
    "abhi thoda busy hu, baad me batata hu",
    "hmm, dekh ke batata hu thodi der me",
    "ispe sochna padega, batata hu",
    "abhi thoda busy hu, free hoke batata hu",
]


def serious_reason(text):
    for name, pat in SERIOUS_PATTERNS:
        if pat.search(text):
            return name
    return None


# ---------------------------------------------------------------------------
# AI brain (called in a thread executor — requests is blocking)
# ---------------------------------------------------------------------------


def _merged_turns(history):
    """Merge consecutive same-role entries (chat APIs want alternating turns)."""
    turns = []
    for h in history:
        if turns and turns[-1]["role"] == h["role"]:
            turns[-1]["text"] += "\n" + h["text"]
        else:
            turns.append({"role": h["role"], "text": h["text"]})
    while turns and turns[0]["role"] == "me":
        turns.pop(0)
    return turns


def gemini_reply(cfg, persona, history):
    turns = _merged_turns(history)
    contents = [
        {
            "role": "user" if t["role"] == "them" else "model",
            "parts": [{"text": t["text"]}],
        }
        for t in turns
    ]
    url = (
        "https://generativelanguage.googleapis.com/v1beta/models/"
        f"{cfg['ai_model']}:generateContent"
    )
    payload = {
        "system_instruction": {"parts": [{"text": persona}]},
        "contents": contents,
        "generationConfig": {"maxOutputTokens": 300, "temperature": 0.9},
    }
    # Key goes in a header, NOT the URL — error messages quote URLs, and a
    # key must never land in the logs.
    r = requests.post(url, json=payload, timeout=30,
                      headers={"x-goog-api-key": cfg["ai_api_key"]})
    r.raise_for_status()
    data = r.json()
    return data["candidates"][0]["content"]["parts"][0]["text"].strip()


def groq_reply(cfg, persona, history):
    turns = _merged_turns(history)
    messages = [{"role": "system", "content": persona}]
    for t in turns:
        messages.append({
            "role": "user" if t["role"] == "them" else "assistant",
            "content": t["text"],
        })
    r = requests.post(
        "https://api.groq.com/openai/v1/chat/completions",
        headers={"Authorization": f"Bearer {cfg.get('groq_api_key') or cfg['ai_api_key']}"},
        json={
            "model": cfg.get("groq_model") or "openai/gpt-oss-120b",
            "messages": messages,
            "max_tokens": 300,
            "temperature": 0.9,
        },
        timeout=30,
    )
    r.raise_for_status()
    return r.json()["choices"][0]["message"]["content"].strip()


_PROVIDERS = {"groq": groq_reply, "gemini": gemini_reply}
_PROVIDER_KEYS = {"groq": "groq_api_key", "gemini": "ai_api_key"}


def _err_detail(e):
    """Short, log-safe error summary — never a URL or anything with a key."""
    resp = getattr(e, "response", None)
    if resp is not None:
        return f"HTTP {resp.status_code}"
    return type(e).__name__


def ai_reply(cfg, persona, history):
    """Try each provider in cfg['ai_chain'] in order.

    Returns (reply_text, provider_name). Raises the last provider's error
    only when every provider in the chain has failed, so the caller keeps
    its existing stay-silent behavior.
    """
    chain = cfg.get("ai_chain") or [cfg.get("ai_provider", "gemini")]
    last_err = None
    for name in chain:
        fn = _PROVIDERS.get(name)
        if fn is None:
            log(f"AI chain: unknown provider {name!r} — skipped")
            continue
        key_field = _PROVIDER_KEYS.get(name)
        if key_field and not cfg.get(key_field):
            log(f"AI chain: {name} has no {key_field} in config — skipped")
            continue
        try:
            text = fn(cfg, persona, history)
        except Exception as e:
            log(f"AI chain: {name} failed ({_err_detail(e)}) — trying next")
            last_err = e
            continue
        if text:
            return text, name
        log(f"AI chain: {name} returned an empty reply — trying next")
        last_err = RuntimeError(f"{name}: empty reply")
    if last_err is not None:
        raise last_err
    raise RuntimeError("AI chain: no usable provider configured")


# ---------------------------------------------------------------------------
# Work-talk output guard: the persona says "don't talk about work", but a
# chat history full of older work-talk replies keeps teaching the model to
# slip it back in — so enforce it here, on the generated text itself.
# ---------------------------------------------------------------------------

WORK_TALK_RE = re.compile(
    r"\b(sites?|tiles?|tiling|patthar|pathar|stones?)\b", re.I)

# If the person is actually ASKING about work / where he is, a work mention
# in the reply is natural and allowed.
WORK_ASK_RE = re.compile(
    r"(kahan?\s+(pe\s+)?ho|kidhar\s+ho|kya\s+kar\s+rah|kaam\s+kaisa|"
    r"\bkaam\b|site\s+pe|\boffice\b|\bjob\b|\bduty\b)", re.I)

WORK_REGEN_INSTRUCTION = (
    "IMPORTANT: Rewrite without ANY mention of site, tiles, stone, patthar "
    "or work — keep it casual and on-topic."
)

NEUTRAL_FALLBACKS = ["acha 😅", "hnn sahi hai", "ok ok"]


def guarded_ai_reply(cfg, persona, history, incoming_text, chat_id):
    """ai_reply + the work-talk guard. Returns (reply_text, via).

    If the generated reply mentions work while the incoming burst never
    asked about work, regenerate ONCE with a no-work rewrite instruction;
    if that still trips the guard, return a short neutral fallback.
    A failure of the FIRST ai_reply call propagates, exactly as before,
    so the caller keeps its stay-silent behavior.
    """
    reply, via = ai_reply(cfg, persona, history)
    if not reply or not WORK_TALK_RE.search(reply):
        return reply, via
    if WORK_ASK_RE.search(incoming_text or ""):
        log(f"guard: work-talk allowed chat={chat_id} (asked about work)")
        return reply, via
    log(f"guard: work-talk blocked chat={chat_id}, regenerated")
    try:
        retry, retry_via = ai_reply(
            cfg, persona + "\n\n" + WORK_REGEN_INSTRUCTION, history)
    except Exception:
        retry, retry_via = "", via
    if retry and not WORK_TALK_RE.search(retry):
        return retry, retry_via
    log(f"guard: fallback used chat={chat_id}")
    return random.choice(NEUTRAL_FALLBACKS), "guard"


# ---------------------------------------------------------------------------
# Runtime state
# ---------------------------------------------------------------------------

history = defaultdict(lambda: deque(maxlen=14))  # chat_id -> [{role, text}]
pending = defaultdict(list)                      # chat_id -> [texts awaiting reply]
pending_tasks = {}                               # chat_id -> asyncio.Task
last_reply_at = {}                               # chat_id -> epoch seconds
flood_until = 0.0                                # global FloodWait pause


async def amain():
    cfg = load_config()
    persona = load_persona()
    state = load_state()

    if not os.path.exists(SESSION_PATH + ".session"):
        sys.exit(
            "No session file (session.session). Run the one-time login first, "
            "with the account owner present:  python3 login.py"
        )

    client = TelegramClient(SESSION_PATH, int(cfg["api_id"]), cfg["api_hash"])
    await client.connect()
    if not await client.is_user_authorized():
        sys.exit("Session is not authorized. Run login.py again with the owner present.")

    me = await client.get_me()
    owner_id = me.id
    log(f"userbot up: account id={owner_id} auto={'ON' if state['auto'] else 'OFF'} "
        f"chain={'>'.join(cfg['ai_chain'])}")

    loop = asyncio.get_event_loop()

    def roll_day():
        today = str(date.today())
        if state["day"] != today:
            state["day"] = today
            state["count"] = 0
            save_state(state)

    # -- reply pipeline ------------------------------------------------------

    async def do_reply(chat_id):
        """Run one reply cycle for a chat. True only if a reply was sent."""
        global flood_until
        if not state["auto"]:
            pending.pop(chat_id, None)
            return False
        now = time.time()
        if now < flood_until:
            log(f"skip chat={chat_id}: FloodWait pause "
                f"({int(flood_until - now)}s left)")
            pending.pop(chat_id, None)
            return False
        wait = cfg["per_chat_min_interval"] - (now - last_reply_at.get(chat_id, 0))
        if wait > 0:
            pending_tasks[chat_id] = asyncio.ensure_future(
                delayed_reply(chat_id, wait + random.uniform(1, 4)))
            return False
        roll_day()
        if state["count"] >= cfg["daily_cap"]:
            log(f"skip chat={chat_id}: daily cap {cfg['daily_cap']} reached")
            pending.pop(chat_id, None)
            return False
        msgs = pending.pop(chat_id, [])
        if not msgs:
            return False

        joined = "\n".join(msgs)
        reason = serious_reason(joined)
        via = "guard"
        if reason:
            reply = random.choice(VAGUE_REPLIES)
            flag_chat(chat_id, reason, len(joined))
        else:
            try:
                reply, via = await loop.run_in_executor(
                    None, guarded_ai_reply, cfg, persona,
                    list(history[chat_id]), joined, chat_id)
            except Exception as e:
                log(f"AI error chat={chat_id}: {e!r} — staying silent")
                return False
            if not reply:
                log(f"AI empty reply chat={chat_id} — staying silent")
                return False
            reply = reply.strip().strip('"')[:500]

        try:
            await client.send_read_acknowledge(chat_id)
            async with client.action(chat_id, "typing"):
                await asyncio.sleep(min(2.0 + len(reply) / 14.0, 8.0))
            await client.send_message(chat_id, reply)
        except FloodWaitError as e:
            flood_until = time.time() + e.seconds + 5
            log(f"FloodWait {e.seconds}s — global pause until backoff ends")
            return False
        except Exception as e:
            log(f"send error chat={chat_id}: {e!r}")
            return False

        last_reply_at[chat_id] = time.time()
        state["count"] += 1
        save_state(state)
        history[chat_id].append({"role": "me", "text": reply})
        log(f"replied chat={chat_id} len={len(reply)} "
            f"(today {state['count']}/{cfg['daily_cap']}) via={via}")
        return True

    async def delayed_reply(chat_id, delay):
        try:
            await asyncio.sleep(delay)
            await do_reply(chat_id)
        except asyncio.CancelledError:
            raise
        except Exception as e:
            log(f"reply task error chat={chat_id}: {e!r}")

    def schedule_reply(chat_id):
        task = pending_tasks.get(chat_id)
        if task and not task.done():
            task.cancel()
        delay = random.uniform(cfg["reply_delay_min"], cfg["reply_delay_max"])
        pending_tasks[chat_id] = asyncio.ensure_future(delayed_reply(chat_id, delay))

    # -- catch-up: answer bursts that arrived while the bot was down --------
    # Telethon does not replay missed messages as NewMessage events, so scan
    # recent dialogs on startup and on ".auto on" for unanswered bursts and
    # feed them through the same do_reply pipeline as live messages.

    async def catchup_reply(chat_id, newest_in_id, delay):
        try:
            await asyncio.sleep(delay)
            if await do_reply(chat_id):
                handled = state.setdefault("catchup_handled", {})
                if newest_in_id > int(handled.get(str(chat_id), 0)):
                    handled[str(chat_id)] = newest_in_id
                    save_state(state)
        except asyncio.CancelledError:
            raise
        except Exception as e:
            log(f"catchup reply error chat={chat_id}: {e!r}")

    async def catchup():
        if not state["auto"]:
            return
        handled = state.setdefault("catchup_handled", {})
        now_utc = datetime.now(timezone.utc)
        try:
            dialogs = await client.get_dialogs(limit=30)
        except Exception as e:
            log(f"catchup: dialog scan failed {e!r}")
            return
        queued = 0
        for dialog in dialogs:
            if not state["auto"]:
                break
            entity = dialog.entity
            # Same eligibility as the live handler: a real human user in a
            # private 1:1 chat — no groups, channels, bots, or self.
            if not isinstance(entity, User) or entity.bot or entity.is_self:
                continue
            if entity.id == TELEGRAM_SERVICE_ID:
                continue
            chat_id = dialog.id
            task = pending_tasks.get(chat_id)
            if task and not task.done():
                continue  # the live pipeline is already on this chat
            try:
                msgs = await client.get_messages(entity, limit=15)
            except Exception as e:
                log(f"catchup chat={chat_id}: fetch failed {e!r}")
                continue
            if not msgs:
                continue
            newest = msgs[0]  # get_messages returns newest first
            if newest.out or newest.action is not None:
                continue  # we (or a service notice) had the last word
            age = (now_utc - newest.date).total_seconds()
            if age < 0 or age > 24 * 3600:
                continue  # stale — don't resurrect day-old chats
            if newest.id <= int(handled.get(str(chat_id), 0)):
                continue  # an earlier catch-up already answered this burst
            # Trailing run of consecutive incoming messages = pending burst.
            burst = []
            for m in msgs:
                if m.out or m.action is not None:
                    break
                burst.append(m)
            burst.reverse()  # chronological order
            # Rebuild the AI's context window from the fetched history.
            history[chat_id].clear()
            for m in reversed(msgs):
                history[chat_id].append({
                    "role": "me" if m.out else "them",
                    "text": m.raw_text or "[media]",
                })
            texts = [m.raw_text for m in burst if m.raw_text]
            if not texts:
                # Media-only burst — the live handler never replies to those;
                # mark it handled so it isn't rescanned forever.
                handled[str(chat_id)] = newest.id
                save_state(state)
                continue
            pending[chat_id] = texts
            log(f"catchup chat={chat_id} pending={len(burst)}")
            queued += 1
            delay = queued * random.uniform(4.0, 7.0)  # stagger the replies
            asyncio.ensure_future(catchup_reply(chat_id, newest.id, delay))
        if queued:
            log(f"catchup: {queued} chat(s) queued")

    async def startup_catchup():
        await asyncio.sleep(5)  # let the connection settle first
        await catchup()

    # -- handlers ------------------------------------------------------------

    @client.on(events.NewMessage(incoming=True))
    async def on_incoming(event):
        # STRICT scope: private 1:1 with a real human user only.
        if not event.is_private or event.is_group or event.is_channel:
            return
        if event.message.action is not None:
            return  # service message (join/leave/pin notices etc.)
        sender = await event.get_sender()
        if not isinstance(sender, User) or sender.bot or sender.is_self:
            return
        if sender.id == TELEGRAM_SERVICE_ID:
            return
        chat_id = event.chat_id
        text = event.raw_text or ""
        if not text:
            history[chat_id].append({"role": "them", "text": "[media]"})
            return
        history[chat_id].append({"role": "them", "text": text})
        pending[chat_id].append(text)
        log(f"msg chat={chat_id} len={len(text)} "
            f"(burst {len(pending[chat_id])})")
        if state["auto"]:
            schedule_reply(chat_id)

    @client.on(events.NewMessage(outgoing=True))
    async def on_outgoing(event):
        chat_id = event.chat_id
        text = (event.raw_text or "").strip()
        if chat_id == owner_id:
            # Saved Messages = the owner's control panel.
            cmd = text.lower()
            if cmd == ".auto on":
                state["auto"] = True
                save_state(state)
                await event.reply("Auto-reply: ON")
                asyncio.ensure_future(catchup())
            elif cmd == ".auto off":
                state["auto"] = False
                save_state(state)
                for t in pending_tasks.values():
                    if not t.done():
                        t.cancel()
                pending.clear()
                await event.reply("Auto-reply: OFF")
            elif cmd == ".auto status":
                roll_day()
                await event.reply(
                    f"Auto-reply: {'ON' if state['auto'] else 'OFF'} · "
                    f"replies today: {state['count']}/{cfg['daily_cap']}")
            return
        if event.is_private and text:
            # The owner is typing in this chat himself — he takes over,
            # so cancel any pending auto-reply and keep context fresh.
            task = pending_tasks.get(chat_id)
            if task and not task.done():
                task.cancel()
            pending.pop(chat_id, None)
            history[chat_id].append({"role": "me", "text": text})

    if state["auto"]:
        asyncio.ensure_future(startup_catchup())

    await client.run_until_disconnected()


if __name__ == "__main__":
    asyncio.run(amain())
