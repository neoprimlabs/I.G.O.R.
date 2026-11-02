"""Scripts that run on events: Hermes' shell hooks, in the place its cron scripts already live here.

Hermes' contract: the hook gets a JSON payload on stdin; a pre_tool_call hook blocks with exit code 2
or {"decision": "block", "reason": ...} on stdout; anything else, a crash or a timeout included, fails
open with a warning, because a broken observer must not stop the agent. "Placing the files is the
opt-in." All of that holds.

Here the files go in memory/private/cron_scripts/hooks/, sealed from every tool, so only the user or
Claude Code can place one, and they run in prerun's sandbox, where Hermes runs its in-process with the
agent's keys. What one prints, past pre_tool_call, goes to the user with no model. Named <event>.py or
<event>_anything.py, .sh or .bash. With no hook placed, an event costs a look at a folder.
Not built: changing a tool's arguments, adding to a prompt, or failing closed.
"""

import asyncio
import json
import logging
import re
from pathlib import Path
from typing import Awaitable, Callable, Optional

import prerun

logger = logging.getLogger(__name__)

EVENTS = ("startup", "pre_tool_call", "post_tool_call", "agent_end")
_TIMEOUT_S = 10
_MAX_PAYLOAD = 20000

_sender: Optional[Callable[[str], Awaitable[object]]] = None
_tasks: set = set()
_started = False


def set_sender(fn: Callable[[str], Awaitable[object]]) -> None:
    global _sender
    _sender = fn


def _folder() -> Path:
    return prerun._folder() / "hooks"


def scripts(event: str) -> list[Path]:
    wanted = re.compile(rf"^{re.escape(event)}(_[A-Za-z0-9-]+)?\.(py|sh|bash)$")
    try:
        return sorted(p for p in _folder().iterdir() if wanted.match(p.name) and p.is_file() and not p.is_symlink())
    except OSError:
        return []


def _payload(event: str, fields: dict) -> str:
    text = json.dumps({"hook_event_name": event, **fields}, default=str)
    return text if len(text) <= _MAX_PAYLOAD else json.dumps({"hook_event_name": event, "truncated": True})


def _block_reason(printed: str) -> Optional[str]:
    """Both of Hermes' shapes, from the last line printed."""
    lines = printed.strip().splitlines()
    try:
        said = json.loads(lines[-1]) if lines else None
    except ValueError:
        return None
    if isinstance(said, dict) and "block" in (said.get("decision"), said.get("action")):
        return str(said.get("reason") or said.get("message") or "Blocked by a hook.")
    return None


async def pre_tool(tool_name: str, tool_input: dict) -> Optional[str]:
    """What to tell the model if a hook blocks this call, or None. Never raises."""
    placed = scripts("pre_tool_call")
    payload = _payload("pre_tool_call", {"tool_name": tool_name, "tool_input": tool_input}) if placed else ""
    for script in placed:
        code, printed, errors = await prerun.spawn(script, f"hook {script.name}", payload, _TIMEOUT_S)
        if code is None:
            logger.warning("Hook %s did not finish, so the call goes ahead - %s", script.name, errors)
            continue
        reason = _block_reason(printed)
        if code == 2 and not reason:
            reason = errors[:400] or "Blocked by a hook."
        if reason and code in (0, 2):
            logger.info("Hook %s blocked %s - %s", script.name, tool_name, reason[:120])
            return (f"[blocked by the user's own hook: {reason}. Nothing ran. Do not retry it. Tell the user what "
                    "you wanted to do.]")
        if code != 0:
            logger.warning("Hook %s failed, exit code %s, so the call goes ahead - %s", script.name, code, errors)
    import plugins
    return await plugins.pre_tool(tool_name, tool_input)


async def _run(event: str, payload: str, placed: list[Path]) -> None:
    for script in placed:
        code, printed, errors = await prerun.spawn(script, f"hook {script.name}", payload, _TIMEOUT_S)
        if code != 0:
            logger.warning("Hook %s failed, exit code %s - %s", script.name, code, errors)
            continue
        if printed and prerun.wakes(printed) and _sender is not None:
            try:
                await _sender(printed)
            except Exception as e:
                logger.error("Could not send what hook %s printed - %s: %s", script.name, type(e).__name__, e)


def fire(event: str, **fields) -> None:
    """Not waited for: an observer never holds up a reply. Never raises."""
    import plugins
    plugins.fire(event, **fields)
    placed = scripts(event)
    if not placed:
        return
    try:
        task = asyncio.get_running_loop().create_task(_run(event, _payload(event, fields), placed))
    except RuntimeError:
        return
    _tasks.add(task)
    task.add_done_callback(_tasks.discard)


def fire_startup() -> None:
    """Once a process: Discord calls on_ready again on every reconnect."""
    global _started
    if not _started:
        _started = True
        fire("startup")


async def drain() -> None:
    """For tests: what fire started."""
    while _tasks:
        await asyncio.gather(*list(_tasks))
