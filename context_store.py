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


def clear() -> None:
    try:
        with _conn() as conn:
            conn.execute("DELETE FROM messages")
    except Exception:
        pass
