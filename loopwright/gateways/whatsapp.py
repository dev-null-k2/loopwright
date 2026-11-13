"""WhatsApp gateway: the Cloud API. A webhook in, a REST call out.

Unlike Telegram and Discord, WhatsApp pushes messages to a URL you host, so it
needs to be reachable from the internet (a tunnel such as cloudflared or ngrok
pointed at this port is enough for personal use).

    export WHATSAPP_TOKEN=...            a Cloud API access token
    export WHATSAPP_PHONE_ID=...         the sending phone number's id
    export WHATSAPP_VERIFY_TOKEN=...     any string; you paste the same one into Meta's webhook form
    export WHATSAPP_APP_SECRET=...       used to check every delivery really came from Meta
    export WHATSAPP_ALLOWED=<your number in international format, digits only>
    loopwright whatsapp --port 8790

Two requests arrive at /webhook:
  GET   once, when you register the webhook: echo back `hub.challenge` if the verify token matches
  POST  for every message: the body is signed with HMAC-SHA256 using the app secret.
        A delivery with a missing or wrong signature is dropped. That check is
        the only thing standing between a public URL and your agent.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import threading
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

from loopwright.gateways.chat import ChatBridge
from loopwright.harness import Agent


def send_text(token: str, phone_id: str, to: str, text: str) -> None:
    version = os.environ.get("WHATSAPP_API_VERSION", "v21.0")
    request = urllib.request.Request(
        f"https://graph.facebook.com/{version}/{phone_id}/messages",
        data=json.dumps({"messaging_product": "whatsapp", "to": to, "type": "text", "text": {"body": text[:4000]}}).encode(),
        headers={"authorization": f"Bearer {token}", "content-type": "application/json"})
    urllib.request.urlopen(request, timeout=20).close()


def signed(secret: str, body: bytes, header: str) -> bool:
    expected = "sha256=" + hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
    return bool(secret) and hmac.compare_digest(expected, header or "")


def make_server(agent: Agent, port: int, *, token: str, phone_id: str, verify_token: str, app_secret: str,
                allowed: set[str], send=send_text) -> tuple[ThreadingHTTPServer, ChatBridge]:
    bridge = ChatBridge(agent, "whatsapp", lambda to, text: send(token, phone_id, to, text), allowed)

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args) -> None:
            pass

        def _reply(self, status: int, text: str = "") -> None:
            self.send_response(status)
            self.send_header("content-length", str(len(text.encode())))
            self.end_headers()
            self.wfile.write(text.encode())

        def do_GET(self) -> None:
            query = parse_qs(urlparse(self.path).query)
            ok = query.get("hub.mode") == ["subscribe"] and verify_token and query.get("hub.verify_token") == [verify_token]
            self._reply(200, query.get("hub.challenge", [""])[0]) if ok else self._reply(403)

        def do_POST(self) -> None:
            body = self.rfile.read(min(int(self.headers.get("content-length") or 0), 1_000_000))
            if not signed(app_secret, body, self.headers.get("X-Hub-Signature-256", "")):
                return self._reply(403)
            self._reply(200)                           # acknowledge at once; Meta retries slow webhooks
            try:
                payload = json.loads(body)
            except json.JSONDecodeError:
                return
            for entry in payload.get("entry", []):
                for change in entry.get("changes", []):
                    for message in change.get("value", {}).get("messages", []):
                        if message.get("type") == "text":
                            threading.Thread(target=bridge.handle, args=(message["from"], message["text"]["body"]), daemon=True).start()

    return ThreadingHTTPServer(("0.0.0.0", port), Handler), bridge


def run(agent: Agent, port: int = 8790) -> None:
    env = {k: os.environ.get(f"WHATSAPP_{k}", "") for k in ("TOKEN", "PHONE_ID", "VERIFY_TOKEN", "APP_SECRET")}
    missing = [f"WHATSAPP_{k}" for k, v in env.items() if not v]
    if missing:
        raise SystemExit("Set " + ", ".join(missing) + ". See docs/gateways.md.")
    allowed = set(os.environ.get("WHATSAPP_ALLOWED", "").replace(",", " ").split())
    server, bridge = make_server(agent, port, token=env["TOKEN"], phone_id=env["PHONE_ID"], verify_token=env["VERIFY_TOKEN"],
                                 app_secret=env["APP_SECRET"], allowed=allowed)
    bridge.start_background()
    print(f"WhatsApp webhook listening on port {port} at /webhook. Allowed numbers: {sorted(allowed) or 'none yet'}")
    server.serve_forever()
