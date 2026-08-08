import logging
from typing import Awaitable, Callable, Optional

import config

logger = logging.getLogger(__name__)

# Deliberately narrower than _ALLOWED_FILES below. ConfigEdit acts on natural
# language from a chat message, so it only reaches settings files. Task and memory
# content stays with React's memory_write, and prompt_*.md files stay editable
# only through Claude Code - a prompt rewritten from Discord is how you lose an
# agent's personality with no undo.
_CONFIG_EDITABLE = {
    "digest_config.md": False,
    "watchlist.md": False,
    "schedule_config.md": True,  # read at startup, so a change needs a restart
}

# Monitor gates sections with an exact string match, so an invented name is
# silently ignored rather than erroring. Validate before writing.
_VALID_DIGEST_SECTIONS = frozenset({
    "tasks", "projects", "daily_forecast", "ai_news", "unreal_news",
})

_CONFIG_SYSTEM_PROMPT = """You edit configuration files for I.G.O.R., a personal assistant. The user describes a change in plain language and you produce the complete new file.

You may edit exactly these three files:

1. digest_config.md - which sections appear in the morning digest.
   Sections must come from this list, spelled exactly: tasks, projects, daily_forecast, ai_news, unreal_news.
   Any other name is silently ignored by the digest, so never invent one.

2. watchlist.md - a plain list of things the monitor keeps an eye on. Free text, one item per line, each starting with "- ".

3. schedule_config.md - when scheduled jobs run. Times are UTC, in the format "time: HH:MM UTC".

Rules:
- Change only what the user asked for. Preserve every other line exactly as it is, including headers, comments and unrelated sections.
- Pick the single file the request is about. If it genuinely spans two files, pick the one the user emphasised and say so in your reasoning line.
- If the request is unclear, or is not a change to one of these three files, do not invent an edit. Reply with a single line explaining the problem and nothing else.

Output format, exactly:
Line 1: the filename and nothing else.
Then a line containing only <<<FILE
Then the complete new content of that file.
Then a line containing only >>>FILE

Output nothing before the filename and nothing after >>>FILE.

Style:
- No emojis
- No em dashes - use plain hyphens
- No exclamation points
- No casual filler phrases ("Sure!", "Of course!", "Happy to help!")"""

_ALLOWED_FILES = frozenset({
    "tasks.md", "projects.md", "user.md", "agents.md",
    "digest_config.md", "schedule_config.md", "watchlist.md",
    "prompt_prodmem.md", "prompt_monitor.md", "prompt_react.md", "prompt_evaluator.md",
    "research.md",
})

_OVERWRITABLE_FILES = frozenset({
    "tasks.md", "projects.md", "user.md", "agents.md",
    "digest_config.md", "schedule_config.md", "watchlist.md",
    "prompt_prodmem.md", "prompt_monitor.md", "prompt_react.md", "prompt_evaluator.md",
    "research.md",
})


def _write_to_memory(filename: str, content: str, mode: str = "append") -> bool:
    if filename not in _ALLOWED_FILES:
        logger.error("Memory write blocked - disallowed file: %s", filename)
        return False

    path = config.MEMORY_DIR / filename

    if mode == "overwrite":
        if filename not in _OVERWRITABLE_FILES:
            logger.error("Memory overwrite blocked - not an overwritable file: %s", filename)
            return False
        try:
            path.write_text(content + "\n", encoding="utf-8")
            return True
        except Exception as e:
            logger.error("Memory overwrite failed for %s - %s: %s", filename, type(e).__name__, e)
            return False

    if not path.exists():
        logger.error("Memory write blocked - file not found: %s", filename)
        return False

    try:
        with path.open("a", encoding="utf-8") as f:
            f.write("\n" + content + "\n")
        return True
    except Exception as e:
        logger.error("Memory write failed for %s - %s: %s", filename, type(e).__name__, e)
        return False


def _read_config(filename: str) -> str:
    path = config.MEMORY_DIR / filename
    if not path.exists():
        return "(file does not exist yet)"
    try:
        return path.read_text(encoding="utf-8").strip()
    except Exception as e:
        return f"(unreadable: {type(e).__name__})"


def _parse_reply(raw: str) -> tuple[Optional[str], Optional[str]]:
    """Pull (filename, new_content) out of the model's reply.

    Returns (None, None) if there is no filename line, or (filename, None) if the
    fenced block is missing or malformed.
    """
    lines = raw.splitlines()
    filename = None
    start = 0
    for i, line in enumerate(lines):
        if line.strip():
            filename = line.strip().strip("`").strip()
            start = i + 1
            break
    if not filename:
        return None, None

    open_idx = close_idx = None
    for i in range(start, len(lines)):
        if lines[i].strip() == "<<<FILE":
            open_idx = i
            break
    if open_idx is None:
        return filename, None
    for i in range(open_idx + 1, len(lines)):
        if lines[i].strip() == ">>>FILE":
            close_idx = i
            break
    if close_idx is None:
        return filename, None

    return filename, "\n".join(lines[open_idx + 1:close_idx]).strip()


def _invalid_digest_sections(content: str) -> list[str]:
    """Section names under a '## Sections' heading that the digest would ignore."""
    bad = []
    in_sections = False
    for line in content.splitlines():
        stripped = line.strip()
        if stripped.startswith("##"):
            in_sections = stripped.lower().lstrip("#").strip() == "sections"
            continue
        if in_sections and stripped.startswith("-"):
            name = stripped.lstrip("-").strip()
            if name and name not in _VALID_DIGEST_SECTIONS:
                bad.append(name)
    return bad


async def handle(message: str, call_claude: Callable[..., Awaitable[str]]) -> Optional[str]:
    """Apply a natural-language configuration change to one settings file."""
    current = "\n\n".join(
        f"=== {name} ===\n{_read_config(name)}" for name in _CONFIG_EDITABLE
    )
    user_content = f"Current files:\n\n{current}\n\nRequested change:\n{message}"

    raw = await call_claude(
        _CONFIG_SYSTEM_PROMPT,
        [{"role": "user", "content": user_content}],
        max_tokens=2048,
        model=config.MODELS["chat"],
    )

    filename, new_content = _parse_reply(raw or "")

    if filename not in _CONFIG_EDITABLE:
        # Not a config change. The router is an 8B model and misroutes at the
        # margins: "I don't think I saw anything in there about PCG content" reads
        # as a file being short of something. Returning None hands the message to
        # React, which can actually answer it. Returning the raw reply instead
        # printed the model's working - including the full contents of
        # digest_config.md - at a user who had asked about a game prompt.
        logger.info("ConfigEdit declining, not a config change (got %r)", filename)
        return None

    if not new_content:
        logger.info("ConfigEdit reply for %s had no usable file block", filename)
        return None

    if filename == "schedule_config.md":
        # Round-trip through the parser that will actually read this at startup.
        # Without it, "change the digest to 8am" could write `time: 8am`, report
        # success, and leave the digest firing at its old time forever with nothing
        # to say otherwise - a silent failure of exactly the kind the debugging
        # playbook warns about.
        from agents import monitor
        if monitor._parse_digest_schedule(new_content) is None:
            logger.warning("ConfigEdit rejected an unparseable schedule")
            return (
                "I did not apply that. schedule_config.md needs a line like "
                "`time: 08:00 UTC` under `## morning_digest`, in 24-hour HH:MM. "
                "What that would have written does not parse, so the digest would "
                "have kept running at its old time without telling you."
            )

    if filename == "digest_config.md":
        bad = _invalid_digest_sections(new_content)
        if bad:
            logger.warning("ConfigEdit rejected unknown digest sections: %s", bad)
            return (
                f"I did not apply that. It would have added digest sections the "
                f"digest does not recognise ({', '.join(bad)}), which would be "
                f"ignored silently. Valid sections are: "
                f"{', '.join(sorted(_VALID_DIGEST_SECTIONS))}."
            )

    # A question can reach this agent - the router is an 8B model and "what time is
    # the digest scheduled for" has classified as CONFIG. Writing an identical file
    # in response to a question is at best noise and at worst a mangled rewrite, so
    # an unchanged result is treated as "nothing was asked for" and reported instead.
    if new_content.strip() == (_read_config(filename) or "").strip():
        logger.info("ConfigEdit produced no change to %s, treating as a question", filename)
        return (
            f"Nothing to change - {filename} already reads that way. "
            f"If you did want something altered, say what should be different."
        )

    # One rolling backup per file, so a bad edit is recoverable immediately rather
    # than waiting on the daily off-host backup.
    path = config.MEMORY_DIR / filename
    if path.exists():
        try:
            (config.MEMORY_DIR / f"{filename}.bak").write_text(
                path.read_text(encoding="utf-8"), encoding="utf-8"
            )
        except Exception as e:
            logger.warning("ConfigEdit could not write a backup for %s: %s", filename, e)

    if not _write_to_memory(filename, new_content, mode="overwrite"):
        return f"I could not write {filename}. The error is in the log."

    logger.info("ConfigEdit wrote %s (%d bytes)", filename, len(new_content))
    restart = _CONFIG_EDITABLE[filename]
    note = (
        " That file is read at startup, so it needs a restart to take effect."
        if restart
        else " That takes effect immediately, no restart needed."
    )
    return f"Updated {filename}.{note}"
