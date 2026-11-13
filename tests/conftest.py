import datetime as dt

import pytest

from loopwright.config import Options
from loopwright.harness import Agent
from loopwright.models import local
from loopwright.models.base import ModelResponse, ToolCall, Usage
from loopwright.models.gateway import reset_breakers

NOW = dt.datetime(2026, 11, 12, 18, 0)        # a Thursday


@pytest.fixture(autouse=True)
def _isolated(tmp_path, monkeypatch):
    """Every test gets its own state folder and no real model credentials."""
    monkeypatch.setenv("LOOPWRIGHT_HOME", str(tmp_path / "home"))
    for key in ("ANTHROPIC_API_KEY", "OPENAI_API_KEY", "LOOPWRIGHT_PROVIDER", "TAVILY_API_KEY", "BRAVE_API_KEY", "LOOPWRIGHT_SEARCH",
                "OTEL_EXPORTER_OTLP_ENDPOINT", "TELEGRAM_BOT_TOKEN", "DISCORD_BOT_TOKEN", "SLACK_APP_TOKEN", "WHATSAPP_TOKEN"):
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setenv("LOOPWRIGHT_LOCAL_URL", "http://127.0.0.1:9")      # nothing listens here: no real local server leaks in
    local._found.update(at=0.0, value=None)
    local._text_only.clear()
    reset_breakers()


@pytest.fixture
def opts():
    return Options(provider="scripted", fast=True, approvals="approve", search="demo")


@pytest.fixture
def agent(tmp_path):
    return Agent(home=tmp_path / "agent", now=lambda: NOW)


class FakeModel:
    """A provider that plays back a fixed list of responses, so a test can make
    the 'model' do exactly the thing the harness has to handle."""
    name, model = "fake", "fake-1"

    def __init__(self, *steps):
        self.steps, self.seen = list(steps), []

    def complete(self, system, messages, tools, *, max_tokens=2048, purpose="loop"):
        self.seen.append({"system": system, "messages": [dict(m) for m in messages], "tools": [t["name"] for t in tools]})
        step = self.steps.pop(0) if self.steps else "done"
        if isinstance(step, Exception):
            raise step
        if isinstance(step, str):
            return ModelResponse(text=step, provider=self.name, model=self.model, usage=Usage(10, 5))
        calls = [ToolCall(f"c{len(self.seen)}_{i}", name, args) for i, (name, args) in enumerate(step)]
        return ModelResponse(tool_calls=calls, provider=self.name, model=self.model, stop_reason="tool_use", usage=Usage(10, 5))


@pytest.fixture
def fake_agent(tmp_path):
    def make(*steps):
        model = FakeModel(*steps)
        return Agent(home=tmp_path / "fake", now=lambda: NOW, providers=[model]), model
    return make


class WSServer:
    """A WebSocket server small enough to read, for testing the client and the
    gateways that sit on it. `script(send, recv)` plays the remote side."""

    def __init__(self, script):
        import base64
        import hashlib
        import socket
        import struct
        import threading
        self.error = None
        listener = socket.socket()
        listener.bind(("127.0.0.1", 0))
        listener.listen(1)
        self.url = f"ws://127.0.0.1:{listener.getsockname()[1]}/"

        def serve():
            conn, _ = listener.accept()
            conn.settimeout(10)
            request = b""
            while b"\r\n\r\n" not in request:
                request += conn.recv(4096)
            key = [line.split(b": ")[1] for line in request.split(b"\r\n") if line.lower().startswith(b"sec-websocket-key")][0]
            accept = base64.b64encode(hashlib.sha1(key + b"258EAFA5-E914-47DA-95CA-C5AB0DC85B11").digest())
            conn.sendall(b"HTTP/1.1 101 Switching Protocols\r\nUpgrade: websocket\r\nConnection: Upgrade\r\n"
                         b"Sec-WebSocket-Accept: " + accept + b"\r\n\r\n")

            def read(n):
                data = b""
                while len(data) < n:
                    data += conn.recv(n - len(data))
                return data

            def send(text, opcode=1):
                payload = text.encode() if isinstance(text, str) else text
                n = len(payload)
                head = bytes([0x80 | opcode]) + (bytes([n]) if n < 126 else bytes([126]) + struct.pack(">H", n) if n < 65536
                                                 else bytes([127]) + struct.pack(">Q", n))
                conn.sendall(head + payload)

            def recv():
                while True:
                    first, second = read(2)
                    n = second & 0x7F
                    n = struct.unpack(">H", read(2))[0] if n == 126 else struct.unpack(">Q", read(8))[0] if n == 127 else n
                    assert second & 0x80, "a client must mask its frames"
                    mask = read(4)
                    payload = bytes(b ^ mask[i % 4] for i, b in enumerate(read(n)))
                    if first & 0x0F == 0x8:
                        return None
                    if first & 0x0F in (0x9, 0xA):
                        continue
                    return payload.decode()

            try:
                script(send, recv)
            except Exception as exc:        # surfaced by the test through .error
                self.error = exc
            finally:
                conn.close()
                listener.close()

        self.thread = threading.Thread(target=serve, daemon=True)
        self.thread.start()


@pytest.fixture
def ws_server():
    return WSServer
