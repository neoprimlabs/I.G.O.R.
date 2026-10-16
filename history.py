"""What the watchers saw, kept over time, in memory/history.db.

The watchers kept only their latest state, so "when was this last in stock?" or "is
this a good price for it?" had no answer: every check overwrote the one before.
This keeps one row per observation. Collection is code; no model call writes here.

Only successful checks are recorded. A failed fetch writes nothing, for the reason
the stock watch already carries its old state forward: no data is not "unavailable",
and a history that says otherwise invents stock-outs that never happened.

Small by construction - about 130 rows a day at the current watch rates - and
backed up with the rest of memory/ by scripts/backup_memory.ps1.
"""

import logging
import sqlite3
import time
from datetime import datetime, timezone
from typing import Optional

import config

logger = logging.getLogger(__name__)


def _conn() -> sqlite3.Connection:
    conn = sqlite3.connect(str(config.MEMORY_DIR / "history.db"))
    conn.execute("""
        CREATE TABLE IF NOT EXISTS observations (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            observed_at INTEGER NOT NULL,
            source TEXT NOT NULL,
            item TEXT NOT NULL,
            field TEXT NOT NULL,
            value TEXT NOT NULL,
            url TEXT NOT NULL DEFAULT ''
        )
    """)
    conn.execute("CREATE INDEX IF NOT EXISTS obs_item ON observations (item, field, observed_at)")
    return conn


def record(source: str, rows: list[tuple[str, str, object, str]],
           now: Optional[datetime] = None) -> None:
    """Add (item, field, value, url) rows from one check. Never raises: losing a
    history row must not cost the user an alert."""
    if not rows:
        return
    ts = int((now or datetime.now(timezone.utc)).timestamp())
    try:
        with _conn() as conn:
            conn.executemany(
                "INSERT INTO observations (observed_at, source, item, field, value, url) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                [(ts, source, item, field, str(value), url or "") for item, field, value, url in rows])
    except Exception as e:
        logger.warning("History not recorded for %s - %s: %s", source, type(e).__name__, e)


def changes(query: str, days: int = 30, now: Optional[datetime] = None,
            limit: int = 40) -> list[tuple[int, str, str, str, str, str]]:
    """Rows whose item contains every word of query, over the last `days`, keeping
    only the first observation and each change of value per (item, field) - an
    hourly stock check that says "available" all week is one row, not 168.
    Newest first: (observed_at, source, item, field, value, url)."""
    words = [w for w in (query or "").lower().split() if w][:6]
    if not words:
        return []
    since = int((now or datetime.now(timezone.utc)).timestamp()) - max(1, int(days)) * 86400
    where = " AND ".join("lower(item) LIKE ? ESCAPE '!'" for _ in words)
    args = [f"%{w.replace('!', '!!').replace('%', '!%').replace('_', '!_')}%" for w in words]
    try:
        with _conn() as conn:
            rows = conn.execute(
                f"SELECT observed_at, source, item, field, value, url FROM observations "
                f"WHERE observed_at >= ? AND {where} ORDER BY observed_at, id",
                [since] + args).fetchall()
    except Exception as e:
        logger.warning("History not readable - %s: %s", type(e).__name__, e)
        return []
    last: dict[tuple[str, str], str] = {}
    kept = []
    for row in rows:
        key = (row[2], row[3])
        if last.get(key) != row[4]:
            kept.append(row)
            last[key] = row[4]
    return list(reversed(kept))[:limit]


def describe(query: str, days: int = 30) -> str:
    """React's watch_history tool."""
    import clock
    rows = changes(query, days)
    if not rows:
        return (f"No watch history for {query!r} in the last {days} days. History starts "
                "when a watcher first records an item, so older checks are not in it.")
    out = [f"Watch history for {query!r}, last {days} days, changes only, newest first:"]
    for ts, source, item, field, value, url in rows:
        when = datetime.fromtimestamp(ts, timezone.utc).astimezone(clock.USER_TZ)
        shown = {"1": "yes", "0": "no"}.get(value, value) if field == "available" else value
        note = " (keyword match, not checked as the right product)" if field == "cheapest_match" else ""
        out.append(f"{when:%a %Y-%m-%d %H:%M}  {source}  {item}  {field}={shown}{note}"
                   + (f"  {url}" if url and source != "stock" else ""))
    return "\n".join(out)
