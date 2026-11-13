"""MESSAGING GATEWAYS: the same agent, reached from a chat app.

A gateway is an adapter. It turns "a message arrived on channel X from chat Y"
into `agent.run_turn(...)` and sends the reply back the way it came. Nothing
about the agent's behaviour lives here, which is why Telegram, Discord and
WhatsApp are each under a hundred lines on top of this file.

What a chat channel changes, and how the bridge handles it:

  who is talking   Anyone can find a bot. Each gateway has an allowlist and
                   refuses everyone else, telling them their id so the owner
                   can add it. An empty allowlist refuses everybody.
  sessions         One conversation per chat id. /new starts a fresh one.
  approvals        There is no button to click, so the bridge asks in words
                   ("reply yes or no") and waits for that chat's next message.
                   No answer in two minutes counts as no.
  seeing the work  /steps toggles a short list of what the agent did under
                   each reply. The full trace is always saved; replay it in
                   the cockpit.
  scheduled tasks  A task created from a chat is delivered back to that chat.
                   Each gateway runs the scheduler clock and checks for
                   results waiting to be sent to its channel.
  untrusted input  A message from an allowlisted user is the user. Everything
                   the agent reads while answering is still fenced and tainted
                   exactly as it is in the terminal.
"""

from __future__ import annotations

import threading
import time
from collections.abc import Callable
from typing import Any

from loopwright.config import Options, env_options
from loopwright.harness import Agent

HELP = ("I'm Loopwright. Send me a message and I'll answer.\n/new starts a fresh conversation\n"
        "/steps shows or hides what I did for each reply\n/help shows this")
CHANNELS = {
    "telegram": ("TELEGRAM_BOT_TOKEN", "TELEGRAM_ALLOWED", "long polling; no public URL needed"),
    "discord": ("DISCORD_BOT_TOKEN", "DISCORD_ALLOWED", "a WebSocket to Discord's gateway; no public URL needed"),
    "slack": ("SLACK_APP_TOKEN", "SLACK_ALLOWED", "Socket Mode (a WebSocket); no public URL needed"),
    "whatsapp": ("WHATSAPP_TOKEN", "WHATSAPP_ALLOWED", "a webhook; needs a public URL or a tunnel"),
}


class ChatBridge:
    def __init__(self, agent: Agent, channel: str, send: Callable[[str, str], None], allowed: set[str],
                 options: Options | None = None, approval_timeout: float = 120, limit: int = 3500):
        self.agent, self.channel, self._send, self.allowed = agent, channel, send, {str(a) for a in allowed if str(a).strip()}
        self.options = options or env_options()
        self.approval_timeout, self.limit = approval_timeout, limit
        self._stop = threading.Event()
        self._session: dict[str, str] = {}
        self._steps: set[str] = set()
        self._waiting: dict[str, dict[str, Any]] = {}       # chat id -> a pending approval
        self._lock = threading.Lock()

    def send(self, chat: str, text: str) -> None:
        for start in range(0, max(len(text), 1), self.limit):
            self._send(chat, text[start:start + self.limit])

    def handle(self, chat: str, text: str) -> None:
        """One incoming message. Call it from its own thread: a turn can wait on an approval."""
        chat, text = str(chat), text.strip()
        if chat not in self.allowed:
            self.send(chat, f"This assistant is private. Your id on {self.channel} is {chat}; its owner can allow it.")
            return
        with self._lock:
            pending = self._waiting.get(chat)
        if pending:                                           # this message is the answer to "approve?"
            pending["approved"] = text.lower() in ("yes", "y", "approve", "ok")
            pending["event"].set()
            return
        if text in ("/start", "/help"):
            return self.send(chat, HELP)
        if text == "/new":
            self._session[chat] = f"{self.channel}-{chat}-{int(time.time())}"
            return self.send(chat, "Started a fresh conversation.")
        if text == "/steps":
            self._steps ^= {chat}
            return self.send(chat, f"Step list {'on' if chat in self._steps else 'off'}.")
        session = self._session.setdefault(chat, f"{self.channel}-{chat}")
        result = self.agent.run_turn(text, session=session, options=self.options, channel=self.channel, origin=f"{self.channel}:{chat}",
                                     approver=lambda request: self._ask(chat, request))
        reply = result.reply
        if chat in self._steps and result.tool_log:
            reply += "\n\nWhat I did:\n" + "\n".join(
                f"- {t['name']}: {'refused' if t['blocked'] else 'ok' if t['ok'] else 'failed'}" for t in result.tool_log)
        self.send(chat, reply)

    def _ask(self, chat: str, request: dict[str, Any]) -> bool:
        slot = {"event": threading.Event(), "approved": False}
        with self._lock:
            self._waiting[chat] = slot
        self.send(chat, f"I'd like to run {request['tool']} with {request['args']}. Reply yes to allow it, anything else to decline.")
        slot["event"].wait(self.approval_timeout)
        with self._lock:
            self._waiting.pop(chat, None)
        return slot["approved"]

    def deliver_waiting(self) -> int:
        """Send every scheduled-task result that is waiting for this channel."""
        sent = 0
        for row in self.agent.store.take_deliveries(self.channel):
            chat = row["origin"].partition(":")[2]
            if chat in self.allowed:
                try:
                    self.send(chat, row["body"])
                    sent += 1
                except Exception:           # the chat app is unreachable; the result stays on the Automations page
                    continue
        return sent

    def start_background(self, every: float = 15) -> None:
        """The scheduler clock, plus delivery of finished tasks, for as long as the gateway runs."""
        from loopwright.scheduler import Scheduler
        scheduler = Scheduler(self.agent, self.options)

        def loop() -> None:
            while not self._stop.wait(every):
                try:
                    scheduler.tick()
                    self.deliver_waiting()
                except Exception:
                    continue
        threading.Thread(target=loop, daemon=True, name=f"loopwright-{self.channel}-clock").start()

    def stop(self) -> None:
        self._stop.set()
