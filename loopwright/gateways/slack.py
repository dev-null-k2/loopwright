"""Slack gateway: Socket Mode. A WebSocket in, the Web API out. No public URL.

    1. api.slack.com/apps: create an app. Turn on Socket Mode and create an
       app-level token (xapp-...) with the `connections:write` scope.
    2. OAuth scopes for the bot: chat:write, im:history, app_mentions:read.
       Subscribe to the bot events `message.im` and `app_mention`. Install the
       app to your workspace and copy the bot token (xoxb-...).
    3. export SLACK_APP_TOKEN=xapp-...  SLACK_BOT_TOKEN=xoxb-...
       loopwright slack              (it prints the id of whoever messages it)
    4. export SLACK_ALLOWED=<your member id>   and start it again

Socket Mode: ask Slack for a one-time WebSocket URL, connect, and Slack pushes
each event in an envelope. Every envelope must be acknowledged within three
seconds by sending its id back, or Slack sends it again; the agent's answer
goes out separately, through chat.postMessage, whenever it is ready.
"""

from __future__ import annotations

import json
import os
import re
import threading
import time
import urllib.request
from typing import Any

from loopwright.gateways.chat import ChatBridge
from loopwright.harness import Agent
from loopwright.ws import WebSocket, WebSocketClosed

API = "https://slack.com/api/"


def api(token: str, method: str, payload: dict[str, Any] | None = None) -> dict[str, Any]:
    request = urllib.request.Request(API + method, data=json.dumps(payload or {}).encode(),
                                     headers={"authorization": f"Bearer {token}", "content-type": "application/json; charset=utf-8"})
    with urllib.request.urlopen(request, timeout=20) as response:
        return json.loads(response.read())


def make_bridge(agent: Agent, bot_token: str, allowed: set[str], call=api) -> tuple[ChatBridge, dict[str, str]]:
    channel_of: dict[str, str] = {}
    # a member id works as a channel for chat.postMessage: it opens the direct message
    send = lambda user, text: call(bot_token, "chat.postMessage", {"channel": channel_of.get(user, user), "text": text})  # noqa: E731
    return ChatBridge(agent, "slack", send, allowed), channel_of


def session(bridge: ChatBridge, channel_of: dict[str, str], url: str, max_events: int = 0) -> None:
    """One Socket Mode connection, until Slack closes it (it does so on purpose, roughly hourly)."""
    socket = WebSocket(url)
    seen = 0
    try:
        while True:
            envelope = json.loads(socket.recv())
            if envelope.get("envelope_id"):
                socket.send(json.dumps({"envelope_id": envelope["envelope_id"]}))      # the acknowledgement
            if envelope.get("type") == "disconnect":
                raise WebSocketClosed("Slack asked for a reconnect")
            event = (envelope.get("payload") or {}).get("event") or {}
            direct = event.get("type") == "message" and event.get("channel_type") == "im" and not event.get("subtype")
            if (direct or event.get("type") == "app_mention") and not event.get("bot_id") and event.get("user"):
                channel_of[event["user"]] = event["channel"]
                text = re.sub(r"<@[A-Z0-9]+>", "", event.get("text", "")).strip()
                threading.Thread(target=bridge.handle, args=(event["user"], text), daemon=True).start()
            seen += 1
            if max_events and seen >= max_events:
                return
    finally:
        socket.close()


def run(agent: Agent) -> None:
    app_token, bot_token = os.environ.get("SLACK_APP_TOKEN", ""), os.environ.get("SLACK_BOT_TOKEN", "")
    if not app_token or not bot_token:
        raise SystemExit("Set SLACK_APP_TOKEN (xapp-...) and SLACK_BOT_TOKEN (xoxb-...). See docs/gateways.md.")
    allowed = set(os.environ.get("SLACK_ALLOWED", "").replace(",", " ").split())
    bridge, channel_of = make_bridge(agent, bot_token, allowed)
    bridge.start_background()
    print(f"Slack gateway running. Allowed member ids: {sorted(allowed) or 'none yet: message the app to see your id'}")
    while True:
        try:
            opened = api(app_token, "apps.connections.open")
            if not opened.get("ok"):
                raise SystemExit(f"Slack refused the app token: {opened.get('error')}")
            session(bridge, channel_of, opened["url"])
        except (WebSocketClosed, OSError, ValueError) as exc:
            print(f"connection lost ({exc}); reconnecting in 5s")
            time.sleep(5)
