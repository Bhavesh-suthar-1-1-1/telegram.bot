# Telegram Auto-Reply Userbot

A small Python program (Telethon) that logs into **Bhavesh's own Telegram
account** and auto-replies to incoming **personal (1:1) chats** with short,
AI-generated replies in his style (persona in `persona.txt`).

It is a *userbot*, not a bot: it acts as the account itself. It **only ever
replies** — it never starts a conversation, and it ignores groups, channels,
bots, and service messages completely.

## Files

| File | What it is |
| --- | --- |
| `userbot.py` | The program. Run by the systemd service. |
| `login.py` | One-time interactive login (phone → code → session file). |
| `persona.txt` | The personality/style prompt given to the AI. Edit freely. |
| `config.json` | **You create this** from `config.example.json`. Holds `api_id`, `api_hash`, `ai_api_key`, `ai_provider`. Keep it `chmod 600`. |
| `config.example.json` | Template for `config.json` (plus optional pacing/cap settings). |
| `session.session` | Created by `login.py` (chmod 600). **Full account access — never share or copy it.** |
| `state.json` | Created at runtime: on/off state + daily reply counter. |
| `flags.jsonl` | Created at runtime: one JSON line per chat that hit a "serious" topic (money, meetups, secrets, commitments) and got a vague deflection instead of an AI answer. Review it to follow up personally. |
| `pylibs/` | Python dependencies (telethon, requests), installed with `pip --target`. |
| `telegram-userbot.service` | systemd unit. |
| `install-service.sh` | Copies the unit into systemd and starts the service (re-run after a VM replacement). |

## Setup (one time)

1. **Get Telegram API credentials:** log in at <https://my.telegram.org> →
   "API development tools" → create an app → note the `api_id` and `api_hash`.
2. **Get an AI key:** a Google Gemini API key (free tier) from Google AI
   Studio. (`ai_provider` can also be `groq` with a Groq key + model.)
3. **Create the config:**
   ```bash
   cd ~/workspace/telegram-userbot
   cp config.example.json config.json
   # edit config.json: fill api_id, api_hash, ai_api_key
   chmod 600 config.json
   ```
4. **Log in (owner present):**
   ```bash
   python3 login.py
   ```
   Enter the phone number in international format, then the code Telegram
   sends (and the 2FA cloud password, if the account has one). This creates
   `session.session`.
5. **Install the service:**
   ```bash
   ./install-service.sh
   ```

## Controls (from the account itself)

In Telegram, open **Saved Messages** (chat with yourself) and send:

- `.auto on` — enable auto-replies
- `.auto off` — disable (also cancels anything queued)
- `.auto status` — current state + replies sent today

**Auto-reply starts OFF.** Nothing is sent until `.auto on` is given.

## How it behaves

- Waits a human-like 20–90 s after the *last* message of a burst (rapid-fire
  messages are merged into one reply), shows "typing…" briefly, then replies.
- At most ~1 reply per chat per minute, and a daily cap (default 60).
- If Telegram returns a FloodWait, it pauses globally for exactly that long.
- If the owner types in a chat himself, the pending auto-reply for that chat
  is cancelled — he takes over.
- Logs (journald) contain sender ids and lengths only, never message text.

## ⚠️ Risks — read before enabling

- **Account ban risk is real.** Automating a personal account goes against
  Telegram's terms of use in spirit, and its anti-spam systems are aggressive.
  Accounts have been **limited or banned** for far less. Human pacing and caps
  reduce the risk; they do not remove it.
- **Never run this on an account that is already limited/flagged** (e.g. hit
  by @SpamBot restrictions) — automation on a flagged account can get it
  banned outright, losing the account entirely.
- A session login from a server looks like a new device in a new location;
  on a fresh session Telegram may restrict sensitive actions for ~24 h.
- People chatting with the account will not know an AI is replying. The
  persona is instructed never to agree to money, meetups, or commitments —
  anything serious gets a deflection and a flag in `flags.jsonl` for the
  owner to handle personally.
- `session.session` = full control of the account. If it ever leaks,
  terminate it immediately: Telegram app → Settings → Devices.

## Stopping it

- Quick: send `.auto off` in Saved Messages.
- Service: `systemctl stop telegram-userbot` (and `systemctl disable
  telegram-userbot` to keep it off across reboots).
- Nuclear: stop the service **and** terminate the session from the Telegram
  app (Settings → Devices), then delete `session.session`.
