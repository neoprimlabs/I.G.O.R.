"""Scripts a scheduled message runs first: Hermes' cron `script`, `no_agent` and wake gate.

Hermes keeps them in ~/.hermes/scripts/, picks the interpreter by extension, honours no shebang
and runs them without provider keys. What a script prints becomes the job's material, or with
no_agent the message itself. Printing nothing, or {"wakeAgent": false} as the last line, ends the
run there with no model call, which is the point on 200000 tokens a day: a check costs nothing
until it finds something.

Here, for what this host and IGOR's limits allow:
- memory/private/cron_scripts/ is sealed from every tool, so only the user or Claude Code can add a
  script. A model can only name one, with no arguments: IGOR cannot write code and then schedule it.
- The sandbox, as for shell, but with the network, because checking something outside is most of
  what these are for, and with one folder of its own that lasts between runs (IGOR_STATE). So a
  script can read IGOR's ordinary files and reach the web in one run, which is why nothing but the
  user or Claude Code may write one. A page's words can reach what it prints, so compose.py treats
  that as outside text.
- 60 seconds rather than Hermes' hour, and a cap on what is kept, on 956 MB.
"""

import asyncio
import json
import logging
import re
import sys
from pathlib import Path
from typing import Optional

import config
import sandbox
import sanitize

logger = logging.getLogger(__name__)

_NAME_RE = re.compile(r"^[A-Za-z0-9_-]{1,40}\.(py|sh|bash)$")
_TAG_CHARS_RE = re.compile("[\U000e0000-\U000e007f]")
_STATE_INSIDE = "/tmp/state"
_TIMEOUT_S = 60
_OUTPUT_CAP = 3000
_ERROR_CAP = 300


def _folder() -> Path:
    return config.MEMORY_DIR / "private" / "cron_scripts"


def available() -> list[str]:
    try:
        return sorted(p.name for p in _folder().iterdir()
                      if _NAME_RE.match(p.name) and p.is_file() and not p.is_symlink())
    except OSError:
        return []


def problem(name: str) -> str:
    """"" when a message can name this script, otherwise what to tell the model."""
    names = available()
    if name in names:
        return ""
    return (f"there is no script called {name!r}. Scripts: {', '.join(names) or 'none yet'}. "
            "Only the user or Claude Code can add one.")


def wakes(printed: str) -> bool:
    """Whether a script found anything to say."""
    lines = printed.strip().splitlines()
    if not lines:
        return False
    try:
        gate = json.loads(lines[-1])
    except ValueError:
        return True
    return not (isinstance(gate, dict) and gate.get("wakeAgent") is False)


async def _read(stream, cap: int) -> str:
    """To the end, so a script never blocks on a full pipe, keeping only the start."""
    kept = b""
    while True:
        chunk = await stream.read(65536)
        if not chunk:
            break
        kept = (kept + chunk)[:cap + 1]
    text = sanitize.redact(_TAG_CHARS_RE.sub("", kept.decode("utf-8", "replace"))).strip()
    return text[:cap] + f"\n[cut at {cap} characters]" if len(kept) > cap else text


async def run(name: str) -> tuple[Optional[str], str]:
    """(what it printed, "") or (None, why it failed). Never raises."""
    why = problem(name)
    if why:
        logger.error("Scheduled script %s not run - %s", name, why)
        return None, why
    suffix = Path(name).suffix
    inside = f"/tmp/job{suffix}"
    state = _folder() / ".state" / name
    process = None
    try:
        state.mkdir(parents=True, exist_ok=True)
        argv = sandbox.prepare([sys.executable if suffix == ".py" else "bash", inside], network=True,
                               mounts=[("--ro-bind", _folder() / name, inside),
                                       ("--bind", state, _STATE_INSIDE)],
                               env={"IGOR_STATE": _STATE_INSIDE})
        process = await asyncio.create_subprocess_exec(
            *argv, stdin=asyncio.subprocess.DEVNULL,
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
        printed, errors = await asyncio.wait_for(
            asyncio.gather(_read(process.stdout, _OUTPUT_CAP), _read(process.stderr, _ERROR_CAP)),
            _TIMEOUT_S)
        code = await asyncio.wait_for(process.wait(), 5)
    except asyncio.TimeoutError:
        process.kill()
        await process.wait()
        logger.error("Scheduled script %s ran past %d seconds and was killed", name, _TIMEOUT_S)
        return None, f"it ran past {_TIMEOUT_S} seconds"
    except Exception as e:
        logger.error("Scheduled script %s could not run - %s: %s", name, type(e).__name__, e)
        return None, f"it could not start ({type(e).__name__})"
    if code != 0:
        logger.error("Scheduled script %s failed, exit code %d - %s", name, code, errors)
        return None, f"exit code {code}. {errors}".strip()
    return printed, ""
