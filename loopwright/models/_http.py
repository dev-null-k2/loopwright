"""One small JSON-over-HTTPS helper, so the API providers need no SDK."""

from __future__ import annotations

import json
import urllib.error
import urllib.request
from typing import Any

from loopwright.models.base import FatalModelError, TransientModelError

RETRYABLE = {408, 409, 425, 429, 500, 502, 503, 504, 529}


def post_json(url: str, headers: dict[str, str], body: dict[str, Any], timeout: float = 120) -> dict[str, Any]:
    request = urllib.request.Request(url, data=json.dumps(body).encode(), method="POST",
                                     headers={"content-type": "application/json", **headers})
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return json.loads(response.read().decode())
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode(errors="replace")[:400]
        try:
            detail = json.loads(detail).get("error", {}).get("message", detail)
        except (json.JSONDecodeError, AttributeError):
            pass
        if exc.code in RETRYABLE:
            raise TransientModelError(f"HTTP {exc.code}: {detail}") from exc
        raise FatalModelError(f"HTTP {exc.code}: {detail}") from exc
    except (urllib.error.URLError, TimeoutError, ConnectionError) as exc:
        raise TransientModelError(f"network error: {exc}") from exc
