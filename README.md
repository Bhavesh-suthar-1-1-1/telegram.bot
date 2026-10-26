# Telegram Bots — Private

> **Not for direct use.** These bots belong to Bhavesh and run on his own
> Telegram accounts. Do not run copies against his accounts, and do not
> message his bots without permission.
>
> **To ask permission, DM him on Telegram: https://t.me/tumara_padosi**

## What's here

| Folder | What it is |
| --- | --- |
| `download-bot/` | A private link-download bot: send it a link, get the file back. |
| `userbot/` | An auto-reply userbot: answers incoming personal chats with short AI-generated replies in the owner's style. |

## Clean copies

This repo contains **clean copies only**: code, docs, and templates. No
real credentials are committed anywhere — no bot token, no API keys, no
Telegram session files, no runtime state. Anywhere a value is needed, the
file has a placeholder (for example `YOUR_TELEGRAM_USER_ID` or
`PUT_AI_API_KEY_HERE`). To run your own copy, fill in your own credentials
and never commit the filled-in files.

Each folder has its own README with setup steps. The `.service` files are
sanitised templates: replace the `/path/to/...` placeholders with your own
paths before installing them.
