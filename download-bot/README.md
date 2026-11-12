# Telegram Relay Bot

A small private Python bot (long-polling, no webhook server needed) that
lets people reach the owner without ever seeing the owner's profile or
username. Text only — it downloads nothing.

- Anyone may DM the bot in private. Their text message is copied to the
  owner with a header naming the sender.
- The owner replies to that message and the bot sends the reply back as
  the bot's own message — never a forward, so no personal account is
  ever exposed.
- A visitor sending anything but text gets a short "text only" note.
- Flood guard: more than 5 messages in 30 seconds from one person are
  ignored. Blocked people are ignored silently.

## Commands

- `/start` — visitor welcome (for anyone) / owner help (for the owner).
- `/id` — the bot replies with your Telegram user ID (used for the
  allow-list below).
- `/block` and `/unblock` — owner only, reply to a person's relayed
  message to block or unblock them.

## Files

| File | What it is |
| --- | --- |
| `bot.py` | The program. |
| `telegram-download-bot.service` | Sanitised systemd template — replace the placeholders first. |

## Setup

1. Install Python 3 and `requests`.
2. Create a bot with [@BotFather](https://t.me/BotFather) and get its token.
3. Give the bot its token, either as the `BOT_TOKEN` environment variable
   or in a `token.txt` file next to `bot.py` (keep it `chmod 600` and
   **never commit it**).
4. Set `ALLOWED_IDS` to your own Telegram user ID (ask the bot for `/id`
   from your account, then put that ID in the service template's
   `ALLOWED_IDS` line) and `OWNER_ID` to the account the messages should
   reach.
5. Run `python3 bot.py`. For always-on operation, install the sanitised
   `.service` template with systemd after replacing its placeholders.

## History

This bot started as a link-download bot (send a link, get the file back).
The download part was removed on 2026-11-12 — it is now a relay only.
