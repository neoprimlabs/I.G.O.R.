"""The overnight work folder, the per-night log and the budget.

memory/work/<goal id>/ holds what IGOR produces for one goal; memory/work/log.json
records every session so the loop can tell what a night has already spent and why it
stopped. Nothing here raises into the caller on a disk error except folder()'s
ValueError: an unreadable log counts as empty, a failed write is logged and dropped.
"""

import json
import logging
import os
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

import clock
import config

logger = logging.getLogger(__name__)

_MAX_ENTRIES = 300
_WINDOW_END_HOUR = 7


def _root() -> Path:
    return config.MEMORY_DIR / "work"


def _log_path() -> Path:
    return _root() / "log.json"


def folder(goal_id: str) -> Path:
    safe = re.sub(r"[^a-z0-9_-]", "", (goal_id or "").lower())
    if not safe:
        raise ValueError(f"unusable goal id {goal_id!r}")
    return _root() / safe


def _local(now: datetime) -> datetime:
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)
    return now.astimezone(clock.USER_TZ)


def night_of(now: datetime) -> str:
    return _local(now).date().isoformat()


def in_window(now: datetime) -> bool:
    return _local(now).hour < _WINDOW_END_HOUR


def _load() -> list[dict]:
    path = _log_path()
    if not path.exists():
        return []
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as e:
        logger.warning("Work log unreadable, treating as empty: %s", e)
        return []
    if not isinstance(data, list):
        logger.warning("Work log is not a list, treating as empty")
        return []
    return [e for e in data if isinstance(e, dict)]


def log_session(goal_id: str, kind: str, summary: str, tokens: int, searches: int,
                files: list[str], finished: list[str], stopped: str = "",
                now: Optional[datetime] = None) -> None:
    now = now or datetime.now(timezone.utc)
    entries = _load()
    entries.append({"night": night_of(now), "at": now.isoformat(), "goal": goal_id,
                    "kind": kind, "summary": summary, "tokens": tokens,
                    "searches": searches, "files": list(files),
                    "finished": list(finished), "stopped": stopped})
    path = _log_path()
    tmp = path.with_name(path.name + ".tmp")
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp.write_text(json.dumps(entries[-_MAX_ENTRIES:], indent=1), encoding="utf-8")
        os.replace(tmp, path)
    except OSError as e:
        logger.warning("Could not write work log: %s", e)


def spent(night: str) -> tuple[int, int]:
    mine = [e for e in _load() if e.get("night") == night]
    return (sum(int(e.get("tokens") or 0) for e in mine),
            sum(int(e.get("searches") or 0) for e in mine))


def stopped(night: str) -> str:
    for e in _load():
        if e.get("night") == night and e.get("stopped"):
            return str(e["stopped"])
    return ""


def overnight_tokens() -> int:
    import usage
    return usage.last_24h().get(config.OVERNIGHT_MODEL, {}).get("Overnight", 0)
