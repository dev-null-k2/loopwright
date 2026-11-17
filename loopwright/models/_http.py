"""Two small HTTPS helpers, so the API providers need no SDK: one for a whole
JSON answer, one for an answer that arrives a piece at a time."""

from __future__ import annotations

import http.client
import json
import urllib.error
import urllib.request
from collections.abc import Iterator
from typing import Any

from loopwright.models.base import FatalModelError, TransientModelError

RETRYABLE = {408, 409, 425, 429, 500, 502, 503, 504, 529}


def _http_error(exc: urllib.error.HTTPError) -> Exception:
    detail = exc.read().decode(errors="replace")[:400]
    try:
        detail = json.loads(detail).get("error", {}).get("message", detail)
    except (json.JSONDecodeError, AttributeError):
        pass
    kind = TransientModelError if exc.code in RETRYABLE else FatalModelError
    return kind(f"HTTP {exc.code}: {detail}")


def post_json(url: str, headers: dict[str, str], body: dict[str, Any], timeout: float = 120) -> dict[str, Any]:
    request = urllib.request.Request(url, data=json.dumps(body).encode(), method="POST",
                                     headers={"content-type": "application/json", **headers})
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return json.loads(response.read().decode())
    except urllib.error.HTTPError as exc:
        raise _http_error(exc) from exc
    except json.JSONDecodeError as exc:
        raise TransientModelError("the server answered with something that is not JSON") from exc
    except (urllib.error.URLError, TimeoutError, ConnectionError) as exc:
        raise TransientModelError(f"network error: {exc}") from exc


def read_stream(lines: Iterator[bytes]) -> Iterator[dict[str, Any]]:
    """Decode a streamed HTTP body into JSON objects, one per message.

    Two framings are in use and this reads both. Server-Sent Events (Anthropic,
    OpenAI and everything that copies them) send `data: {...}` lines with a
    blank line after each message, and OpenAI ends with `data: [DONE]`. Ollama's
    own API sends one bare JSON object per line."""
    held: list[str] = []
    for raw in lines:
        line = raw.decode("utf-8", errors="replace").rstrip("\r\n")
        if line.startswith("data:"):
            held.append(line[5:].lstrip())
            continue
        if line.startswith("{"):                       # one JSON object per line
            held.append(line)
        if held and (not line or line.startswith("{")):   # a blank line ends an SSE message
            text, held = "\n".join(held), []
            if text.strip() == "[DONE]":
                return
            try:
                yield json.loads(text)
            except json.JSONDecodeError:
                continue                                # a keep-alive or a comment: nothing to read
    if held and "\n".join(held).strip() != "[DONE]":    # the server closed without a final blank line
        try:
            yield json.loads("\n".join(held))
        except json.JSONDecodeError:
            pass


def post_stream(url: str, headers: dict[str, str], body: dict[str, Any], timeout: float = 120) -> Iterator[dict[str, Any]]:
    """POST, then yield each streamed message as it arrives. `timeout` is how
    long the connection may sit silent, not a limit on the whole answer: a
    long reply is fine as long as pieces keep coming."""
    request = urllib.request.Request(url, data=json.dumps(body).encode(), method="POST",
                                     headers={"content-type": "application/json", "accept": "text/event-stream", **headers})
    try:
        response = urllib.request.urlopen(request, timeout=timeout)
    except urllib.error.HTTPError as exc:
        raise _http_error(exc) from exc
    except (urllib.error.URLError, TimeoutError, ConnectionError) as exc:
        raise TransientModelError(f"network error: {exc}") from exc
    try:
        with response:
            yield from read_stream(response)
    except (urllib.error.URLError, TimeoutError, ConnectionError, http.client.HTTPException, OSError) as exc:
        # the stream broke part-way: the caller has seen some text already, and will be told to start over
        raise TransientModelError(f"the stream was cut off: {exc}") from exc
