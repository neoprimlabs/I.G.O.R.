"""Long commands: Hermes' terminal(background=true), process_manage and notify_on_complete.

Hermes runs a command in the background, hands back an id, and wakes the agent when it finishes.
Here the finish goes straight to the user in code, with the last of the output: a wake is a whole
model call, and on this budget nobody needs one to read an exit code. They can ask about it after.

The same sandbox as shell, so no network, keys or sealed files, with the workspace to write in.
Two at a time and 30 minutes each, at low priority, because this host has one core and the bot
runs on it. No writing to a command's input, no restoring after a restart: bubblewrap dies with
the service, so a restart ends a command, and the next start says which were lost.
"""

import asyncio
import json
import logging
import os
import secrets
import time
from pathlib import Path
from typing import Awaitable, Callable, Optional

import config
import sandbox

logger = logging.getLogger(__name__)

_MAX_RUNNING = 2
_MAX_SECONDS = 1800
_NOTICE_TAIL = 1200
_READ_WINDOW = 3000
_KEEP_FINISHED = 10
_KEEP_DAYS = 7

_jobs: dict[str, dict] = {}


def _folder() -> Path:
    return config.MEMORY_DIR / "background"


def _running_path() -> Path:
    return _folder() / "running.json"


def _is_running(job: dict) -> bool:
    return job.get("code") is None


def _note_running() -> None:
    """What is running, on disk, so the next start can say what a restart ended. Never raises."""
    try:
        path = _running_path()
        tmp = path.with_name(path.name + ".tmp")
        tmp.write_text(json.dumps([{"id": j["id"], "command": j["command"]} for j in _jobs.values() if _is_running(j)]),
                       encoding="utf-8")
        os.replace(tmp, path)
    except OSError as e:
        logger.error("Could not note the background commands - %s: %s", type(e).__name__, e)


def _prune(now: float) -> None:
    for job_id in [j["id"] for j in _jobs.values() if not _is_running(j)][:-_KEEP_FINISHED]:
        _jobs.pop(job_id, None)
    for path in _folder().glob("*.log"):
        try:
            if now - path.stat().st_mtime > _KEEP_DAYS * 86400 and path.stem not in _jobs:
                path.unlink()
        except OSError:
            pass


def _duration(seconds: float) -> str:
    minutes, rest = divmod(int(seconds), 60)
    return f"{minutes}m {rest}s" if minutes else f"{rest}s"


def _read(job: dict, offset: int, window: int) -> tuple[str, int, int]:
    """(text, where it ended, the size of the log)."""
    try:
        with open(job["log"], "rb") as f:
            f.seek(0, os.SEEK_END)
            size = f.tell()
            f.seek(min(offset, size))
            data = f.read(window)
            return data.decode("utf-8", "replace"), f.tell(), size
    except OSError:
        return "", offset, offset


def _tail(job: dict, chars: int) -> str:
    try:
        size = Path(job["log"]).stat().st_size
    except OSError:
        return ""
    return _read(job, max(0, size - chars), chars)[0].strip()


def _state(job: dict) -> str:
    if _is_running(job):
        return f"running for {_duration(time.time() - job['started'])}"
    how = job.get("why") or f"exit code {job['code']}"
    return f"finished: {how}, after {_duration(job['ended'] - job['started'])}"


async def _watch(job: dict, process, notify: Optional[Callable[[str], Awaitable[None]]]) -> None:
    try:
        code = await asyncio.wait_for(process.wait(), _MAX_SECONDS)
    except asyncio.TimeoutError:
        job["why"] = f"stopped after the {_MAX_SECONDS // 60} minute limit"
        process.kill()
        code = await process.wait()
    job["code"], job["ended"] = code, time.time()
    _note_running()
    logger.info("Background command %s ended, code %s", job["id"], code)
    # A kill was asked for in this conversation, and its result said so.
    if job.get("killed") or notify is None:
        return
    tail = _tail(job, _NOTICE_TAIL)
    text = f"A background command has finished ({_state(job)}): {job['command'][:120]}"
    text += f"\nThe last of its output:\n{tail}" if tail else "\nIt printed nothing."
    try:
        await notify(text)
    except Exception as e:
        logger.error("Could not report background command %s - %s: %s", job["id"], type(e).__name__, e)


async def start(command: str, notify: Optional[Callable[[str], Awaitable[None]]]) -> str:
    """The caller has checked the command against the shell's rules."""
    if sum(_is_running(j) for j in _jobs.values()) >= _MAX_RUNNING:
        return (f"NOT RUN: {_MAX_RUNNING} background commands are already running, which is the limit on this host. "
                "Wait for one, or stop one with the process tool.")
    try:
        argv = sandbox.prepare(["nice", "-n", "10", "bash", "-c", command], workspace=True)
    except sandbox.Unavailable as e:
        return str(e)
    job_id = secrets.token_hex(3)
    log = _folder() / f"{job_id}.log"
    try:
        _folder().mkdir(parents=True, exist_ok=True)
        _prune(time.time())
        with open(log, "wb") as out:
            process = await asyncio.create_subprocess_exec(
                *argv, stdin=asyncio.subprocess.DEVNULL, stdout=out, stderr=asyncio.subprocess.STDOUT)
    except Exception as e:
        logger.error("Could not start a background command - %s: %s", type(e).__name__, e)
        return f"[could not start it: {type(e).__name__}]"
    job = {"id": job_id, "command": command, "log": str(log), "started": time.time(), "code": None,
           "process": process, "read_to": 0}
    _jobs[job_id] = job
    _note_running()
    job["task"] = asyncio.create_task(_watch(job, process, notify))
    logger.info("Background command %s started: %s", job_id, command[:80])
    return (f"Started in the background, id {job_id}. The user is told when it finishes, with the last of its "
            f"output, so do not wait for it. It stops after {_MAX_SECONDS // 60} minutes.")


def listing() -> str:
    if not _jobs:
        return "No background commands since the last restart."
    return "\n".join(f"{j['id']}  {_state(j)}  {j['command'][:80]}" for j in _jobs.values())


def _poll(job: dict) -> str:
    text, job["read_to"], size = _read(job, job["read_to"], _READ_WINDOW)
    more = f"\n[{size - job['read_to']} more bytes: poll again, or log with an offset.]" if job["read_to"] < size else ""
    return f"{job['id']} is {_state(job)}.\nNew output:\n{text or '(none)'}{more}"


def _log(job: dict, offset: int) -> str:
    text, end, size = _read(job, offset, _READ_WINDOW)
    more = f"\n[{size} bytes in all. Call log with offset={end} for the next part.]" if end < size else ""
    return f"{job['id']} is {_state(job)}.\n{text or '(no output here)'}{more}"


def _kill(job: dict) -> str:
    if not _is_running(job):
        return f"{job['id']} had already {_state(job)}."
    job["killed"], job["why"] = True, "stopped on request"
    job["process"].kill()
    logger.info("Background command %s stopped on request", job["id"])
    return f"Stopped {job['id']}."


def run_tool(inputs: dict) -> str:
    action = inputs.get("action") or "list"
    if action == "list":
        return listing()
    job = _jobs.get(str(inputs.get("id") or "").strip())
    if action not in ("poll", "log", "kill"):
        return "[use list, poll, log or kill]"
    if job is None:
        return f"[no background command with that id. These are known:\n{listing()}]"
    if action == "kill":
        return _kill(job)
    if action == "poll":
        return _poll(job)
    try:
        offset = max(0, int(inputs.get("offset") or 0))
    except (TypeError, ValueError):
        offset = 0
    return _log(job, offset)


def lost_in_restart() -> str:
    """What a restart ended, once, for the first start after it. "" if nothing was."""
    try:
        listed = json.loads(_running_path().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return ""
    lost = [x for x in listed if isinstance(x, dict) and x.get("id") not in _jobs] if isinstance(listed, list) else []
    _note_running()
    if not lost:
        return ""
    names = "; ".join(str(x.get("command"))[:80] for x in lost)
    return f"A restart ended {len(lost)} background command{'s' if len(lost) > 1 else ''} before finishing: {names}"
