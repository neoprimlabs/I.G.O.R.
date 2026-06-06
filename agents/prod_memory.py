import logging
import re
from typing import Awaitable, Callable

import config

logger = logging.getLogger(__name__)

_ALLOWED_FILES = frozenset({
    "tasks.md", "projects.md", "user.md", "agents.md",
    "digest_config.md", "schedule_config.md", "system_config.md", "watchlist.md",
    "prompt_dev.md", "prompt_research.md", "prompt_comms.md",
    "prompt_prodmem.md", "prompt_monitor.md", "prompt_direct.md",
})

_OVERWRITABLE_FILES = frozenset({
    "tasks.md", "projects.md", "user.md", "agents.md",
    "digest_config.md", "schedule_config.md", "system_config.md", "watchlist.md",
    "prompt_dev.md", "prompt_research.md", "prompt_comms.md",
    "prompt_prodmem.md", "prompt_monitor.md", "prompt_direct.md",
})

_DEFAULT_SYSTEM_PROMPT = """You are I.G.O.R.'s Prod+Memory agent - task tracking, organization, scheduling, and persistent memory.

You have access to the user's current memory and config files. Use them to give accurate, contextual responses.

Address the user as "Creator" occasionally - once per response at most, only when it feels natural. Never force it.

WRITING TO MEMORY - When the user asks you to remember, add, store, or update something, include a write instruction at the start of your response.

Default (append) - adds content to the end of the file:
%%WRITE%%
file: <filename>
content:
<content to append>
%%END%%

Overwrite - replaces the entire file. Use for all config files and prompt files:
%%WRITE%%
file: <filename>
mode: overwrite
content:
<complete new file content>
%%END%%

MEMORY FILES:
- tasks.md - tasks, todos, action items
- projects.md - project details, context, status updates
- user.md - persistent facts about the user, preferences, working style
- agents.md - agent behavior notes

Default to append for new content. Before appending, check if the exact same entry already exists in the file - if it does, skip the write and tell the user it's already there. Use overwrite (mode: overwrite) ONLY when the user explicitly asks to edit, remove, complete, or reorganize existing entries. When overwriting, reproduce the full file with the requested changes applied.

CONFIG FILES (overwrite to update):
- digest_config.md - morning digest sections (valid: tasks, projects, daily_forecast, ai_news)
- schedule_config.md - scheduled job times. Format: ## morning_digest / time: HH:MM UTC. Changes take effect after restart.
- system_config.md - model name and context window. Changes take effect after restart.
- watchlist.md - what Monitor tracks and reports on

AGENT PROMPTS (overwrite to update - changes take effect immediately):
- prompt_dev.md, prompt_research.md, prompt_comms.md, prompt_prodmem.md, prompt_monitor.md, prompt_direct.md
- If a prompt file is empty or missing, the agent uses its built-in default

Always confirm what was written and note if a restart is required for the change to take effect.

READING FROM MEMORY - For queries, respond normally with no write block.

Behavior:
- Summarize and synthesize memory content - never reproduce files verbatim
- Be specific: what's active, what's pending, what was noted
- Proactively surface relevant pending items when asked about status

Style:
- No emojis
- No em dashes - use plain hyphens
- No exclamation points
- No casual filler phrases ("Sure!", "Of course!", "Happy to help!")"""


def _get_system_prompt() -> str:
    path = config.MEMORY_DIR / "prompt_prodmem.md"
    if path.exists():
        content = path.read_text(encoding="utf-8").strip()
        if content:
            return content
    return _DEFAULT_SYSTEM_PROMPT


def _read_memory() -> str:
    sections = []
    for name in ("user.md", "projects.md", "tasks.md", "agents.md",
                 "digest_config.md", "schedule_config.md", "system_config.md", "watchlist.md"):
        path = config.MEMORY_DIR / name
        if path.exists():
            content = path.read_text(encoding="utf-8").strip()
            sections.append(f"=== {name} ===\n{content or '(empty)'}")
    return "\n\n".join(sections) if sections else "(no memory content)"


def _parse_write_instruction(response: str) -> tuple[str | None, str | None, str | None, str]:
    pattern = r"%%WRITE%%\s*\nfile:\s*(\S+)\s*\n(?:mode:\s*(\S+)\s*\n)?content:\s*\n(.*?)%%END%%\s*\n?"
    match = re.search(pattern, response, re.DOTALL)
    if not match:
        return None, None, None, response

    filename = match.group(1).strip()
    mode = match.group(2).strip() if match.group(2) else "append"
    content = match.group(3).rstrip()
    clean_response = (response[: match.start()] + response[match.end() :]).strip()
    return filename, mode, content, clean_response


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


async def handle(
    message: str,
    context: list[dict],
    call_claude: Callable[..., Awaitable[str]],
) -> str:
    try:
        memory_content = _read_memory()
    except Exception as e:
        logger.error("Memory read failed - %s: %s", type(e).__name__, e)
        memory_content = "(memory unavailable)"

    user_content = f"Current memory state:\n\n{memory_content}\n\nUser: {message}"
    messages = context + [{"role": "user", "content": user_content}]
    response = await call_claude(_get_system_prompt(), messages)

    filename, mode, content, clean_response = _parse_write_instruction(response)

    if filename and content:
        success = _write_to_memory(filename, content, mode or "append")
        if not success:
            clean_response += "\n\n(Note: Memory write failed - details logged.)"

    return clean_response
