"""Telegram gateway: long polling against the Bot API. No public URL needed.

    1. Talk to @BotFather, create a bot, copy its token.
    2. export TELEGRAM_BOT_TOKEN=...
    3. loopwright telegram           (it prints the id of whoever messages it)
    4. export TELEGRAM_ALLOWED=<your id>   and start it again

Long polling: ask "any updates after number N?" and let the request hang for
up to 25 seconds. It is the simplest way to receive pushes without a server.
"""

from __future__ import annotations

import json
import os
import threading
import time
import urllib.error
import urllib.request
from typing import Any

from loopwright.gateways.chat import ChatBridge
from loopwright.harness import Agent

API = "https://api.telegram.org/bot{token}/{method}"


def call(token: str, method: str, payload: dict[str, Any], timeout: float = 40) -> Any:
    request = urllib.request.Request(API.format(token=token, method=method), data=json.dumps(payload).encode(),
                                     headers={"content-type": "application/json"})
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return json.loads(response.read().decode()).get("result")


def run(agent: Agent, token: str = "", allowed: set[str] | None = None, api=call, once: bool = False) -> ChatBridge:
    token = token or os.environ.get("TELEGRAM_BOT_TOKEN", "")
    if not token:
        raise SystemExit("Set TELEGRAM_BOT_TOKEN (create a bot with @BotFather).")
    allowed = allowed if allowed is not None else set(os.environ.get("TELEGRAM_ALLOWED", "").replace(",", " ").split())
    bridge = ChatBridge(agent, "telegram", lambda chat, text: api(token, "sendMessage", {"chat_id": chat, "text": text}), allowed)
    if not once:
        bridge.start_background()
    print(f"Telegram gateway running. Allowed chat ids: {sorted(allowed) or 'none yet: message the bot to see your id'}")
    offset = 0
    while True:
        try:
            updates = api(token, "getUpdates", {"offset": offset, "timeout": 25}) or []
        except (urllib.error.URLError, TimeoutError, OSError, ValueError):
            time.sleep(3)                         # network blip: wait and poll again
            updates = []
        for update in updates:
            offset = update["update_id"] + 1
            message = update.get("message") or {}
            if message.get("text"):
                threading.Thread(target=bridge.handle, args=(str(message["chat"]["id"]), message["text"]), daemon=True).start()
        if once:
            return bridge
