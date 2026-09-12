"""Messages to the user at a later time.

React's scheduled_message tool writes them to memory/scheduled.json, and a monitor
job checks that file every minute and sends whatever is due. The text is fixed when
the message is scheduled, so delivery costs no tokens.

A polled file rather than one APScheduler job per message, because APScheduler
3.11.3 silently drops a date job more than one second late (measured on the server,
2026-09-12). A message due during a deploy would vanish. Here an overdue message is
simply due at the next check, so restarts need no catch-up code.

The model supplies local wall-clock time or a number of minutes. Every conversion
and every addition happens here, in code.
"""

import json
import logging
import os
import secrets
from datetime import datetime, timedelta, timezone
from typing import Awaitable, Callable, Optional

import clock
import config

logger = logging.getLogger(__name__)

_MAX_PENDING = 20
_MAX_AHEAD = timedelta(days=30)
_MAX_ATTEMPTS = 10
_LATE_AFTER = timedelta(minutes=5)
_AT_FORMAT = "%Y-%m-%d %H:%M"


class _Unreadable(Exception):
    pass


def _path():
    return config.MEMORY_DIR / "scheduled.json"


def _load() -> list[dict]:
    path = _path()
    if not path.exists():
        return []
    try:
        entries = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as e:
        raise _Unreadable(f"{type(e).__name__}: {e}") from e
    if not isinstance(entries, list) or not all(
        isinstance(e, dict) and all(isinstance(e.get(k), str) for k in ("id", "due", "content"))
        for e in entries
    ):
        raise _Unreadable("not a list of entries with id, due and content")
    return entries


def _save(entries: list[dict]) -> None:
    # Write then rename, so a crash mid-write leaves the previous file, not half of one.
    path = _path()
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(entries, indent=2), encoding="utf-8")
    os.replace(tmp, path)


def _local(due_iso: str) -> str:
    return f"{datetime.fromisoformat(due_iso).astimezone(clock.USER_TZ):%a %Y-%m-%d %H:%M %Z}"


def add(content: str, at: Optional[str] = None, in_minutes: Optional[int] = None,
        now: Optional[datetime] = None) -> str:
    now = now or datetime.now(timezone.utc)
    content = (content or "").strip()
    if not content:
        return "Not scheduled: the message is empty."
    if (at is None) == (in_minutes is None):
        return "Not scheduled: give exactly one of at (local time, YYYY-MM-DD HH:MM) or in_minutes."

    if at is not None:
        try:
            due = datetime.strptime(at.strip(), _AT_FORMAT).replace(tzinfo=clock.USER_TZ)
        except ValueError:
            return f"Not scheduled: could not read {at!r}. Use local time as YYYY-MM-DD HH:MM."
    elif isinstance(in_minutes, bool) or not isinstance(in_minutes, int) or in_minutes < 1:
        return "Not scheduled: in_minutes must be a whole number of at least 1."
    else:
        due = now + timedelta(minutes=in_minutes)
    due = due.astimezone(timezone.utc).replace(microsecond=0)

    if due <= now:
        return f"Not scheduled: {_local(due.isoformat())} has already passed. It is now {clock.time_line(now)}."
    if due - now > _MAX_AHEAD:
        return "Not scheduled: messages can be scheduled at most 30 days ahead."

    try:
        entries = _load()
    except _Unreadable as e:
        logger.error("scheduled.json is unreadable, refusing to add - %s", e)
        return "Not scheduled: the scheduled message store is unreadable. Nothing was changed."
    if len(entries) >= _MAX_PENDING:
        return f"Not scheduled: {_MAX_PENDING} messages are already pending. Cancel one first."

    entry = {"id": secrets.token_hex(3), "due": due.isoformat(), "content": content,
             "created": now.isoformat(), "attempts": 0}
    entries.append(entry)
    _save(entries)
    logger.info("Scheduled message %s for %s", entry["id"], entry["due"])
    return f"Scheduled {entry['id']} for {_local(entry['due'])}."


def list_pending() -> str:
    try:
        entries = _load()
    except _Unreadable:
        return "The scheduled message store is unreadable."
    if not entries:
        return "No scheduled messages."
    lines = []
    for e in sorted(entries, key=lambda e: e["due"]):
        state = "  FAILED to send - cancel to clear" if e.get("failed") else ""
        lines.append(f"{e['id']}  {_local(e['due'])}{state}  {e['content'][:80]}")
    return "\n".join(lines)


def cancel(entry_id: str) -> str:
    entry_id = (entry_id or "").strip()
    try:
        entries = _load()
    except _Unreadable:
        return "Not cancelled: the scheduled message store is unreadable."
    kept = [e for e in entries if e["id"] != entry_id]
    if len(kept) == len(entries):
        return f"No scheduled message with id {entry_id!r}."
    _save(kept)
    logger.info("Cancelled scheduled message %s", entry_id)
    return f"Cancelled {entry_id}."


async def deliver_due(send_fn: Callable[[str], Awaitable[bool]],
                      now: Optional[datetime] = None) -> int:
    """Send everything due. Returns how many were delivered. Never raises."""
    now = now or datetime.now(timezone.utc)
    try:
        due = [e for e in _load()
               if not e.get("failed") and datetime.fromisoformat(e["due"]) <= now]
    except (_Unreadable, ValueError) as e:
        logger.error("scheduled.json is unreadable, nothing delivered - %s", e)
        return 0

    delivered = 0
    for entry in due:
        text = entry["content"]
        if now - datetime.fromisoformat(entry["due"]) > _LATE_AFTER:
            text = f"(Scheduled for {_local(entry['due'])}, delivered late.)\n{text}"
        try:
            ok = bool(await send_fn(text))
        except Exception as e:
            logger.error("Scheduled message %s send raised - %s: %s", entry["id"], type(e).__name__, e)
            ok = False

        # Re-read after the await. React can add or cancel while the send is in
        # flight, and writing back the list read before it would undo that.
        try:
            entries = _load()
        except _Unreadable as e:
            logger.error("scheduled.json became unreadable mid-delivery - %s", e)
            return delivered

        if ok:
            delivered += 1
            entries = [x for x in entries if x["id"] != entry["id"]]
            logger.info("Scheduled message %s delivered", entry["id"])
        else:
            for x in entries:
                if x["id"] != entry["id"]:
                    continue
                x["attempts"] = x.get("attempts", 0) + 1
                if x["attempts"] >= _MAX_ATTEMPTS:
                    x["failed"] = True
                    logger.error("Scheduled message %s failed %d times, giving up", x["id"], x["attempts"])
                else:
                    logger.warning("Scheduled message %s not delivered (attempt %d), will retry",
                                   x["id"], x["attempts"])
        _save(entries)
    return delivered


def run_tool(inputs: dict) -> str:
    """React's entry point. Models send "" for unused fields and numbers as strings."""
    action = inputs.get("action")
    if action == "add":
        at = inputs.get("at") or None
        in_minutes = inputs.get("in_minutes")
        if in_minutes in ("", None):
            in_minutes = None
        elif isinstance(in_minutes, str) and in_minutes.strip().isdigit():
            in_minutes = int(in_minutes.strip())
        return add(inputs.get("content", ""), at=at, in_minutes=in_minutes)
    if action == "list":
        return list_pending()
    if action == "cancel":
        return cancel(inputs.get("id", ""))
    return "Unknown action. Use add, list or cancel."
