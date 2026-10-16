"""Self-improvement proposals and the standing preferences the user approves.

memory/work/self-improvement/proposals.md holds what the night's review suggested.
Its text comes from corrections and logs, which are derived from untrusted input, so
every field is flattened to one line and nothing here acts on a proposal by itself.
decide() is the only path that writes outside the work folder: approving a
`preference` appends one dated line to memory/preferences.md, which Direct, React and
the job ranking read. Approving a `prompt` or `code` proposal only marks it for a
Claude Code session.
"""

import logging
import os
import re
from datetime import datetime, timezone
from typing import Optional

import config
from sanitize import clean

logger = logging.getLogger(__name__)

KINDS = ("preference", "prompt", "code")
PREFERENCES_CAP = 1500
_MAX_PENDING = 30
_KEEP_DECIDED = 40
_CAPS = {"problem": 300, "evidence": 400, "change": 300, "test": 300}
_PREFERENCE_CHANGE_CAP = 200
_HEADER_RE = re.compile(r"^## (p\d+) \[(\w+)\] (pending|approved|rejected) (\S+)$")
_HEADING = "Standing preferences the user approved:"


def _folder():
    return config.MEMORY_DIR / "work" / "self-improvement"


def _path():
    return _folder() / "proposals.md"


def _preferences_path():
    return config.MEMORY_DIR / "preferences.md"


def _write(path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(text, encoding="utf-8")
    os.replace(tmp, path)


def _flat(text: str, cap: int) -> str:
    return clean(" ".join(str(text or "").split()))[:cap].strip()


def _load() -> list[dict]:
    try:
        text = _path().read_text(encoding="utf-8")
    except OSError:
        return []
    out: list[dict] = []
    current: Optional[dict] = None
    for line in text.splitlines():
        match = _HEADER_RE.match(line)
        if match:
            current = {"id": match.group(1), "kind": match.group(2),
                       "status": match.group(3), "date": match.group(4),
                       "problem": "", "evidence": "", "change": "", "test": ""}
            out.append(current)
            continue
        if current is not None:
            label, sep, value = line.partition(": ")
            if sep and label.lower() in _CAPS:
                current[label.lower()] = value
    return out


def _render(items: list[dict]) -> str:
    blocks = []
    for p in items:
        blocks.append(
            f"## {p['id']} [{p['kind']}] {p['status']} {p['date']}\n"
            f"Problem: {p['problem']}\nEvidence: {p['evidence']}\n"
            f"Change: {p['change']}\nTest: {p['test']}\n")
    return "# Proposals\n\n" + "\n".join(blocks)


def _save(items: list[dict]) -> None:
    decided = [p for p in items if p["status"] != "pending"]
    drop = {p["id"] for p in decided[:-_KEEP_DECIDED]} if len(decided) > _KEEP_DECIDED else set()
    _write(_path(), _render([p for p in items if p["id"] not in drop]))


def pending() -> list[dict]:
    return [p for p in _load() if p["status"] == "pending"]


def add(kind: str, problem: str, evidence: str, change: str, test: str,
        now: Optional[datetime] = None) -> str:
    """The new proposal's id, or "" when it was refused (bad kind, empty field, full)."""
    now = now or datetime.now(timezone.utc)
    kind = (kind or "").strip()
    fields = {"problem": problem, "evidence": evidence, "change": change, "test": test}
    cap = dict(_CAPS)
    if kind == "preference":
        cap["change"] = _PREFERENCE_CHANGE_CAP
    fields = {k: _flat(v, cap[k]) for k, v in fields.items()}
    if kind not in KINDS or not all(fields.values()):
        return ""
    items = _load()
    if sum(1 for p in items if p["status"] == "pending") >= _MAX_PENDING:
        logger.warning("Proposal refused: %d already pending", _MAX_PENDING)
        return ""
    number = max([int(p["id"][1:]) for p in items], default=0) + 1
    items.append({"id": f"p{number}", "kind": kind, "status": "pending",
                  "date": now.date().isoformat(), **fields})
    _save(items)
    return f"p{number}"


def _preference_lines() -> list[str]:
    try:
        text = _preferences_path().read_text(encoding="utf-8")
    except OSError:
        return []
    return [line for line in text.splitlines() if line.startswith("- ")]


def preferences_block() -> str:
    lines = _preference_lines()
    return f"{_HEADING}\n" + "\n".join(lines) if lines else ""


def with_preferences(prompt: str) -> str:
    """A system prompt plus the approved preferences, whichever prompt source it came from."""
    block = preferences_block()
    return f"{prompt}\n\n{block}" if block else prompt


def decide(pid: str, approve: bool, now: Optional[datetime] = None) -> str:
    now = now or datetime.now(timezone.utc)
    pid = (pid or "").strip()
    items = _load()
    target = next((p for p in items if p["id"] == pid and p["status"] == "pending"), None)
    if target is None:
        return f"There is no pending proposal {pid or '(no id given)'}."
    if not approve:
        target["status"] = "rejected"
        _save(items)
        return f"Rejected {pid}."
    if target["kind"] == "preference":
        lines = _preference_lines()
        lines.append(f"- {target['change']} (approved {now.date().isoformat()})")
        text = "\n".join(lines) + "\n"
        if len(text) > PREFERENCES_CAP:
            return (f"Not approved: preferences.md would pass {PREFERENCES_CAP} characters. "
                    f"{pid} stays pending. Reject or retire an older preference first.")
        _write(_preferences_path(), text)
        target["status"] = "approved"
        _save(items)
        logger.info("Approved preference %s", pid)
        return f"Approved {pid}. It is now a standing preference."
    target["status"] = "approved"
    _save(items)
    return (f"Approved {pid}. Nothing was changed: a {target['kind']} change is applied in a "
            f"Claude Code session, not by IGOR.")


def _listing(items: list[dict]) -> str:
    return "\n\n".join(
        f"{p['id']} [{p['kind']}] {p['date']}\n  Problem: {p['problem']}\n"
        f"  Evidence: {p['evidence']}\n  Change: {p['change']}\n  Test: {p['test']}"
        for p in items)


def run_tool(inputs: dict) -> str:
    """React's entry point. Models send "" for fields they do not use."""
    action = inputs.get("action")
    if action == "list":
        items = pending()
        return _listing(items) if items else "No pending proposals."
    if action in ("approve", "reject"):
        return decide(inputs.get("id") or "", approve=action == "approve")
    return "Unknown action. Use list, approve or reject."
