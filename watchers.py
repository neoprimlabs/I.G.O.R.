"""The plumbing every watcher needs: what has been seen, and proof of life.

Three features poll a source, drop what they have already reported, and notify -
AI news, job postings, price drops. Each had grown its own copy of the same two
things: a JSON file of URLs with timestamps and a prune, and nothing at all for
"still running, nothing to report". The third copy went in without noticing the
second, which is how duplication usually arrives.

The second half matters more than the first. A watcher that only speaks when it has
something cannot be told apart from a watcher that has died. The job watch sent
nothing for five days in September and neither the user nor I noticed until a
question arrived that it could not answer. Silence has to be a message.

Stores keep their existing filenames so nothing is lost on deploy.
"""

import json
import logging
import os
from datetime import datetime, timedelta, timezone
from typing import Optional

import config

logger = logging.getLogger(__name__)

DEFAULT_DAYS = 30
DEFAULT_HEARTBEAT_DAYS = 7

# Bookkeeping lives in the same file as the seen urls, so it needs a name no url can
# collide with and that never leaks back out as something already reported.
_HEARTBEAT_KEY = "last_heartbeat"


def _path(name: str):
    return config.MEMORY_DIR / name


def _write(name: str, data: dict) -> None:
    """Write then rename: a crash mid-write leaves the old file, not half of one."""
    try:
        path = _path(name)
        tmp = path.with_name(path.name + ".tmp")
        tmp.write_text(json.dumps(data, indent=2), encoding="utf-8")
        os.replace(tmp, path)
    except OSError as e:
        logger.error("Could not write %s - %s: %s", name, type(e).__name__, e)


def _read(name: str) -> dict:
    try:
        data = json.loads(_path(name).read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def load_seen(name: str) -> dict:
    """URLs already reported, as {url: iso timestamp}. Unreadable reads as empty."""
    return {k: v for k, v in _read(name).items() if k != _HEARTBEAT_KEY}


def remember(name: str, urls, now: Optional[datetime] = None,
             days: int = DEFAULT_DAYS) -> None:
    """Record what was reported and drop anything past the window.

    Pruning on write rather than on read keeps the file from growing forever without
    needing a separate job, and lets a story or posting come back legitimately once
    the cycle has moved on.
    """
    now = now or datetime.now(timezone.utc)
    horizon = now - timedelta(days=days)
    kept = {}
    for url, stamp in load_seen(name).items():
        try:
            if datetime.fromisoformat(stamp) >= horizon:
                kept[url] = stamp
        except (TypeError, ValueError):
            continue
    for url in urls:
        if url:
            kept[url] = now.isoformat()
    existing = _read(name)
    if _HEARTBEAT_KEY in existing:
        kept[_HEARTBEAT_KEY] = existing[_HEARTBEAT_KEY]
    _write(name, kept)


def heartbeat_due(name: str, now: Optional[datetime] = None,
                  days: int = DEFAULT_HEARTBEAT_DAYS) -> bool:
    """Has it been long enough with nothing to report that silence needs explaining?"""
    now = now or datetime.now(timezone.utc)
    last = _read(name).get(_HEARTBEAT_KEY)
    if last is None:
        return True
    try:
        return now - datetime.fromisoformat(last) >= timedelta(days=days)
    except (TypeError, ValueError):
        return True


def heartbeat_sent(name: str, now: Optional[datetime] = None) -> None:
    now = now or datetime.now(timezone.utc)
    state = _read(name)
    state[_HEARTBEAT_KEY] = now.isoformat()
    _write(name, state)
