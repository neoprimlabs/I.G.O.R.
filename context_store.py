import sqlite3
import time
from pathlib import Path

import config

_DB_PATH = config.MEMORY_DIR / "context.db"
_MAX_STORED = 200


def _conn() -> sqlite3.Connection:
    conn = sqlite3.connect(str(_DB_PATH))
    conn.execute("""
        CREATE TABLE IF NOT EXISTS messages (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            role TEXT NOT NULL,
            content TEXT NOT NULL,
            ts INTEGER NOT NULL
        )
    """)
    conn.commit()
    return conn


def load(limit: int = config.CONTEXT_WINDOW) -> list[dict]:
    try:
        with _conn() as conn:
            rows = conn.execute(
                "SELECT role, content FROM messages ORDER BY id DESC LIMIT ?",
                (limit,),
            ).fetchall()
        return [{"role": r, "content": c} for r, c in reversed(rows)]
    except Exception:
        return []


def last_timestamp() -> "datetime.datetime | None":
    """When anything was last stored, IGOR's own proactive sends included."""
    import datetime
    try:
        with _conn() as conn:
            row = conn.execute("SELECT ts FROM messages ORDER BY id DESC LIMIT 1").fetchone()
    except Exception:
        return None
    if not row:
        return None
    return datetime.datetime.fromtimestamp(row[0], datetime.timezone.utc)


def last_user_timestamp() -> "datetime.datetime | None":
    """When the USER last said something.

    Distinct from last_timestamp on purpose. record_outbound stores the digest as an
    assistant turn, so on 2026-09-17 the 13:00 digest read as "a conversation just
    ended" and presence chased a lull for an hour. A lull is about the person.
    """
    import datetime
    try:
        with _conn() as conn:
            row = conn.execute(
                "SELECT ts FROM messages WHERE role = 'user' ORDER BY id DESC LIMIT 1"
            ).fetchone()
    except Exception:
        return None
    if not row:
        return None
    return datetime.datetime.fromtimestamp(row[0], datetime.timezone.utc)


def append(role: str, content: str) -> None:
    try:
        with _conn() as conn:
            conn.execute(
                "INSERT INTO messages (role, content, ts) VALUES (?, ?, ?)",
                (role, content, int(time.time())),
            )
            conn.execute(
                "DELETE FROM messages WHERE id NOT IN "
                "(SELECT id FROM messages ORDER BY id DESC LIMIT ?)",
                (_MAX_STORED,),
            )
    except Exception:
        pass


def last_timestamp():
    """When the last message was stored, or None.

    Presence uses this to tell a live conversation from a break. load() returns
    roles and text only, and the difference between "eight minutes ago" and "forty
    minutes ago" is the difference between interrupting and not.
    """
    from datetime import datetime, timezone
    try:
        with _conn() as conn:
            row = conn.execute("SELECT ts FROM messages ORDER BY id DESC LIMIT 1").fetchone()
        return datetime.fromtimestamp(row[0], timezone.utc) if row else None
    except Exception:
        return None


def clear() -> None:
    try:
        with _conn() as conn:
            conn.execute("DELETE FROM messages")
    except Exception:
        pass
