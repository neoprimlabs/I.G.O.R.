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


# 2026-11-09, asked how the watches were doing, React called this with "price watch" and "stock watch".
# Those words are in no item's name, so both came back empty, and the user was told neither watch had
# found anything while the dock had been in stock all week. A watcher's own name now means its rows,
# and "watch" itself means nothing.
_GENERIC = frozenset({"watch", "watches", "watcher", "watchers", "watching", "watched", "history",
                      "the", "my", "all", "both"})
_SOURCES = {"price": "price", "prices": "price", "stock": "stock", "restock": "stock", "restocks": "stock",
            "job": "jobs", "jobs": "jobs", "gig": "gigs", "gigs": "gigs"}


def changes(query: str, days: int = 30, now: Optional[datetime] = None,
            limit: int = 40) -> list[tuple[int, str, str, str, str, str]]:
    """Rows whose item contains every word of query, over the last `days`, keeping
    only the first observation and each change of value per (item, field) - an
    hourly stock check that says "available" all week is one row, not 168.
    A watcher's name (price, stock, jobs, gigs) matches that watcher's rows; a query
    of only "watch" words means the price and stock watches.
    Newest first: (observed_at, source, item, field, value, url)."""
    words = [w for w in (query or "").lower().split() if w and w not in _GENERIC][:6]
    since = int((now or datetime.now(timezone.utc)).timestamp()) - max(1, int(days)) * 86400
    clauses, args = [], []
    for w in words:
        like = f"%{w.replace('!', '!!').replace('%', '!%').replace('_', '!_')}%"
        if w in _SOURCES:
            clauses.append("(source = ? OR lower(item) LIKE ? ESCAPE '!')")
            args += [_SOURCES[w], like]
        else:
            clauses.append("lower(item) LIKE ? ESCAPE '!'")
            args.append(like)
    where = " AND ".join(clauses) or "source IN ('price', 'stock')"
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


def _last_checks(sources: list[str]) -> list[tuple[str, int]]:
    if not sources:
        return []
    try:
        with _conn() as conn:
            return conn.execute(
                f"SELECT source, max(observed_at) FROM observations WHERE source IN "
                f"({', '.join('?' for _ in sources)}) GROUP BY source ORDER BY source", sources).fetchall()
    except Exception as e:
        logger.warning("History not readable - %s: %s", type(e).__name__, e)
        return []


def _on_record(days: int) -> str:
    """What the watchers did record in the window, so a query that missed can be asked again."""
    since = int(time.time()) - max(1, int(days)) * 86400
    try:
        with _conn() as conn:
            rows = conn.execute(
                "SELECT source, item FROM observations WHERE observed_at >= ? GROUP BY source, item "
                "ORDER BY source, item", [since]).fetchall()
    except Exception as e:
        logger.warning("History not readable - %s: %s", type(e).__name__, e)
        return "The history could not be read."
    if not rows:
        return ("The watchers recorded nothing in that time. History starts when a watcher first "
                "records an item, so older checks are not in it.")
    named = {}
    for source, item in rows:
        named.setdefault(source, []).append(item)
    parts = []
    for source, items in named.items():
        if source in ("price", "stock"):
            parts.append(f"{source}: {'; '.join(items)}")
        else:
            parts.append(f"{source}: {len(items)} posting{'s' if len(items) != 1 else ''}")
    return ("In that time the watchers recorded - " + ". ".join(parts)
            + ". Ask again with words from one of those names, or with price, stock, jobs or gigs.")


def describe(query: str, days: int = 30) -> str:
    """React's watch_history tool."""
    import clock
    rows = changes(query, days)
    if not rows:
        return (f"No watch history matches {query!r} in the last {days} days. That is not the same as "
                f"the watches finding nothing. {_on_record(days)}")
    out = [f"Watch history for {query!r}, last {days} days, changes only, newest first:"]
    for ts, source, item, field, value, url in rows:
        when = datetime.fromtimestamp(ts, timezone.utc).astimezone(clock.USER_TZ)
        shown = {"1": "yes", "0": "no"}.get(value, value) if field == "available" else value
        note = " (keyword match, not checked as the right product)" if field == "cheapest_match" else ""
        out.append(f"{when:%a %Y-%m-%d %H:%M}  {source}  {item}  {field}={shown}{note}"
                   + (f"  {url}" if url and source != "stock" else ""))
    # Changes only, so a dock in stock all week shows its first row in the window as the newest. On
    # 2026-11-09 that read as the watches last running two days before; they had run that evening.
    last = _last_checks(sorted({row[1] for row in rows} & {"price", "stock"}))
    if last:
        out.append("Latest check recorded, unchanged since the rows above: " + "; ".join(
            f"{source} {datetime.fromtimestamp(ts, timezone.utc).astimezone(clock.USER_TZ):%a %Y-%m-%d %H:%M}"
            for source, ts in last))
    # So a quiet watch can be explained from the rows instead of guessed at: on 2026-11-03 a
    # dock in stock at one price for three weeks, alerted once, read as a watch gone wrong.
    if any(row[1] in ("stock", "price") for row in rows):
        out.append("When they message the user: the stock watch only when an item becomes buyable "
                   "within its price ceiling, so availability that has stayed yes has sent nothing "
                   "since it turned yes. The price watch for a posting under target from the last 14 "
                   "days; the cheapest match here can be older than that.")
    return "\n".join(out)
