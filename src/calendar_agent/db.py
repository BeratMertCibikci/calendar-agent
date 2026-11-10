"""SQLite persistence: tasks, preferences, conversation history, transcript,
pending approvals and one-shot notes for the model.

One short-lived connection per call keeps this safe under Flask's threaded
server; WAL mode lets readers and a writer coexist.

The table and column names of ``tasks``, ``memory`` and ``conversations`` match
the first prototype, so an old ``asistan.db`` can simply be renamed to
``data/agent.db``.
"""
from __future__ import annotations

import contextlib
import json
import sqlite3
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Optional

SCHEMA = """
CREATE TABLE IF NOT EXISTS tasks (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    text TEXT NOT NULL,
    done INTEGER DEFAULT 0,
    created TEXT
);
CREATE TABLE IF NOT EXISTS memory (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    fact TEXT NOT NULL,
    created TEXT
);
CREATE TABLE IF NOT EXISTS conversations (
    session_id TEXT NOT NULL,
    engine TEXT NOT NULL,
    history TEXT NOT NULL,
    updated TEXT NOT NULL,
    PRIMARY KEY (session_id, engine)
);
CREATE TABLE IF NOT EXISTS transcript (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id TEXT NOT NULL,
    role TEXT NOT NULL,
    text TEXT NOT NULL,
    has_image INTEGER DEFAULT 0,
    created TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_transcript_session ON transcript(session_id, id);
CREATE TABLE IF NOT EXISTS pending_actions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id TEXT NOT NULL,
    tool TEXT NOT NULL,
    args TEXT NOT NULL,
    summary TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'pending',
    result TEXT,
    created REAL NOT NULL,
    resolved REAL
);
CREATE TABLE IF NOT EXISTS notes (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id TEXT NOT NULL,
    text TEXT NOT NULL
);
"""

MAX_PREFERENCES = 50
MAX_PREFERENCE_LENGTH = 300


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


class Store:
    def __init__(self, path, action_ttl: int = 1800, clock: Callable[[], float] = time.time):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.action_ttl = action_ttl
        self.clock = clock
        with self._conn() as con:
            con.execute("PRAGMA journal_mode=WAL")
            con.executescript(SCHEMA)

    @contextlib.contextmanager
    def _conn(self):
        con = sqlite3.connect(self.path, timeout=10)
        con.row_factory = sqlite3.Row
        try:
            yield con
            con.commit()
        except Exception:
            con.rollback()
            raise
        finally:
            con.close()

    # ------------------------------------------------------------------ tasks
    def add_task(self, text: str) -> int:
        with self._conn() as con:
            cur = con.execute("INSERT INTO tasks (text, created) VALUES (?, ?)", (text, _now_iso()))
            return int(cur.lastrowid)

    def list_tasks(self, include_done: bool = False) -> list[dict]:
        sql = "SELECT id, text, done FROM tasks" + ("" if include_done else " WHERE done = 0") + " ORDER BY id"
        with self._conn() as con:
            return [dict(r) for r in con.execute(sql)]

    def complete_task(self, task_id: int) -> bool:
        with self._conn() as con:
            cur = con.execute("UPDATE tasks SET done = 1 WHERE id = ? AND done = 0", (task_id,))
            return cur.rowcount == 1

    # ------------------------------------------------------------- preferences
    def add_preference(self, fact: str) -> int:
        fact = " ".join(fact.split())
        if not fact:
            raise ValueError("Preference text is empty.")
        if len(fact) > MAX_PREFERENCE_LENGTH:
            raise ValueError(f"Preference is too long (max {MAX_PREFERENCE_LENGTH} characters).")
        with self._conn() as con:
            count = con.execute("SELECT COUNT(*) FROM memory").fetchone()[0]
            if count >= MAX_PREFERENCES:
                raise ValueError(f"Preference limit reached ({MAX_PREFERENCES}). Forget one first.")
            cur = con.execute("INSERT INTO memory (fact, created) VALUES (?, ?)", (fact, _now_iso()))
            return int(cur.lastrowid)

    def list_preferences(self) -> list[dict]:
        with self._conn() as con:
            return [dict(r) for r in con.execute("SELECT id, fact FROM memory ORDER BY id")]

    def delete_preference(self, pref_id: int) -> bool:
        with self._conn() as con:
            return con.execute("DELETE FROM memory WHERE id = ?", (pref_id,)).rowcount == 1

    # ----------------------------------------------------------- conversations
    def load_history(self, session_id: str, engine: str) -> list:
        """Engine-specific message history. Corrupt or missing data yields an empty history."""
        with self._conn() as con:
            row = con.execute(
                "SELECT history FROM conversations WHERE session_id = ? AND engine = ?",
                (session_id, engine),
            ).fetchone()
        if not row:
            return []
        try:
            data = json.loads(row["history"])
        except (TypeError, ValueError):
            return []
        return data if isinstance(data, list) else []

    def save_history(self, session_id: str, engine: str, history: list) -> None:
        payload = json.dumps(history, ensure_ascii=False, default=str)
        with self._conn() as con:
            con.execute(
                """INSERT INTO conversations (session_id, engine, history, updated) VALUES (?, ?, ?, ?)
                   ON CONFLICT(session_id, engine) DO UPDATE
                   SET history = excluded.history, updated = excluded.updated""",
                (session_id, engine, payload, _now_iso()),
            )

    # -------------------------------------------------------------- transcript
    def add_transcript(self, session_id: str, role: str, text: str, has_image: bool = False) -> None:
        with self._conn() as con:
            con.execute(
                "INSERT INTO transcript (session_id, role, text, has_image, created) VALUES (?, ?, ?, ?, ?)",
                (session_id, role, text, int(has_image), _now_iso()),
            )

    def get_transcript(self, session_id: str, limit: int = 200) -> list[dict]:
        with self._conn() as con:
            rows = con.execute(
                "SELECT role, text, has_image FROM transcript WHERE session_id = ? ORDER BY id DESC LIMIT ?",
                (session_id, limit),
            ).fetchall()
        return [dict(r) for r in reversed(rows)]

    # ----------------------------------------------------------------- actions
    def _expire_stale(self, con: sqlite3.Connection) -> None:
        now = self.clock()
        con.execute(
            "UPDATE pending_actions SET status = 'expired', resolved = ? WHERE status = 'pending' AND created < ?",
            (now, now - self.action_ttl),
        )

    def create_action(self, session_id: str, tool: str, args: dict, summary: str) -> int:
        with self._conn() as con:
            cur = con.execute(
                "INSERT INTO pending_actions (session_id, tool, args, summary, created) VALUES (?, ?, ?, ?, ?)",
                (session_id, tool, json.dumps(args, ensure_ascii=False), summary, self.clock()),
            )
            return int(cur.lastrowid)

    def pending_for_session(self, session_id: str) -> list[dict]:
        with self._conn() as con:
            self._expire_stale(con)
            rows = con.execute(
                "SELECT id, tool, summary FROM pending_actions WHERE session_id = ? AND status = 'pending' ORDER BY id",
                (session_id,),
            ).fetchall()
        return [dict(r) for r in rows]

    def claim_action(self, action_id: int, session_id: str) -> Optional[dict[str, Any]]:
        """Atomically move a pending action to 'executing'. Returns None if it is not
        pending (already handled, expired, or belongs to another session), which is
        what stops double clicks and replays from running a tool twice."""
        with self._conn() as con:
            self._expire_stale(con)
            cur = con.execute(
                "UPDATE pending_actions SET status = 'executing' WHERE id = ? AND session_id = ? AND status = 'pending'",
                (action_id, session_id),
            )
            if cur.rowcount != 1:
                return None
            row = con.execute("SELECT id, tool, args, summary FROM pending_actions WHERE id = ?", (action_id,)).fetchone()
        return {"id": row["id"], "tool": row["tool"], "args": json.loads(row["args"]), "summary": row["summary"]}

    def finish_action(self, action_id: int, status: str, result: str) -> None:
        with self._conn() as con:
            con.execute(
                "UPDATE pending_actions SET status = ?, result = ?, resolved = ? WHERE id = ?",
                (status, result, self.clock(), action_id),
            )

    def reject_action(self, action_id: int, session_id: str) -> Optional[dict[str, Any]]:
        with self._conn() as con:
            self._expire_stale(con)
            row = con.execute(
                "SELECT id, tool, summary FROM pending_actions WHERE id = ? AND session_id = ? AND status = 'pending'",
                (action_id, session_id),
            ).fetchone()
            if not row:
                return None
            con.execute(
                "UPDATE pending_actions SET status = 'rejected', resolved = ? WHERE id = ?",
                (self.clock(), action_id),
            )
        return dict(row)

    def get_action(self, action_id: int) -> Optional[dict]:
        with self._conn() as con:
            row = con.execute("SELECT * FROM pending_actions WHERE id = ?", (action_id,)).fetchone()
        return dict(row) if row else None

    # ------------------------------------------------------------------- notes
    def add_note(self, session_id: str, text: str) -> None:
        with self._conn() as con:
            con.execute("INSERT INTO notes (session_id, text) VALUES (?, ?)", (session_id, text))

    def pop_notes(self, session_id: str) -> list[str]:
        with self._conn() as con:
            rows = con.execute("SELECT id, text FROM notes WHERE session_id = ? ORDER BY id", (session_id,)).fetchall()
            if rows:
                con.execute("DELETE FROM notes WHERE session_id = ?", (session_id,))
        return [r["text"] for r in rows]
