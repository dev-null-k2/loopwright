"""THE STORE: everything the agent keeps between turns, in one SQLite file.

Open `~/.loopwright/state.db` with any SQLite browser and you are looking at
the agent's whole long-term state. The tables map one-to-one onto the kinds of
memory the handbook describes:

  messages   working memory   the running conversation, per session
  sessions   working memory   the compaction summary that replaces old messages
  facts      semantic memory  durable statements ("Alex prefers mornings")
  episodes   episodic memory  dated summaries of what happened
  events_cal, notes, outbox   the world the tools act on (all local)
  tool_log   idempotency      results of side-effecting calls, by key
  checkpoints durable runs    the loop's state after every iteration
  tasks      scheduled runs   prompts the agent runs on a clock, with no one watching
  deliveries scheduled runs   results waiting to go back to the chat app that asked

Facts are never edited in place. A changed fact gets a new row and the old row
points at it (`superseded_by`), so the history of what the agent believed, and
when, is kept.
"""

from __future__ import annotations

import json
import sqlite3
import threading
import time
from pathlib import Path
from typing import Any

from loopwright.memory.retrieval import Hit, Index

SCHEMA = """
CREATE TABLE IF NOT EXISTS messages (
    id INTEGER PRIMARY KEY, session TEXT NOT NULL, turn_id TEXT, role TEXT NOT NULL,
    content TEXT NOT NULL, created REAL NOT NULL, consolidated INTEGER DEFAULT 0);
CREATE TABLE IF NOT EXISTS sessions (
    session TEXT PRIMARY KEY, summary TEXT DEFAULT '', summarized_upto INTEGER DEFAULT 0);
CREATE TABLE IF NOT EXISTS facts (
    id INTEGER PRIMARY KEY, text TEXT NOT NULL, subject TEXT DEFAULT '', attr TEXT DEFAULT '',
    source TEXT DEFAULT '', created REAL NOT NULL, superseded_by INTEGER,
    uses INTEGER DEFAULT 0, last_used REAL);
CREATE TABLE IF NOT EXISTS episodes (
    id INTEGER PRIMARY KEY, day TEXT NOT NULL, summary TEXT NOT NULL, session TEXT, created REAL NOT NULL);
CREATE TABLE IF NOT EXISTS events_cal (
    id INTEGER PRIMARY KEY, title TEXT NOT NULL, day TEXT NOT NULL, time TEXT NOT NULL,
    attendees TEXT DEFAULT '[]', created REAL NOT NULL);
CREATE TABLE IF NOT EXISTS notes (id INTEGER PRIMARY KEY, text TEXT NOT NULL, created REAL NOT NULL);
CREATE TABLE IF NOT EXISTS outbox (
    id INTEGER PRIMARY KEY, recipient TEXT NOT NULL, body TEXT NOT NULL, created REAL NOT NULL);
CREATE TABLE IF NOT EXISTS tool_log (idem_key TEXT PRIMARY KEY, result TEXT NOT NULL, created REAL NOT NULL);
CREATE TABLE IF NOT EXISTS checkpoints (
    turn_id TEXT NOT NULL, step INTEGER NOT NULL, state TEXT NOT NULL, created REAL NOT NULL,
    PRIMARY KEY (turn_id, step));
CREATE TABLE IF NOT EXISTS tasks (
    id INTEGER PRIMARY KEY, prompt TEXT NOT NULL, schedule TEXT NOT NULL, next_run REAL, last_run REAL,
    last_status TEXT DEFAULT '', last_reply TEXT DEFAULT '', last_turn TEXT DEFAULT '', origin TEXT DEFAULT '',
    enabled INTEGER DEFAULT 1, runs INTEGER DEFAULT 0, created REAL NOT NULL);
CREATE TABLE IF NOT EXISTS deliveries (
    id INTEGER PRIMARY KEY, origin TEXT NOT NULL, body TEXT NOT NULL, created REAL NOT NULL, sent INTEGER DEFAULT 0);
"""


class Store:
    def __init__(self, home: Path):
        self.home = Path(home)
        self.home.mkdir(parents=True, exist_ok=True)
        self.path = self.home / "state.db"
        self._lock = threading.RLock()
        self._db = sqlite3.connect(self.path, check_same_thread=False)
        self._db.row_factory = sqlite3.Row
        with self._lock:
            self._db.executescript(SCHEMA)
            self._db.commit()

    def _run(self, sql: str, args: tuple = ()) -> sqlite3.Cursor:
        with self._lock:
            cur = self._db.execute(sql, args)
            self._db.commit()
            return cur

    def _rows(self, sql: str, args: tuple = ()) -> list[dict[str, Any]]:
        with self._lock:
            return [dict(r) for r in self._db.execute(sql, args).fetchall()]

    def close(self) -> None:
        with self._lock:
            self._db.close()

    # ------------------------------------------------ working memory
    def add_message(self, session: str, role: str, content: str, turn_id: str = "") -> int:
        return self._run("INSERT INTO messages (session, turn_id, role, content, created) VALUES (?,?,?,?,?)",
                         (session, turn_id, role, content, time.time())).lastrowid

    def history(self, session: str, after_id: int = 0) -> list[dict[str, Any]]:
        return self._rows("SELECT id, role, content FROM messages WHERE session=? AND id>? ORDER BY id",
                          (session, after_id))

    def session_summary(self, session: str) -> tuple[str, int]:
        rows = self._rows("SELECT summary, summarized_upto FROM sessions WHERE session=?", (session,))
        return (rows[0]["summary"], rows[0]["summarized_upto"]) if rows else ("", 0)

    def set_session_summary(self, session: str, summary: str, upto: int) -> None:
        self._run("INSERT INTO sessions (session, summary, summarized_upto) VALUES (?,?,?) "
                  "ON CONFLICT(session) DO UPDATE SET summary=excluded.summary, summarized_upto=excluded.summarized_upto",
                  (session, summary, upto))

    def sessions(self, limit: int = 30) -> list[dict[str, Any]]:
        """Every conversation, newest first, titled by its first message."""
        return self._rows(
            "SELECT m.session, COUNT(*) AS messages, MAX(m.created) AS last, "
            "(SELECT content FROM messages WHERE session=m.session AND role='user' ORDER BY id LIMIT 1) AS title "
            "FROM messages m GROUP BY m.session ORDER BY last DESC LIMIT ?", (limit,))

    def transcript(self, session: str) -> list[dict[str, Any]]:
        return self._rows("SELECT role, content, turn_id FROM messages WHERE session=? ORDER BY id", (session,))

    def pending_consolidation(self) -> list[dict[str, Any]]:
        return self._rows("SELECT session, role, content FROM messages WHERE consolidated=0 ORDER BY id")

    def unconsolidated(self, session: str) -> list[dict[str, Any]]:
        return self._rows("SELECT id, role, content FROM messages WHERE session=? AND consolidated=0 ORDER BY id",
                          (session,))

    def mark_consolidated(self, ids: list[int]) -> None:
        if ids:
            self._run(f"UPDATE messages SET consolidated=1 WHERE id IN ({','.join('?' * len(ids))})", tuple(ids))

    # ------------------------------------------------ semantic memory
    def add_fact(self, text: str, subject: str = "", attr: str = "", source: str = "") -> int:
        return self._run("INSERT INTO facts (text, subject, attr, source, created) VALUES (?,?,?,?,?)",
                         (text.strip(), subject.strip().lower(), attr.strip().lower(), source, time.time())).lastrowid

    def supersede(self, old_id: int, new_id: int) -> None:
        self._run("UPDATE facts SET superseded_by=? WHERE id=?", (new_id, old_id))

    def delete_fact(self, fact_id: int) -> bool:
        return self._run("DELETE FROM facts WHERE id=?", (fact_id,)).rowcount > 0

    def facts(self, include_superseded: bool = False) -> list[dict[str, Any]]:
        where = "" if include_superseded else "WHERE superseded_by IS NULL"
        return self._rows(f"SELECT * FROM facts {where} ORDER BY id")

    def subjects(self) -> set[str]:
        """Who and what the agent has facts about; the retrieval gate uses this."""
        return {r["subject"] for r in self._rows(
            "SELECT DISTINCT subject FROM facts WHERE superseded_by IS NULL AND subject NOT IN ('', 'user')")}

    def search_facts(self, query: str, k: int = 5, mode: str = "hybrid") -> list[Hit]:
        index = Index()
        for fact in self.facts():
            index.add(f"fact:{fact['id']}", fact["text"], {"kind": "fact", "subject": fact["subject"]},
                      boost=fact["subject"] if fact["subject"] != "user" else "")
        hits = index.search(query, k=k, mode=mode)
        used = [int(h.id.split(":")[1]) for h in hits]
        if used:
            self._run(f"UPDATE facts SET uses=uses+1, last_used=? WHERE id IN ({','.join('?' * len(used))})",
                      (time.time(), *used))
        return hits

    # ------------------------------------------------ episodic memory
    def add_episode(self, summary: str, session: str = "", day: str = "") -> int:
        return self._run("INSERT INTO episodes (day, summary, session, created) VALUES (?,?,?,?)",
                         (day or time.strftime("%Y-%m-%d"), summary, session, time.time())).lastrowid

    def episodes(self) -> list[dict[str, Any]]:
        return self._rows("SELECT * FROM episodes ORDER BY id DESC")

    def search_episodes(self, query: str, k: int = 2) -> list[Hit]:
        index = Index()
        for ep in self.episodes():
            index.add(f"episode:{ep['id']}", f"{ep['day']}: {ep['summary']}", {"kind": "episode", "day": ep["day"]})
        return index.search(query, k=k, min_cosine=0.3)

    # ------------------------------------------------ the world the tools act on
    def add_event(self, title: str, day: str, at: str, attendees: list[str]) -> int:
        return self._run("INSERT INTO events_cal (title, day, time, attendees, created) VALUES (?,?,?,?,?)",
                         (title, day, at, json.dumps(attendees), time.time())).lastrowid

    def events(self, day_from: str = "", day_to: str = "") -> list[dict[str, Any]]:
        rows = self._rows("SELECT * FROM events_cal WHERE day>=? AND day<=? ORDER BY day, time",
                          (day_from or "0000", day_to or "9999"))
        for row in rows:
            row["attendees"] = json.loads(row["attendees"])
        return rows

    def add_note(self, text: str) -> int:
        return self._run("INSERT INTO notes (text, created) VALUES (?,?)", (text, time.time())).lastrowid

    def notes(self) -> list[dict[str, Any]]:
        return self._rows("SELECT * FROM notes ORDER BY id DESC")

    def add_outbox(self, recipient: str, body: str) -> int:
        return self._run("INSERT INTO outbox (recipient, body, created) VALUES (?,?,?)",
                         (recipient, body, time.time())).lastrowid

    def outbox(self) -> list[dict[str, Any]]:
        return self._rows("SELECT * FROM outbox ORDER BY id DESC")

    # ------------------------------------------------ idempotency + checkpoints
    def tool_result(self, idem_key: str) -> str | None:
        rows = self._rows("SELECT result FROM tool_log WHERE idem_key=?", (idem_key,))
        return rows[0]["result"] if rows else None

    def save_tool_result(self, idem_key: str, result: str) -> None:
        self._run("INSERT OR REPLACE INTO tool_log (idem_key, result, created) VALUES (?,?,?)",
                  (idem_key, result, time.time()))

    def save_checkpoint(self, turn_id: str, step: int, state: dict[str, Any]) -> None:
        self._run("INSERT OR REPLACE INTO checkpoints (turn_id, step, state, created) VALUES (?,?,?,?)",
                  (turn_id, step, json.dumps(state, ensure_ascii=False), time.time()))

    def last_checkpoint(self, turn_id: str) -> tuple[int, dict[str, Any]] | None:
        rows = self._rows("SELECT step, state FROM checkpoints WHERE turn_id=? ORDER BY step DESC LIMIT 1", (turn_id,))
        return (rows[0]["step"], json.loads(rows[0]["state"])) if rows else None

    def clear_checkpoints(self, turn_id: str) -> None:
        self._run("DELETE FROM checkpoints WHERE turn_id=?", (turn_id,))

    # ------------------------------------------------ housekeeping
    # ---------------------------------------------------------------- scheduled tasks
    def add_task(self, prompt: str, schedule: str, next_run: float | None, origin: str = "") -> int:
        return self._run("INSERT INTO tasks (prompt, schedule, next_run, origin, created) VALUES (?,?,?,?,?)",
                         (prompt, schedule, next_run, origin, time.time())).lastrowid

    def tasks(self) -> list[dict[str, Any]]:
        return self._rows("SELECT * FROM tasks ORDER BY id")

    def task(self, task_id: int) -> dict[str, Any] | None:
        rows = self._rows("SELECT * FROM tasks WHERE id=?", (task_id,))
        return rows[0] if rows else None

    def due_tasks(self, now: float) -> list[dict[str, Any]]:
        return self._rows("SELECT * FROM tasks WHERE enabled=1 AND next_run IS NOT NULL AND next_run<=? ORDER BY next_run", (now,))

    def claim_task(self, task_id: int, expected_next: float, new_next: float | None) -> bool:
        """Move a due task's next run forward, but only if nobody else already did.
        Two processes (the web server and a chat gateway) may both be watching the
        clock; this compare-and-set is what stops a task from running twice."""
        return self._run("UPDATE tasks SET next_run=? WHERE id=? AND next_run=?", (new_next, task_id, expected_next)).rowcount == 1

    def finish_task(self, task_id: int, status: str, reply: str, turn_id: str, when: float) -> None:
        self._run("UPDATE tasks SET last_run=?, last_status=?, last_reply=?, last_turn=?, runs=runs+1 WHERE id=?",
                  (when, status, reply[:4000], turn_id, task_id))

    def set_task(self, task_id: int, *, enabled: bool | None = None, next_run: float | None = None) -> bool:
        if enabled is not None:
            self._run("UPDATE tasks SET enabled=? WHERE id=?", (int(enabled), task_id))
        if next_run is not None:
            self._run("UPDATE tasks SET next_run=? WHERE id=?", (next_run, task_id))
        return self.task(task_id) is not None

    def delete_task(self, task_id: int) -> bool:
        return self._run("DELETE FROM tasks WHERE id=?", (task_id,)).rowcount == 1

    def add_delivery(self, origin: str, body: str) -> int:
        return self._run("INSERT INTO deliveries (origin, body, created) VALUES (?,?,?)", (origin, body, time.time())).lastrowid

    def take_deliveries(self, channel: str) -> list[dict[str, Any]]:
        """Results waiting for one chat channel. Each row is handed out once."""
        with self._lock:
            rows = self._rows("SELECT * FROM deliveries WHERE sent=0 AND origin LIKE ? ORDER BY id", (channel + ":%",))
            for row in rows:
                self._run("UPDATE deliveries SET sent=1 WHERE id=?", (row["id"],))
        return rows

    def counts(self) -> dict[str, int]:
        tables = ("messages", "facts", "episodes", "events_cal", "notes", "outbox", "tasks")
        out = {t: self._rows(f"SELECT COUNT(*) AS n FROM {t}")[0]["n"] for t in tables}
        out["facts_live"] = self._rows("SELECT COUNT(*) AS n FROM facts WHERE superseded_by IS NULL")[0]["n"]
        return out

    def reset(self) -> None:
        """Wipe everything. Only ever called on an explicit user request."""
        with self._lock:
            for table in ("messages", "sessions", "facts", "episodes", "events_cal", "notes",
                          "outbox", "tool_log", "checkpoints", "tasks", "deliveries"):
                self._db.execute(f"DELETE FROM {table}")
            self._db.commit()
