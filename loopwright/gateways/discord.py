"""Discord gateway: a WebSocket in, REST out.

    1. discord.com/developers: create an application, add a Bot, copy its token,
       and switch on the "Message Content" intent.
    2. Invite the bot to a server, or just DM it.
    3. export DISCORD_BOT_TOKEN=...  ;  loopwright discord
    4. export DISCORD_ALLOWED=<your user id>   and start it again

Discord pushes events over a WebSocket ("the gateway"). The protocol is a
handful of opcodes: the server says Hello with a heartbeat interval, the
client Identifies, then heartbeats on a timer while Dispatch events arrive.
The bot answers direct messages, and in servers only when it is mentioned.
"""

from __future__ import annotations

import json
import os
import threading
import time
import urllib.request
from typing import Any

from loopwright.gateways.chat import ChatBridge
from loopwright.harness import Agent
from loopwright.ws import WebSocket, WebSocketClosed

GATEWAY = "wss://gateway.discord.gg/?v=10&encoding=json"
REST = "https://discord.com/api/v10"
INTENTS = (1 << 9) | (1 << 12) | (1 << 15)          # guild messages, direct messages, message content


def _rest(token: str, path: str, payload: dict[str, Any]) -> dict[str, Any]:
    request = urllib.request.Request(f"{REST}{path}", data=json.dumps(payload).encode(),
                                     headers={"authorization": f"Bot {token}", "content-type": "application/json",
                                              "user-agent": "DiscordBot (https://github.com/dev-null-k2/loopwright, 0.2)"})
    with urllib.request.urlopen(request, timeout=20) as response:
        return json.loads(response.read() or b"{}")


def post_message(token: str, channel: str, text: str) -> None:
    _rest(token, f"/channels/{channel}/messages", {"content": text[:2000]})


def dm_channel(token: str, user: str) -> str:
    return _rest(token, "/users/@me/channels", {"recipient_id": user})["id"]


def make_bridge(agent: Agent, token: str, allowed: set[str], post=post_message, open_dm=dm_channel) -> tuple[ChatBridge, dict[str, str]]:
    """Users are allowlisted by user id; a reply goes to the channel they last wrote in, or a DM."""
    channel_of: dict[str, str] = {}

    def send(user: str, text: str) -> None:
        if user not in channel_of:                # a scheduled result after a restart: open a direct message
            channel_of[user] = open_dm(token, user)
        post(token, channel_of[user], text)

    return ChatBridge(agent, "discord", send, allowed, limit=1900), channel_of


def session(bridge: ChatBridge, channel_of: dict[str, str], token: str, url: str = GATEWAY, max_events: int = 0) -> None:
    """One gateway connection, until it drops."""
    socket = WebSocket(url)
    hello = json.loads(socket.recv())
    interval, state = hello["d"]["heartbeat_interval"] / 1000, {"seq": None, "me": "", "alive": True}

    def heartbeat() -> None:
        while state["alive"]:
            time.sleep(interval)
            try:
                socket.send(json.dumps({"op": 1, "d": state["seq"]}))
            except OSError:
                return

    threading.Thread(target=heartbeat, daemon=True).start()
    socket.send(json.dumps({"op": 2, "d": {"token": token, "intents": INTENTS,
                                           "properties": {"os": "any", "browser": "loopwright", "device": "loopwright"}}}))
    seen = 0
    try:
        while True:
            event: dict[str, Any] = json.loads(socket.recv())
            state["seq"] = event.get("s") or state["seq"]
            if event.get("t") == "READY":
                state["me"] = event["d"]["user"]["id"]
            elif event.get("t") == "MESSAGE_CREATE":
                d = event["d"]
                mentioned = any(m.get("id") == state["me"] for m in d.get("mentions", []))
                if not d.get("author", {}).get("bot") and (not d.get("guild_id") or mentioned):
                    user = d["author"]["id"]
                    channel_of[user] = d["channel_id"]
                    text = d.get("content", "").replace(f"<@{state['me']}>", "").strip()
                    threading.Thread(target=bridge.handle, args=(user, text), daemon=True).start()
            elif event.get("op") in (7, 9):              # the server asks us to reconnect
                raise WebSocketClosed("reconnect requested")
            seen += 1
            if max_events and seen >= max_events:
                return
    finally:
        state["alive"] = False
        socket.close()


def run(agent: Agent) -> None:
    token = os.environ.get("DISCORD_BOT_TOKEN", "")
    if not token:
        raise SystemExit("Set DISCORD_BOT_TOKEN (discord.com/developers, Bot tab).")
    allowed = set(os.environ.get("DISCORD_ALLOWED", "").replace(",", " ").split())
    bridge, channel_of = make_bridge(agent, token, allowed)
    bridge.start_background()
    print(f"Discord gateway running. Allowed user ids: {sorted(allowed) or 'none yet: message the bot to see your id'}")
    while True:
        try:
            session(bridge, channel_of, token)
        except (WebSocketClosed, OSError, ValueError) as exc:
            print(f"connection lost ({exc}); reconnecting in 5s")
            time.sleep(5)
