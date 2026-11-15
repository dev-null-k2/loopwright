"""A public HTTPS address for the cockpit, so a phone anywhere can reach it.

`loopwright serve --tunnel` runs Cloudflare's `cloudflared` (a separate program
you install yourself) in its quick-tunnel mode: no account, a new random
`https://….trycloudflare.com` address each time, forwarding to this machine's
cockpit port. The agent still runs here. The address is public, so every request
through it must carry the access key.

Any other tunnel works too: start it yourself and pass its address with
`loopwright serve --public-url https://…`.
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import threading
import time

ADDRESS = re.compile(r"https://[a-z0-9][a-z0-9-]*\.trycloudflare\.com")
INSTALL = ("cloudflared is not installed. On a Mac: brew install cloudflared. "
           "Elsewhere: https://developers.cloudflare.com/cloudflare-one/connections/connect-networks/downloads/")


class TunnelError(RuntimeError):
    pass


def find_cloudflared() -> str | None:
    return os.environ.get("LOOPWRIGHT_CLOUDFLARED") or shutil.which("cloudflared")


class Tunnel:
    def __init__(self, process: subprocess.Popen, url: str):
        self.process = process
        self.url = url

    def close(self) -> None:
        if self.process.poll() is None:
            self.process.terminate()
            try:
                self.process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self.process.kill()


def start(port: int, timeout: float = 30.0, program: str | None = None) -> Tunnel:
    """Start the tunnel and wait until it has printed its public address."""
    program = program or find_cloudflared()
    if not program:
        raise TunnelError(INSTALL)
    try:
        process = subprocess.Popen([program, "tunnel", "--no-autoupdate", "--url", f"http://127.0.0.1:{port}"],
                                   stdout=subprocess.PIPE, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL, text=True, errors="replace")
    except OSError as error:
        raise TunnelError(f"could not start {program}: {error}") from error
    found: list[str] = []
    tail: list[str] = []

    def read() -> None:                 # keeps draining the output for as long as the tunnel runs
        for line in process.stdout:
            tail.append(line.strip())
            del tail[:-6]
            match = ADDRESS.search(line)
            if match and not found:
                found.append(match.group(0))

    threading.Thread(target=read, daemon=True).start()
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline and not found and process.poll() is None:
        time.sleep(0.1)
    if not found:
        time.sleep(0.2)                 # let the reader finish the last lines
    if found:
        return Tunnel(process, found[0])
    Tunnel(process, "").close()
    why = "; ".join(line for line in tail if line)[-300:] or "it printed no address"
    raise TunnelError(f"the tunnel did not start: {why}")
