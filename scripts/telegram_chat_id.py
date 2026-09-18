#!/usr/bin/env python3
"""Print the chat ids your Telegram bot has seen, so you can fill TELEGRAM_CHAT_ID.

1. Create a bot with @BotFather, put its token into .env as TELEGRAM_BOT_TOKEN.
2. Open the bot in Telegram and send it any message (for a group: add the bot, then write in the group).
3. Run:  python scripts/telegram_chat_id.py
"""
from __future__ import annotations

import json
import sys
import urllib.request

from common import load_env, require

env = load_env()
require(env, "TELEGRAM_BOT_TOKEN")
url = f"https://api.telegram.org/bot{env['TELEGRAM_BOT_TOKEN']}/getUpdates"
with urllib.request.urlopen(url, timeout=30) as r:
    data = json.loads(r.read())
seen = {}
for upd in data.get("result", []):
    msg = upd.get("message") or upd.get("channel_post") or upd.get("my_chat_member", {}) or {}
    chat = msg.get("chat")
    if chat:
        seen[chat["id"]] = f"{chat.get('type')} · {chat.get('title') or chat.get('username') or chat.get('first_name')}"
if not seen:
    sys.exit("No chats yet: send the bot a message first, then run again.")
for cid, label in seen.items():
    print(f"TELEGRAM_CHAT_ID={cid}    # {label}")
