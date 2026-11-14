"""The web gateway: serves the cockpit and streams each turn's events to it.

While the server runs it is also the scheduler's clock: a background thread
checks every twenty seconds for scheduled tasks that are due.

Standard library only. One endpoint matters most:

    POST /api/chat   runs a turn and streams its events as Server-Sent Events,
                     one JSON object per step, as they happen

The browser does not need the events slowly; it queues them and plays them at
a readable pace (see web/js/player.js). Replay of an old run reads the same
events from its trace file through GET /api/runs/<id>.

Local-server hardening, because a server on localhost is reachable by any
web page you visit:
  - binds to 127.0.0.1 only
  - rejects requests whose Host header is not localhost (DNS rebinding)
  - rejects state-changing requests whose Origin is another site (CSRF)

`loopwright serve --lan` opens it to other devices, for using the cockpit from
a phone. The agent keeps running on this machine; the phone is only a screen.
Because the agent can read your memory and run tools, every request from
another device must carry an access key: the link printed at start-up contains
it, the first visit stores it in a cookie, and anything without it gets a 403.
"""

from __future__ import annotations

import datetime as dt
import hmac
import json
import mimetypes
import os
import secrets
import socket
import threading
import time
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse

from loopwright import __version__, otel, scheduler, workflows
from loopwright.config import PACKAGE_DIR, Options, home_dir
from loopwright.context import IDENTITY, load_identity
from loopwright.evals import compare as eval_compare
from loopwright.evals import mine as eval_mine
from loopwright.evals import runner as eval_runner
from loopwright.events import STAGES, Event, list_traces, read_trace, trace_dir
from loopwright.gateways.chat import CHANNELS
from loopwright.harness import Agent
from loopwright.learn import PILLARS, load_concepts, load_interview, load_lessons
from loopwright.memory.skills import delete_skill, load_skills, save_skill
from loopwright.models.gateway import ModelGateway, available_providers, pick_provider
from loopwright.tools.browser import find_chrome
from loopwright.tools.plugins import OPT_IN, add_mcp_server, read_mcp_config, remove_mcp_server, set_tool_enabled

WEB = PACKAGE_DIR / "web"
APPROVAL_TIMEOUT_S = 180
SOURCE_SUFFIXES = {".py", ".md", ".toml", ".js", ".jsonl", ".txt"}


def handbook() -> dict[str, Any]:
    concepts = load_concepts()
    return {"concepts": concepts, "interview": load_interview(), "lessons": load_lessons(), "pillars": PILLARS,
            "stages": {k: {"label": v[0], "pillar": v[1], "file": v[2]} for k, v in STAGES.items()},
            "kinds": {kind: c["id"] for c in concepts for kind in c.get("kinds", [])}}


class App:
    def __init__(self, home: Path | None = None):
        self.root = Path(home) if home else home_dir()
        self._agents: dict[str, Agent] = {"main": Agent(self.root)}
        self.space = "main"                                 # which agent the current request is about
        self._pending: dict[str, dict[str, Any]] = {}      # call_id -> {"event": threading.Event, "approved": bool}
        self._lock = threading.Lock()

    # ---- spaces: "main" is your agent; "classroom" is a scratch copy the lessons run in,
    #      wiped at the start of each lesson so they never touch your real memory
    def use(self, space: str) -> App:
        view = App.__new__(App)
        view.__dict__ = {**self.__dict__, "space": "classroom" if space == "classroom" else "main"}
        return view

    @property
    def home(self) -> Path:
        return self.root if self.space == "main" else self.root / "classroom"

    @property
    def agent(self) -> Agent:
        with self._lock:
            if self.space not in self._agents:
                self._agents[self.space] = Agent(self.home)
            return self._agents[self.space]

    def reset_classroom(self) -> None:
        import shutil
        with self._lock:
            old = self._agents.pop("classroom", None)
            if old:
                old.store.close()
            shutil.rmtree(self.root / "classroom", ignore_errors=True)

    # ---- approvals: the turn's thread waits here until the browser answers
    def wait_for_approval(self, request: dict[str, Any]) -> bool:
        slot = {"event": threading.Event(), "approved": False}
        with self._lock:
            self._pending[request["call_id"]] = slot
        slot["event"].wait(APPROVAL_TIMEOUT_S)              # no answer in time counts as "no"
        with self._lock:
            self._pending.pop(request["call_id"], None)
        return slot["approved"]

    def answer_approval(self, call_id: str, approved: bool) -> bool:
        with self._lock:
            slot = self._pending.get(call_id)
        if not slot:
            return False
        slot["approved"] = approved
        slot["event"].set()
        return True

    def health(self) -> dict[str, Any]:
        return {"version": __version__, "providers": available_providers(), "default_provider": pick_provider("auto"),
                "home": str(self.home), "counts": self.agent.store.counts(), "options": Options().to_dict(),
                "static": False, "browser": bool(find_chrome())}

    def automations(self) -> dict[str, Any]:
        when = lambda ts: dt.datetime.fromtimestamp(ts).strftime("%a %Y-%m-%d %H:%M") if ts else ""  # noqa: E731
        tasks = [{**t, "enabled": bool(t["enabled"]), "next": when(t["next_run"]), "last": when(t["last_run"]),
                  "runs_text": scheduler.describe(t["schedule"])} for t in self.agent.store.tasks()]
        channels = [{"name": name, "configured": bool(os.environ.get(token)), "allowed": bool(os.environ.get(allow)),
                     "token_var": token, "allow_var": allow, "how": how, "command": f"loopwright {name}"}
                    for name, (token, allow, how) in CHANNELS.items()]
        return {"tasks": tasks, "workflows": [w.to_json() for w in workflows.load_all(self.home).values()],
                "channels": channels, "workflows_folder": str(self.home / "workflows")}

    def compare_report(self) -> dict[str, Any] | None:
        for path in (self.root / "compare" / "compare.json", PACKAGE_DIR.parent / "results" / "compare" / "compare.json"):
            if path.is_file():
                return json.loads(path.read_text(encoding="utf-8"))
        return None

    def compare_trace(self, slug: str, case_id: str) -> dict[str, Any] | None:
        clean = lambda text: "".join(ch for ch in text if ch.isalnum() or ch == "-")  # noqa: E731
        for root in (self.root / "compare" / "traces", PACKAGE_DIR.parent / "results" / "compare" / "traces"):
            path = root / clean(slug) / f"{clean(case_id)}.jsonl"
            if path.is_file():
                return read_trace(path)
        return None

    def memory(self) -> dict[str, Any]:
        store = self.agent.store
        return {"facts": store.facts(include_superseded=True), "episodes": store.episodes(),
                "events": store.events(), "notes": store.notes(), "outbox": store.outbox(),
                "skills": [{"name": s.name, "description": s.description, "triggers": s.triggers, "body": s.body,
                            "yours": str(self.home) in s.path} for s in load_skills(self.home)],
                "identity": load_identity(self.home), "identity_is_custom": (self.home / "IDENTITY.md").is_file(),
                "pending": store.pending_consolidation(),
                "counts": store.counts(), "db": str(store.path)}

    def tools(self) -> dict[str, Any]:
        agent = self.agent
        rows = [{"name": t.name, "description": t.description, "effect": t.effect, "untrusted": t.untrusted,
                 "private": t.private, "parameters": t.parameters, "enabled": t.name not in agent.disabled_tools,
                 "opt_in": t.name in OPT_IN,
                 "origin": "mcp" if "__" in t.name and t.name in agent._extension_tools else
                           "yours" if t.name in agent._extension_tools else "built in"}
                for t in agent.registry._tools.values()]
        return {"tools": rows, "mcp": agent.mcp_status, "plugins": agent.plugin_status,
                "mcp_config": read_mcp_config(self.home).get("servers", {}), "tools_folder": str(self.home / "tools")}

    def overview(self) -> dict[str, Any]:
        """All-time numbers, computed from the trace files: the traces are the source of truth."""
        turns, ms, tokens, cost, known_cost, steps = 0, 0, 0, 0.0, True, 0
        gate = {"retrieve": 0, "skip": 0}
        tools: dict[str, dict[str, int]] = {}
        for path in trace_dir(self.home).glob("*.jsonl"):
            try:
                trace = read_trace(path)
            except (OSError, json.JSONDecodeError):
                continue
            receipt = trace["end"].get("receipt") or {}
            turns += 1
            steps += len(trace["events"])
            ms += receipt.get("duration_ms", 0)
            tokens += receipt.get("tokens_in", 0) + receipt.get("tokens_out", 0)
            if receipt.get("cost_usd") is None:
                known_cost = False
            else:
                cost += receipt["cost_usd"]
            for e in trace["events"]:
                if e["kind"] == "memory.gate" and e["data"].get("mode") != "off":
                    gate["retrieve" if e["data"].get("retrieve") else "skip"] += 1
                elif e["kind"] in ("tool.run", "tool.error", "toolgate.refuse") and e.get("span") == "main":
                    row = tools.setdefault(e["data"].get("tool", "?"), {"ran": 0, "failed": 0, "refused": 0})
                    row[{"tool.run": "ran", "tool.error": "failed", "toolgate.refuse": "refused"}[e["kind"]]] += 1
        return {"turns": turns, "avg_ms": round(ms / turns) if turns else 0, "tokens": tokens, "steps": steps,
                "cost_usd": round(cost, 4) if known_cost else None, "gate": gate,
                "tools": [{"name": k, **v} for k, v in sorted(tools.items(), key=lambda kv: -kv[1]["ran"])],
                "tool_calls": sum(v["ran"] + v["failed"] for v in tools.values()), "counts": self.agent.store.counts()}

    def eval_report(self) -> dict[str, Any] | None:
        for path in (self.root / "evals" / "eval-report.json", PACKAGE_DIR.parent / "results" / "eval-report.json"):
            if path.is_file():
                return json.loads(path.read_text(encoding="utf-8"))
        return None

    def eval_trace(self, case_id: str) -> dict[str, Any] | None:
        safe = "".join(ch for ch in case_id if ch.isalnum() or ch == "-")
        for root in (self.root / "evals" / "traces", PACKAGE_DIR.parent / "results" / "traces"):
            if (root / f"{safe}.jsonl").is_file():
                return read_trace(root / f"{safe}.jsonl")
        return None


def lan_address() -> str:
    """This machine's address on the local network (no packet is sent to find it)."""
    probe = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        probe.connect(("10.255.255.255", 1))
        return probe.getsockname()[0]
    except OSError:
        return "127.0.0.1"
    finally:
        probe.close()


def make_handler(root_app: App, access_key: str = "") -> type[BaseHTTPRequestHandler]:
    class Handler(BaseHTTPRequestHandler):
        server_version = f"loopwright/{__version__}"

        def log_message(self, *args: Any) -> None:      # keep the terminal quiet
            pass

        # ------------------------------------------------ plumbing
        def _local(self) -> bool:
            host = (self.headers.get("Host") or "").split(":")[0]
            return host in ("127.0.0.1", "localhost", "[::1]") and self.client_address[0] in ("127.0.0.1", "::1")

        def _has_key(self) -> bool:
            cookies = dict(pair.strip().split("=", 1) for pair in (self.headers.get("Cookie") or "").split(";") if "=" in pair)
            return bool(access_key) and hmac.compare_digest(cookies.get("lw_key", ""), access_key)

        def _host_ok(self) -> bool:
            """Local requests need the right Host header. Requests from another device (only
            possible with --lan) need the access key instead."""
            return self._local() or self._has_key()

        def _origin_ok(self) -> bool:
            origin = self.headers.get("Origin")
            if not origin:
                return True
            if urlparse(origin).hostname in ("127.0.0.1", "localhost", "::1"):
                return True
            return self._has_key() and urlparse(origin).netloc == (self.headers.get("Host") or "")

        def _sign_in(self, url) -> bool:
            """A first visit from another device: `/?key=...` stores the key in a cookie and reloads without it."""
            given = (parse_qs(url.query).get("key") or [""])[0]
            if not access_key or not given:
                return False
            if not hmac.compare_digest(given, access_key):
                time.sleep(1)                     # make guessing slow
                self._json({"error": "wrong key"}, 403)
                return True
            self.send_response(303)
            self.send_header("Location", url.path or "/")
            self.send_header("Set-Cookie", f"lw_key={access_key}; Path=/; HttpOnly; SameSite=Strict; Max-Age=2592000")
            self.end_headers()
            return True

        def _json(self, data: Any, status: int = 200) -> None:
            body = json.dumps(data, ensure_ascii=False, default=str).encode()
            self.send_response(status)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)

        def _body(self) -> dict[str, Any]:
            length = int(self.headers.get("Content-Length") or 0)
            if not length or length > 1_000_000:
                return {}
            try:
                data = json.loads(self.rfile.read(length))
                return data if isinstance(data, dict) else {}
            except json.JSONDecodeError:
                return {}

        def _sse_start(self) -> None:
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream; charset=utf-8")
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Accel-Buffering", "no")
            self.end_headers()

        def _sse(self, data: dict[str, Any]) -> None:
            try:
                self.wfile.write(f"data: {json.dumps(data, ensure_ascii=False, default=str)}\n\n".encode())
                self.wfile.flush()
            except (BrokenPipeError, ConnectionResetError, OSError):
                pass                                    # the tab was closed; the turn still finishes and is traced

        # ------------------------------------------------ GET
        def do_GET(self) -> None:
            url = urlparse(self.path)
            if not self._local() and self._sign_in(url):
                return
            if not self._host_ok():
                return self._json({"error": "this cockpit is private: open the link with its key, from `loopwright serve --lan`"}, 403)
            path = url.path
            app = root_app.use(parse_qs(url.query).get("space", ["main"])[0])
            if path == "/api/health":
                return self._json(app.health())
            if path == "/api/handbook":
                return self._json(handbook())
            if path == "/api/runs":
                return self._json({"runs": list_traces(app.home)})
            if path.startswith("/api/runs/"):
                name = "".join(ch for ch in path.rsplit("/", 1)[1] if ch.isalnum() or ch == "-")
                file = trace_dir(app.home) / f"{name}.jsonl"
                return self._json(read_trace(file)) if file.is_file() else self._json({"error": "no such run"}, 404)
            if path.startswith("/api/shots/"):
                return self._shot(app, path.rsplit("/", 1)[1])
            if path.startswith("/api/otlp/"):
                name = "".join(ch for ch in path.rsplit("/", 1)[1] if ch.isalnum() or ch == "-")
                file = trace_dir(app.home) / f"{name}.jsonl"
                return self._json(otel.to_otlp(read_trace(file))) if file.is_file() else self._json({"error": "no such run"}, 404)
            if path == "/api/automations":
                return self._json(app.automations())
            if path == "/api/compare/report":
                return self._json({"report": app.compare_report(), "providers": available_providers()})
            if path.startswith("/api/compare/trace/"):
                parts = path.split("/")
                trace = app.compare_trace(parts[-2], parts[-1]) if len(parts) >= 6 else None
                return self._json(trace) if trace else self._json({"error": "no trace for that cell"}, 404)
            if path == "/api/evals/mine":
                return self._json({"cases": eval_mine.load_mine(app.home), "feedback": eval_mine.load_feedback(app.home)})
            if path == "/api/memory":
                return self._json(app.memory())
            if path == "/api/overview":
                return self._json(app.overview())
            if path == "/api/tools":
                return self._json(app.tools())
            if path == "/api/sessions":
                return self._json({"sessions": app.agent.store.sessions()})
            if path.startswith("/api/sessions/"):
                return self._json({"messages": app.agent.store.transcript(path.rsplit("/", 1)[1])})
            if path == "/api/evals/report":
                return self._json({"report": app.eval_report()})
            if path.startswith("/api/evals/trace/"):
                trace = app.eval_trace(path.rsplit("/", 1)[1])
                return self._json(trace) if trace else self._json({"error": "no trace for that case"}, 404)
            if path == "/api/source":
                return self._source(parse_qs(url.query).get("path", [""])[0])
            return self._static(path)

        def _shot(self, app: App, name: str) -> None:
            safe = "".join(ch for ch in name if ch.isalnum() or ch in "-.")
            target = trace_dir(app.home) / "shots" / safe
            if not safe.endswith(".jpg") or not target.is_file():
                return self._json({"error": "no such screenshot"}, 404)
            body = target.read_bytes()
            self.send_response(200)
            self.send_header("Content-Type", "image/jpeg")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def _source(self, rel: str) -> None:
            root = PACKAGE_DIR.parent.resolve()
            target = (root / rel).resolve()
            inside = PACKAGE_DIR.resolve() in target.parents
            if not inside or target.suffix not in SOURCE_SUFFIXES or not target.is_file():
                return self._json({"error": "not a readable source file"}, 404)
            self._json({"path": rel, "text": target.read_text(encoding="utf-8")})

        def _static(self, path: str) -> None:
            rel = "index.html" if path in ("/", "") else path.lstrip("/")
            target = (WEB / rel).resolve()
            if WEB.resolve() not in target.parents or not target.is_file():
                return self._json({"error": "not found"}, 404)
            body = target.read_bytes()
            self.send_response(200)
            self.send_header("Content-Type", (mimetypes.guess_type(target.name)[0] or "application/octet-stream")
                             + ("; charset=utf-8" if target.suffix in (".html", ".css", ".js", ".json", ".svg") else ""))
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-cache")
            self.end_headers()
            self.wfile.write(body)

        # ------------------------------------------------ POST / DELETE
        def do_POST(self) -> None:
            if not self._host_ok() or not self._origin_ok():
                return self._json({"error": "cross-site request refused"}, 403)
            path = urlparse(self.path).path
            body = self._body()
            app = root_app.use(str(body.get("space", "main")))
            if path == "/api/chat":
                return self._chat(app, body)
            if path == "/api/classroom/reset":
                root_app.reset_classroom()
                return self._json({"ok": True})
            if path == "/api/approve":
                return self._json({"ok": root_app.answer_approval(str(body.get("call_id", "")), bool(body.get("approved")))})
            if path == "/api/evals/run":
                return self._run_evals(app)
            if path == "/api/compare":
                return self._run_compare(app, body)
            if path == "/api/tasks":
                try:
                    return self._json({"ok": True, "task": scheduler.add_task(app.agent.store, str(body.get("prompt", "")).strip() or "(empty)",
                                                                              str(body.get("schedule", "")), app.agent.now(), "web:cockpit")})
                except ValueError as exc:
                    return self._json({"error": str(exc)}, 400)
            if path.startswith("/api/tasks/") and path.endswith(("/run", "/toggle")):
                _, _, _, task_id, action = path.split("/")
                task = app.agent.store.task(int(task_id)) if task_id.isdigit() else None
                if not task:
                    return self._json({"error": "no such task"}, 404)
                if action == "toggle":
                    upcoming = scheduler.next_after(task["schedule"], app.agent.now())
                    app.agent.store.set_task(task["id"], enabled=bool(body.get("enabled")),
                                             next_run=upcoming.timestamp() if upcoming else None)
                    return self._json({"ok": True})
                result = scheduler.Scheduler(app.agent).run_task(task)
                return self._json({"ok": result.ok, "turn_id": result.turn_id, "reply": result.reply})
            if path == "/api/feedback":
                return self._json({"ok": True, "feedback": eval_mine.add_feedback(app.home, str(body.get("turn_id", "")),
                                                                                  str(body.get("rating", "")), str(body.get("note", "")))})
            if path == "/api/evals/mine":
                name = "".join(ch for ch in str(body.get("turn_id", "")) if ch.isalnum() or ch == "-")
                file = trace_dir(app.home) / f"{name}.jsonl"
                if not file.is_file():
                    return self._json({"error": "no such run"}, 404)
                return self._json({"ok": True, "case": eval_mine.add_case(app.home, read_trace(file), str(body.get("note", "")))})
            if path == "/api/evals/mine/run":
                provider = pick_provider(str((body.get("options") or {}).get("provider", "auto")))
                return self._json(eval_mine.run_mine(app.home, {"provider": provider, "fast": provider == "scripted"}))
            if path == "/api/skills":
                try:
                    saved = save_skill(app.home, str(body.get("name", "")), str(body.get("description", "")),
                                       [str(t) for t in body.get("triggers", [])], str(body.get("body", "")))
                except ValueError as exc:
                    return self._json({"error": str(exc)}, 400)
                return self._json({"ok": True, "name": saved.parent.name})
            if path == "/api/tools/toggle":
                set_tool_enabled(app.home, str(body.get("name", "")), bool(body.get("enabled")))
                app.agent.load_extensions()
                return self._json({"ok": True})
            if path == "/api/mcp":
                try:
                    add_mcp_server(app.home, str(body.get("name", "")), str(body.get("command", "")),
                                   [str(a) for a in body.get("args", [])], str(body.get("trust", "ask")))
                except ValueError as exc:
                    return self._json({"error": str(exc)}, 400)
                app.agent.load_extensions()
                return self._json({"ok": True, "status": app.agent.mcp_status})
            if path == "/api/identity":
                text = str(body.get("text", "")).strip()
                target = app.home / "IDENTITY.md"
                if text and text != IDENTITY:
                    target.write_text(text[:8000] + "\n", encoding="utf-8")
                elif target.is_file():
                    target.unlink()             # empty or unchanged text restores the built-in identity
                return self._json({"ok": True, "custom": target.is_file()})
            if path == "/api/memory/reset":
                if body.get("confirm") != "reset":
                    return self._json({"error": "send {\"confirm\": \"reset\"} to wipe memory"}, 400)
                app.agent.store.reset()
                return self._json({"ok": True})
            self._json({"error": "not found"}, 404)

        def do_DELETE(self) -> None:
            if not self._host_ok() or not self._origin_ok():
                return self._json({"error": "cross-site request refused"}, 403)
            url = urlparse(self.path)
            path = url.path
            app = root_app.use(parse_qs(url.query).get("space", ["main"])[0])
            if path.startswith("/api/memory/facts/"):
                tail = path.rsplit("/", 1)[1]
                return self._json({"ok": tail.isdigit() and app.agent.store.delete_fact(int(tail))})
            if path.startswith("/api/mcp/"):
                ok = remove_mcp_server(app.home, path.rsplit("/", 1)[1])
                app.agent.load_extensions()
                return self._json({"ok": ok})
            if path.startswith("/api/skills/"):
                return self._json({"ok": delete_skill(app.home, path.rsplit("/", 1)[1])})
            if path.startswith("/api/tasks/"):
                tail = path.rsplit("/", 1)[1]
                return self._json({"ok": tail.isdigit() and app.agent.store.delete_task(int(tail))})
            if path.startswith("/api/evals/mine/"):
                return self._json({"ok": eval_mine.remove_case(app.home, path.rsplit("/", 1)[1])})
            self._json({"error": "not found"}, 404)

        def _chat(self, app: App, body: dict[str, Any]) -> None:
            message = str(body.get("message", "")).strip()
            if not message:
                return self._json({"error": "empty message"}, 400)
            options = Options.from_dict(body.get("options") or {})
            self._sse_start()

            def sink(event: Event) -> None:
                self._sse({"type": "event", **event.to_json()})

            try:
                result = app.agent.run_turn(message, session=str(body.get("session") or "default"), options=options,
                                            sinks=[sink], approver=root_app.wait_for_approval, channel="web", origin="web:cockpit")
                self._sse({"type": "done", "turn_id": result.turn_id, "reply": result.reply, "ok": result.ok,
                           "receipt": result.receipt, "checks": result.checks})
            except Exception as exc:          # never leave the browser hanging on a half-open stream
                self._sse({"type": "done", "turn_id": "", "ok": False, "receipt": {}, "checks": [],
                           "reply": f"The harness itself raised {type(exc).__name__}: {exc}"})

        def _run_compare(self, app: App, body: dict[str, Any]) -> None:
            try:
                targets = [eval_compare.parse_target(str(t)) for t in body.get("targets", [])][:6]
            except ValueError as exc:
                return self._json({"error": str(exc)}, 400)
            if not targets:
                return self._json({"error": "choose at least one model to compare"}, 400)
            self._sse_start()
            try:
                report = eval_compare.run_compare(targets, app.root / "compare", on_progress=lambda p: self._sse({"type": "progress", **p}))
                self._sse({"type": "done", "report": report})
            except Exception as exc:
                self._sse({"type": "error", "error": f"{type(exc).__name__}: {exc}"})

        def _run_evals(self, app: App) -> None:
            self._sse_start()
            try:
                report = eval_runner.run_all(app.root / "evals", on_progress=lambda p: self._sse({"type": "progress", **p}))
                self._sse({"type": "done", "report": report})
            except Exception as exc:
                self._sse({"type": "error", "error": f"{type(exc).__name__}: {exc}"})

    return Handler


def serve(port: int = 8765, home: Path | None = None, open_browser: bool = True, lan: bool = False) -> None:
    app = App(home)
    key = secrets.token_hex(5) if lan else ""
    server = ThreadingHTTPServer(("0.0.0.0" if lan else "127.0.0.1", port), make_handler(app, key))
    server.daemon_threads = True
    url = f"http://127.0.0.1:{port}"
    provider = pick_provider("auto")
    print(f"Loopwright cockpit  {url}")
    print(f"model               {provider}  ({available_providers()[provider]['note']})")
    print(f"state               {app.root}")
    print(f"scheduled tasks     {len(app.agent.store.tasks())} (checked every 20s while this runs)")
    if lan:
        print(f"\nOn your phone       http://{lan_address()}:{port}/?key={key}")
        print("                    Same Wi-Fi as this computer. The agent runs here; the phone is a screen.")
        print("                    Anyone with this link can use your agent and its tools until you stop it.")
        print("                    From anywhere: point a tunnel at this port and add ?key=" + key + " to its address")
        print(f"                    (for example: cloudflared tunnel --url http://localhost:{port}).")
    clock = scheduler.Scheduler(app.agent)
    clock.start()                               # scheduled tasks run for as long as the cockpit is up
    print("Ctrl+C to stop")
    if open_browser:
        threading.Timer(0.6, lambda: webbrowser.open(url)).start()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nstopped")
    finally:
        server.server_close()


# kept importable for tests and the demo builder
__all__ = ["App", "ModelGateway", "handbook", "make_handler", "serve"]
