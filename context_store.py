import sqlite3
import time
from pathlib import Path

import config

_DB_PATH = config.MEMORY_DIR / "context.db"
# Was 200, about a week, and nothing could search it. Raised for search_history
# (2026-10-16): rows are capped at 20000 chars by the orchestrator, so even a full
# table is well under a few hundred MB, against 39 GB free, and a LIKE scan of it
# is milliseconds. Prompts never see more than CONTEXT_WINDOW of it. Age is the real
# limit (config.HISTORY_RETENTION_DAYS); the count is a backstop.
_MAX_STORED = 10000


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


def load_timed(limit: int = config.CONTEXT_WINDOW) -> list[dict]:
    """As load(), with each message's unix time under "ts"."""
    try:
        with _conn() as conn:
            rows = conn.execute(
                "SELECT role, content, ts FROM messages ORDER BY id DESC LIMIT ?",
                (limit,),
            ).fetchall()
        return [{"role": r, "content": c, "ts": t} for r, c, t in reversed(rows)]
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
                "DELETE FROM messages WHERE ts < ?",
                (int(time.time()) - config.HISTORY_RETENTION_DAYS * 86400,),
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


def search(query: str, limit: int = 8, every: bool = True) -> list[tuple[int, int, str, str]]:
    """Messages containing every word of the query, newest first: (id, ts, role, content).
    every=False: those containing at least two of the words (one, for a two-word query),
    most words first - for when no message holds them all.

    A LIKE scan rather than Hermes' FTS5: at 10000 rows it is milliseconds, needs no
    index to keep in step, and matches word fragments ("deploy" finds "deployed").
    """
    words = [w for w in (query or "").lower().split() if w][:6]
    if not words:
        return []
    escaped = [w.replace("!", "!!").replace("%", "!%").replace("_", "!_") for w in words]
    hits = ["(lower(content) LIKE ? ESCAPE '!')" for _ in escaped]
    params = [f"%{w}%" for w in escaped]
    if every:
        sql = f"SELECT id, ts, role, content FROM messages WHERE {' AND '.join(hits)} ORDER BY id DESC LIMIT ?"
        args = params + [limit]
    else:
        score = " + ".join(hits)
        sql = (f"SELECT id, ts, role, content FROM messages WHERE ({score}) >= ? "
               f"ORDER BY ({score}) DESC, id DESC LIMIT ?")
        args = params + [1 if len(words) <= 2 else 2] + params + [limit]
    try:
        with _conn() as conn:
            return conn.execute(sql, args).fetchall()
    except Exception:
        return []


def around(msg_id: int, window: int = 2) -> list[tuple[int, int, str, str]]:
    """(id, ts, role, content) from `window` messages before msg_id to `window` after."""
    try:
        with _conn() as conn:
            return conn.execute(
                "SELECT id, ts, role, content FROM messages WHERE id BETWEEN ? AND ? ORDER BY id",
                (msg_id - window, msg_id + window),
            ).fetchall()
    except Exception:
        return []


def rows_after(after_id: int) -> list[tuple[int, str, str, int]]:
    """(id, role, content, ts) for every message after after_id, oldest first."""
    try:
        with _conn() as conn:
            return conn.execute(
                "SELECT id, role, content, ts FROM messages WHERE id > ? ORDER BY id",
                (after_id,),
            ).fetchall()
    except Exception:
        return []


def max_id() -> int:
    try:
        with _conn() as conn:
            row = conn.execute("SELECT MAX(id) FROM messages").fetchone()
        return int(row[0] or 0)
    except Exception:
        return 0


def clear() -> None:
    try:
        with _conn() as conn:
            conn.execute("DELETE FROM messages")
    except Exception:
        pass
