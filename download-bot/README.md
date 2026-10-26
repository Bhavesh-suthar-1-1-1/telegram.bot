# Telegram Link-Download Bot

A small private Python bot (long-polling, no webhook server needed). Send
it a link; it sends the file back.

- Direct file links are fetched and returned as-is.
- Video and page links go through yt-dlp at the best quality under the
  size limit — it covers all yt-dlp sites, not just YouTube.
- File sizes are shown so you can judge before saving.
- Hard limit: **50 MB per file**.
- Private: it only answers its owner's account. Anyone else gets a
  "private bot" reply.

## Commands

- Send any link → the bot fetches it and returns the file.
- `/id` → the bot replies with your Telegram user ID (used for the
  allow-list below).

## Files

| File | What it is |
| --- | --- |
| `bot.py` | The program. |
| `telegram-download-bot.service` | Sanitised systemd template — replace the placeholders first. |

## Setup

1. Install Python 3, `requests`, and `yt-dlp`.
2. Create a bot with [@BotFather](https://t.me/BotFather) and get its token.
3. Give the bot its token, either as the `BOT_TOKEN` environment variable
   or in a `token.txt` file next to `bot.py` (keep it `chmod 600` and
   **never commit it**).
4. Set the allow-list to your own Telegram user ID (ask the bot for `/id`
   from your account, then put that ID in the service template's
   `ALLOWED_IDS` line).
5. Run `python3 bot.py`. For always-on operation, install the sanitised
   `.service` template with systemd after replacing its placeholders.
