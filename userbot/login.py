#!/usr/bin/env python3
"""One-time interactive login for the auto-reply userbot.

Run this WITH THE ACCOUNT OWNER PRESENT:

    cd ~/workspace/telegram-userbot && python3 login.py

It reads api_id / api_hash from config.json, asks for the phone number,
Telegram sends a login code (to the Telegram app on the phone, or SMS),
the owner types the code here, and — if the account has a 2FA cloud
password — Telethon asks for that too. On success it saves the session
to session.session (permissions set to 600).

The session file is effectively full access to the account. Never copy
it anywhere, never share it. To revoke it later: Telegram app →
Settings → Devices → terminate the session.
"""
import json
import os
import sys

BASE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(BASE, "pylibs"))

from telethon import TelegramClient

CONFIG_PATH = os.path.join(BASE, "config.json")
SESSION_PATH = os.path.join(BASE, "session")


def main():
    if not os.path.exists(CONFIG_PATH):
        sys.exit(
            "config.json is missing. Copy config.example.json to config.json, "
            "fill in api_id + api_hash from https://my.telegram.org "
            "(and ai_api_key for the userbot), then chmod 600 config.json."
        )
    with open(CONFIG_PATH) as f:
        cfg = json.load(f)
    api_id, api_hash = cfg.get("api_id"), cfg.get("api_hash")
    if not api_id or not api_hash:
        sys.exit("config.json needs api_id and api_hash (from https://my.telegram.org).")

    client = TelegramClient(SESSION_PATH, int(api_id), api_hash)
    # start() interactively prompts for: phone number → login code → 2FA password (if set).
    client.start()
    me = client.get_me()
    session_file = SESSION_PATH + ".session"
    if os.path.exists(session_file):
        os.chmod(session_file, 0o600)
    print()
    print(f"Logged in as: {me.first_name} (id {me.id})")
    print(f"Session saved to {session_file} (chmod 600). Keep it secret.")
    print("Next: send '.auto on' to your Saved Messages to enable auto-reply "
          "(or start the service and toggle it there).")
    client.disconnect()


if __name__ == "__main__":
    main()
