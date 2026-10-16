"""Standing goals IGOR works on at night.

memory/goals.md holds one block per goal; retired goals move to
memory/goals_archive.md. React's goal tool is the only writer besides the work loop
(note_progress). A block missing a field is skipped with a warning rather than
failing the whole file, so one hand-edit gone wrong cannot stop the others.
"""

import logging
import os
from datetime import datetime, timezone
from typing import Optional

import config

logger = logging.getLogger(__name__)

KINDS = ("research", "self_improve")
_SETTABLE = ("active", "paused")
_FIELDS = ("kind", "status", "created", "last_reviewed", "next_action")


def _path():
    return config.MEMORY_DIR / "goals.md"


def _archive_path():
    return config.MEMORY_DIR / "goals_archive.md"


def _write(path, text: str) -> None:
    # Write then rename, so a crash mid-write leaves the previous file, not half of one.
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(text, encoding="utf-8")
    os.replace(tmp, path)


def _parse(text: str) -> tuple[list[dict], int]:
    """(valid goals, highest id number seen including skipped blocks)."""
    goals, top = [], 0
    for chunk in text.split("\n## "):
        chunk = chunk.strip()
        if chunk.startswith("## "):
            chunk = chunk[3:]
        if not chunk:
            continue
        head, _, body = chunk.partition("\n")
        goal_id, _, statement = head.strip().partition(" ")
        if goal_id[:1] == "g" and goal_id[1:].isdigit():
            top = max(top, int(goal_id[1:]))
        fields = {}
        for line in body.splitlines():
            key, sep, value = line.partition(":")
            if sep and key.strip() in _FIELDS:
                fields[key.strip()] = value.strip()
        if (not (goal_id[:1] == "g" and goal_id[1:].isdigit()) or not statement.strip()
                or any(f not in fields for f in _FIELDS)):
            logger.warning("Skipping malformed goal block %r", head[:60])
            continue
        goals.append({"id": goal_id, "statement": statement.strip(), **fields})
    return goals, top


def _block(g: dict) -> str:
    lines = [f"## {g['id']} {g['statement']}"] + [f"{f}: {g[f]}" for f in _FIELDS]
    return "\n".join(lines) + "\n"


def _read(path) -> tuple[list[dict], int]:
    if not path.exists():
        return [], 0
    return _parse(path.read_text(encoding="utf-8"))


def _save(goals: list[dict]) -> None:
    _write(_path(), "\n".join(_block(g) for g in goals))


def load() -> list[dict]:
    return _read(_path())[0]


def add(statement: str, kind: str, now: Optional[datetime] = None) -> str:
    now = now or datetime.now(timezone.utc)
    statement = " ".join((statement or "").split())
    kind = (kind or "").strip()
    if not statement:
        return "Not added: give the goal as a statement."
    if kind not in KINDS:
        return "Not added: kind must be research or self_improve."
    top_a = _read(_path())[1]
    top = max(top_a, _read(_archive_path())[1])
    goal = {"id": f"g{top + 1}", "statement": statement, "kind": kind, "status": "active",
            "created": now.date().isoformat(), "last_reviewed": now.isoformat(),
            "next_action": "start"}
    # The file is read-modify-written whole, so unreadable blocks would be dropped on
    # save; keep them by appending to the raw text instead.
    path = _path()
    existing = path.read_text(encoding="utf-8") if path.exists() else ""
    if existing and not existing.endswith("\n"):
        existing += "\n"
    _write(path, existing + ("\n" if existing else "") + _block(goal))
    logger.info("Added goal %s (%s)", goal["id"], kind)
    return f"Added {goal['id']}."


def set_status(goal_id: str, status: str) -> str:
    goal_id = (goal_id or "").strip()
    if status not in _SETTABLE and status != "retired":
        return "Not changed: status must be active, paused or retired."
    goals = load()
    goal = next((g for g in goals if g["id"] == goal_id), None)
    if goal is None:
        return f"No goal with id {goal_id!r}. List them to find it."
    if status == "retired":
        goal["status"] = "retired"
        archive = _archive_path()
        old = archive.read_text(encoding="utf-8") if archive.exists() else ""
        if old and not old.endswith("\n"):
            old += "\n"
        # Archive first: a crash between the two writes duplicates a goal, never loses one.
        _write(archive, old + ("\n" if old else "") + _block(goal))
        _save([g for g in goals if g["id"] != goal_id])
        logger.info("Retired goal %s", goal_id)
        return f"Retired {goal_id}. It is kept in the archive."
    goal["status"] = status
    _save(goals)
    logger.info("Goal %s now %s", goal_id, status)
    return f"{goal_id} is now {status}."


def next_to_work() -> Optional[dict]:
    """The active goal reviewed longest ago; ties go to the earlier in the file."""
    active = [g for g in load() if g["status"] == "active"]
    best, best_at = None, None
    for g in active:
        try:
            at = datetime.fromisoformat(g["last_reviewed"])
        except ValueError:
            logger.warning("Goal %s has an unreadable last_reviewed, treating as oldest", g["id"])
            at = datetime.min.replace(tzinfo=timezone.utc)
        if at.tzinfo is None:
            at = at.replace(tzinfo=timezone.utc)
        if best_at is None or at < best_at:
            best, best_at = g, at
    return best


def note_progress(goal_id: str, next_action: str, now: Optional[datetime] = None) -> None:
    now = now or datetime.now(timezone.utc)
    goals = load()
    for g in goals:
        if g["id"] == goal_id:
            g["last_reviewed"] = now.isoformat()
            g["next_action"] = " ".join((next_action or "").split()) or "start"
            _save(goals)
            return
    logger.warning("note_progress: no goal %s", goal_id)


def _lines(goals: list[dict]) -> str:
    return "\n".join(f"{g['id']}  [{g['kind']}, {g['status']}]  {g['statement']}"
                     f"  (next: {g['next_action']})" for g in goals)


def run_tool(inputs: dict) -> str:
    """React's entry point. Models send "" for fields they do not use."""
    action = inputs.get("action")
    if action == "add":
        return add(inputs.get("statement") or "", inputs.get("kind") or "")
    if action == "list":
        goals = load()
        return _lines(goals) if goals else "No goals."
    if action in ("pause", "resume", "retire"):
        status = {"pause": "paused", "resume": "active", "retire": "retired"}[action]
        return set_status(inputs.get("id") or "", status)
    return "Unknown action. Use add, list, pause, resume or retire."
