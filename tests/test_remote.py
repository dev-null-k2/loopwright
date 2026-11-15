"""The public page as the screen for an agent on your own computer: `loopwright serve --tunnel`.

The page is on another origin, so the server has to let that one origin in, and only with the key.
"""

import hashlib
import json
import stat
import threading
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer

import pytest

from loopwright import qr, tunnel
from loopwright.server import App, make_handler, page_origin, phone_link

PAGE = "https://dev-null-k2.github.io"
KEY = "0123456789abcdef"


@pytest.fixture()
def computer(tmp_path, monkeypatch):
    """A cockpit server with a key, and a function that calls it the way a browser on the public page would."""
    monkeypatch.setenv("LOOPWRIGHT_PROVIDER", "scripted")
    monkeypatch.setattr("loopwright.server.time.sleep", lambda seconds: None)     # the wrong-key delay
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(App(tmp_path / "home"), KEY, (PAGE,)))
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    port = httpd.server_address[1]
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))

    def call(path, headers=None, body=None, method=None):
        request = urllib.request.Request(f"http://127.0.0.1:{port}{path}", data=json.dumps(body).encode() if body is not None else None,
                                         headers={"Host": "calm-river.trycloudflare.com", "content-type": "application/json", **(headers or {})}, method=method)
        try:
            with opener.open(request, timeout=20) as response:
                return response.status, dict(response.headers), response.read().decode()
        except urllib.error.HTTPError as exc:
            return exc.code, dict(exc.headers), exc.read().decode()

    call.port = port
    yield call
    httpd.shutdown()


def test_the_public_page_can_drive_the_agent_with_the_key(computer):
    page = {"Origin": PAGE, "X-Loopwright-Key": KEY}
    status, headers, body = computer("/api/health", page)
    assert status == 200 and json.loads(body)["version"]
    assert headers["Access-Control-Allow-Origin"] == PAGE and "Origin" in headers["Vary"]      # so the page may read the answer

    status, headers, _ = computer("/api/chat", {"Origin": PAGE, "Access-Control-Request-Method": "POST"}, method="OPTIONS")   # the browser asks first
    assert status == 204 and headers["Access-Control-Allow-Origin"] == PAGE
    assert "x-loopwright-key" in headers["Access-Control-Allow-Headers"] and "POST" in headers["Access-Control-Allow-Methods"]

    status, headers, body = computer("/api/chat", page, {"message": "What is 12% of 300?", "session": "s", "options": {"provider": "scripted", "fast": True}})
    assert status == 200 and headers["Access-Control-Allow-Origin"] == PAGE
    messages = [json.loads(chunk[6:]) for chunk in body.split("\n\n") if chunk.startswith("data: ")]
    assert messages[-1]["type"] == "done" and "36" in messages[-1]["reply"]
    assert any(m["type"] == "event" and m["kind"] == "tool.run" for m in messages)          # the steps come along, live


def test_without_the_key_the_public_page_gets_nothing(computer):
    for headers in ({"Origin": PAGE}, {"Origin": PAGE, "X-Loopwright-Key": "0123456789abcdeX"}, {"Origin": PAGE, "X-Loopwright-Key": "cl\xe9 fausse"}):
        status, answer, body = computer("/api/memory", headers)
        assert status == 403 and "key" in json.loads(body)["error"]
        assert answer["Access-Control-Allow-Origin"] == PAGE                                   # readable, so the page can say why
        assert computer("/api/feedback", headers, {"turn_id": "t", "rating": "up"})[0] == 403
    # a cookie is not enough from another origin: the key must be in the header, which a foreign site cannot set
    assert computer("/api/feedback", {"Origin": PAGE, "Cookie": f"lw_key={KEY}"}, {"turn_id": "t", "rating": "up"})[0] == 403


def test_no_other_site_is_let_in(computer):
    evil = {"Origin": "https://evil.example", "X-Loopwright-Key": KEY}
    status, headers, _ = computer("/api/health", evil)
    assert "Access-Control-Allow-Origin" not in headers                                        # a browser will not hand it the answer
    assert computer("/api/feedback", evil, {"turn_id": "t", "rating": "up"})[0] == 403
    assert computer("/api/chat", {"Origin": "https://evil.example"}, method="OPTIONS")[0] == 403
    assert computer("/api/chat", {"Origin": PAGE + ".evil.example"}, method="OPTIONS")[0] == 403


def test_a_tunnel_that_ends_on_this_machine_is_not_treated_as_this_machine(computer):
    """A tunnel connects from 127.0.0.1. If it also rewrote the Host header, the request would look local."""
    local = {"Host": f"127.0.0.1:{computer.port}"}
    assert computer("/api/health", local)[0] == 200
    assert computer("/api/health", {**local, "X-Forwarded-For": "203.0.113.9"})[0] == 403
    assert computer("/api/health", {**local, "Cf-Connecting-Ip": "203.0.113.9"})[0] == 403
    assert computer("/api/health", {**local, "X-Forwarded-For": "203.0.113.9", "X-Loopwright-Key": KEY})[0] == 200


def test_a_server_without_a_key_never_opens_to_the_public_page(tmp_path, monkeypatch):
    """Plain `loopwright serve`: the page's origin is known, but nothing was shared, so it stays out."""
    monkeypatch.setenv("LOOPWRIGHT_PROVIDER", "scripted")
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(App(tmp_path / "home"), "", (PAGE,)))
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    try:
        for method, headers in (("OPTIONS", {"Origin": PAGE}), ("GET", {"Origin": PAGE, "X-Loopwright-Key": ""})):
            request = urllib.request.Request(f"http://127.0.0.1:{httpd.server_address[1]}/api/health", headers=headers, method=method)
            try:
                with opener.open(request, timeout=10) as response:
                    assert method == "GET" and "Access-Control-Allow-Origin" not in response.headers
            except urllib.error.HTTPError as exc:
                assert exc.code == 403 and "Access-Control-Allow-Origin" not in exc.headers
    finally:
        httpd.shutdown()


def test_the_phone_link_keeps_the_address_and_key_out_of_the_request():
    link = phone_link("https://dev-null-k2.github.io/loopwright/", "https://calm-river.trycloudflare.com/", KEY)
    assert link == f"https://dev-null-k2.github.io/loopwright/#connect=https://calm-river.trycloudflare.com&key={KEY}"
    assert link.index("#") < link.index("connect")                    # after the #: browsers never send that part to the host
    assert page_origin("https://dev-null-k2.github.io/loopwright/") == PAGE


def fake_cloudflared(tmp_path, script: str) -> str:
    path = tmp_path / "cloudflared"
    path.write_text("#!/bin/sh\n" + script)
    path.chmod(path.stat().st_mode | stat.S_IEXEC)
    return str(path)


def test_the_tunnel_address_is_read_from_cloudflared(tmp_path):
    program = fake_cloudflared(tmp_path, 'echo "INF Requesting new quick Tunnel on trycloudflare.com..." >&2\n'
                                         'echo "INF |  https://calm-river-stone-light.trycloudflare.com  |" >&2\nexec sleep 30\n')
    tube = tunnel.start(8765, timeout=10, program=program)
    try:
        assert tube.url == "https://calm-river-stone-light.trycloudflare.com" and tube.process.poll() is None
    finally:
        tube.close()
    assert tube.process.poll() is not None                            # stopped with the cockpit


def test_a_tunnel_that_fails_says_why(tmp_path, monkeypatch):
    program = fake_cloudflared(tmp_path, 'echo "ERR failed to request quick Tunnel: no network" >&2\nexit 1\n')
    with pytest.raises(tunnel.TunnelError, match="no network"):
        tunnel.start(8765, timeout=10, program=program)
    monkeypatch.setenv("PATH", str(tmp_path / "empty"))
    monkeypatch.delenv("LOOPWRIGHT_CLOUDFLARED", raising=False)
    with pytest.raises(tunnel.TunnelError, match="brew install cloudflared"):
        tunnel.start(8765)


def digest(grid) -> str:
    return hashlib.sha256("".join("".join(map(str, row)) for row in grid).encode()).hexdigest()[:16]


def test_the_qr_code_is_the_one_a_phone_reads():
    """The two fingerprints are of codes that were decoded back to their text with OpenCV's reader when
    this was written (as were codes of every size from version 1 to 10)."""
    link = phone_link("https://dev-null-k2.github.io/loopwright/", "https://calm-river-stone-light.trycloudflare.com", KEY)
    grid = qr.matrix(link)
    assert len(grid) == 41 and digest(grid) == "b66f943fed0aa3d2"
    small = qr.matrix("https://example.com")
    assert len(small) == 25 and digest(small) == "b651e04e1cfffb83"
    for row, col in ((0, 0), (0, 18), (18, 0)):                       # the three corner squares
        assert [small[row][col + i] for i in range(7)] == [1] * 7 and small[row + 3][col + 3] == 1 and small[row + 1][col + 1] == 0
    with pytest.raises(ValueError):
        qr.matrix("x" * 300)


def test_the_qr_code_is_drawn_dark_on_white_in_any_terminal():
    drawn = qr.render("https://example.com")
    lines = drawn.split("\n")
    assert len(lines) == (25 + 6 + 1) // 2 and all(line.startswith("\033[30;107m") and line.endswith("\033[0m") for line in lines)
    plain = qr.render("https://example.com", colour=False).split("\n")
    assert all(len(line) == 31 for line in plain) and set("".join(plain)) <= set(" ▀▄█")
