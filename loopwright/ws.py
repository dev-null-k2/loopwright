"""A WebSocket client in the standard library, about ninety lines.

Two parts of Loopwright need a long-lived two-way connection: the Discord
gateway (Discord pushes messages over a WebSocket) and the browser agent
(Chrome's DevTools Protocol is JSON over a WebSocket). Neither justifies a
dependency, and the protocol is small enough to read:

  1. an HTTP request that asks to "upgrade" the connection
  2. after that, frames: a header with an opcode and a length, then the
     payload. Frames from a client must be XOR-masked with a random key.

This handles text frames, fragmentation, ping/pong and close. It does not do
compression or extensions, and it is not meant for anything but these uses.
"""

from __future__ import annotations

import base64
import os
import socket
import ssl
import struct
import threading
from urllib.parse import urlparse


class WebSocketClosed(ConnectionError):
    pass


class WebSocket:
    def __init__(self, url: str, timeout: float = 30, headers: dict[str, str] | None = None):
        parsed = urlparse(url)
        secure = parsed.scheme == "wss"
        port = parsed.port or (443 if secure else 80)
        raw = socket.create_connection((parsed.hostname, port), timeout=timeout)
        self._sock = ssl.create_default_context().wrap_socket(raw, server_hostname=parsed.hostname) if secure else raw
        self._lock = threading.Lock()
        self._buffer = b""
        key = base64.b64encode(os.urandom(16)).decode()
        path = (parsed.path or "/") + (f"?{parsed.query}" if parsed.query else "")
        lines = [f"GET {path} HTTP/1.1", f"Host: {parsed.hostname}:{port}", "Upgrade: websocket", "Connection: Upgrade",
                 f"Sec-WebSocket-Key: {key}", "Sec-WebSocket-Version: 13",
                 *[f"{k}: {v}" for k, v in (headers or {}).items()], "", ""]
        self._sock.sendall("\r\n".join(lines).encode())
        head = b""
        while b"\r\n\r\n" not in head:
            chunk = self._sock.recv(4096)
            if not chunk:
                raise WebSocketClosed("the server closed the connection during the handshake")
            head += chunk
        head, _, self._buffer = head.partition(b"\r\n\r\n")
        status = head.split(b"\r\n", 1)[0]
        if b" 101 " not in status:
            raise WebSocketClosed(f"the server refused the upgrade: {status.decode(errors='replace')}")

    def settimeout(self, seconds: float | None) -> None:
        self._sock.settimeout(seconds)

    def _read(self, n: int) -> bytes:
        while len(self._buffer) < n:
            chunk = self._sock.recv(65536)
            if not chunk:
                raise WebSocketClosed("connection closed")
            self._buffer += chunk
        data, self._buffer = self._buffer[:n], self._buffer[n:]
        return data

    def _send_frame(self, opcode: int, payload: bytes) -> None:
        mask = os.urandom(4)
        n = len(payload)
        header = bytes([0x80 | opcode]) + (bytes([0x80 | n]) if n < 126 else
                                           bytes([0x80 | 126]) + struct.pack(">H", n) if n < 65536 else
                                           bytes([0x80 | 127]) + struct.pack(">Q", n))
        masked = bytes(b ^ mask[i % 4] for i, b in enumerate(payload))
        with self._lock:
            self._sock.sendall(header + mask + masked)

    def send(self, text: str) -> None:
        self._send_frame(0x1, text.encode())

    def recv(self) -> str:
        """The next complete text message. Pings are answered on the way."""
        message = b""
        while True:
            first, second = self._read(2)
            opcode, length = first & 0x0F, second & 0x7F
            if length == 126:
                length = struct.unpack(">H", self._read(2))[0]
            elif length == 127:
                length = struct.unpack(">Q", self._read(8))[0]
            mask = self._read(4) if second & 0x80 else b""
            payload = self._read(length)
            if mask:
                payload = bytes(b ^ mask[i % 4] for i, b in enumerate(payload))
            if opcode == 0x8:
                raise WebSocketClosed(f"closed by the server ({struct.unpack('>H', payload[:2])[0] if len(payload) >= 2 else 'no code'})")
            if opcode == 0x9:
                self._send_frame(0xA, payload)
                continue
            if opcode == 0xA:
                continue
            message += payload
            if first & 0x80:                 # FIN: the message is complete
                return message.decode("utf-8", errors="replace")

    def close(self) -> None:
        try:
            self._send_frame(0x8, struct.pack(">H", 1000))
        except OSError:
            pass
        finally:
            self._sock.close()
