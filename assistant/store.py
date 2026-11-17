"""Conversation store: sessions, turns, feedback, unanswered backlog.

Every assistant turn is persisted. That is not instrumentation for a demo — it is what
makes the assistant operable: you can replay a session, see which questions the KB could
not answer, and feed those back into the corpus.
"""

from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterator
from uuid import uuid4

SCHEMA = """
CREATE TABLE IF NOT EXISTS sessions (
    id TEXT PRIMARY KEY,
    tenant TEXT NOT NULL,
    subject TEXT NOT NULL,
    workspace TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    turns INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS messages (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id TEXT NOT NULL,
    role TEXT NOT NULL,
    content TEXT NOT NULL,
    intent TEXT,
    standalone_question TEXT,
    answer_status TEXT,
    citations TEXT,
    latency_ms REAL,
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_messages_session ON messages(session_id, id);
CREATE TABLE IF NOT EXISTS feedback (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    message_id INTEGER NOT NULL,
    value TEXT NOT NULL,
    comment TEXT,
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS unresolved (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id TEXT NOT NULL,
    identity TEXT NOT NULL,
    workspace TEXT NOT NULL,
    question TEXT NOT NULL,
    rewritten TEXT,
    reason TEXT NOT NULL,
    created_at TEXT NOT NULL,
    resolved_at TEXT
);
CREATE INDEX IF NOT EXISTS idx_unresolved_open ON unresolved(resolved_at, id);
"""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class Store:
    def __init__(self, path: Path | str) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.connection() as conn:
            conn.executescript(SCHEMA)

    @contextmanager
    def connection(self) -> Iterator[sqlite3.Connection]:
        conn = sqlite3.connect(self.path, timeout=10)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        try:
            yield conn
            conn.commit()
        finally:
            conn.close()

    # -- sessions ---------------------------------------------------------
    def create_session(self, tenant: str, subject: str, workspace: str) -> str:
        session_id = uuid4().hex[:12]
        stamp = _now()
        with self.connection() as conn:
            conn.execute(
                "INSERT INTO sessions (id, tenant, subject, workspace, created_at, updated_at)"
                " VALUES (?, ?, ?, ?, ?, ?)",
                (session_id, tenant, subject, workspace, stamp, stamp),
            )
        return session_id

    def session(self, session_id: str) -> dict | None:
        with self.connection() as conn:
            row = conn.execute("SELECT * FROM sessions WHERE id = ?", (session_id,)).fetchone()
        return dict(row) if row else None

    def touch(self, session_id: str) -> None:
        with self.connection() as conn:
            conn.execute(
                "UPDATE sessions SET updated_at = ?, turns = turns + 1 WHERE id = ?",
                (_now(), session_id),
            )

    # -- turns ------------------------------------------------------------
    def add_message(
        self,
        session_id: str,
        role: str,
        content: str,
        *,
        intent: str | None = None,
        standalone_question: str | None = None,
        answer_status: str | None = None,
        citations: list[dict] | None = None,
        latency_ms: float | None = None,
    ) -> int:
        with self.connection() as conn:
            cursor = conn.execute(
                "INSERT INTO messages (session_id, role, content, intent, standalone_question,"
                " answer_status, citations, latency_ms, created_at)"
                " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    session_id,
                    role,
                    content,
                    intent,
                    standalone_question,
                    answer_status,
                    json.dumps(citations, ensure_ascii=False) if citations is not None else None,
                    latency_ms,
                    _now(),
                ),
            )
            return int(cursor.lastrowid)

    def history(self, session_id: str, limit: int = 8) -> list[dict]:
        with self.connection() as conn:
            rows = conn.execute(
                "SELECT role, content FROM messages WHERE session_id = ?"
                " ORDER BY id DESC LIMIT ?",
                (session_id, limit),
            ).fetchall()
        return [{"role": r["role"], "content": r["content"]} for r in reversed(rows)]

    def transcript(self, session_id: str) -> list[dict]:
        with self.connection() as conn:
            rows = conn.execute(
                "SELECT id, role, content, intent, standalone_question, answer_status,"
                " citations, latency_ms, created_at FROM messages WHERE session_id = ?"
                " ORDER BY id",
                (session_id,),
            ).fetchall()
        out = []
        for row in rows:
            item = dict(row)
            if item.get("citations"):
                item["citations"] = json.loads(item["citations"])
            out.append(item)
        return out

    # -- operations -------------------------------------------------------
    def add_feedback(self, message_id: int, value: str, comment: str | None = None) -> int:
        with self.connection() as conn:
            cursor = conn.execute(
                "INSERT INTO feedback (message_id, value, comment, created_at) VALUES (?, ?, ?, ?)",
                (message_id, value, comment, _now()),
            )
            return int(cursor.lastrowid)

    def add_unresolved(
        self,
        session_id: str,
        identity: str,
        workspace: str,
        question: str,
        reason: str,
        rewritten: str | None = None,
    ) -> int:
        with self.connection() as conn:
            cursor = conn.execute(
                "INSERT INTO unresolved (session_id, identity, workspace, question, rewritten,"
                " reason, created_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
                (session_id, identity, workspace, question, rewritten, reason, _now()),
            )
            return int(cursor.lastrowid)

    def unresolved(self, limit: int = 50, include_resolved: bool = False) -> list[dict]:
        clause = "" if include_resolved else " WHERE resolved_at IS NULL"
        with self.connection() as conn:
            rows = conn.execute(
                f"SELECT * FROM unresolved{clause} ORDER BY id DESC LIMIT ?", (limit,)
            ).fetchall()
        return [dict(r) for r in rows]

    def stats(self) -> dict:
        with self.connection() as conn:
            def count(sql: str) -> int:
                return int(conn.execute(sql).fetchone()[0])

            return {
                "sessions": count("SELECT COUNT(*) FROM sessions"),
                "messages": count("SELECT COUNT(*) FROM messages"),
                "feedback_positive": count("SELECT COUNT(*) FROM feedback WHERE value = 'up'"),
                "feedback_negative": count("SELECT COUNT(*) FROM feedback WHERE value = 'down'"),
                "unresolved_open": count("SELECT COUNT(*) FROM unresolved WHERE resolved_at IS NULL"),
            }
