import logging
import re
from typing import Awaitable, Callable

import config

logger = logging.getLogger(__name__)

_ALLOWED_FILES = frozenset({"tasks.md", "projects.md", "user.md", "agents.md"})

_SYSTEM_PROMPT = """You are I.G.O.R.'s Prod+Memory agent - task tracking, organization, scheduling, and persistent memory.

You have access to the user's current memory files. Use them to give accurate, contextual responses.

Address the user as "Creator" occasionally - once per response at most, only when it feels natural. Never force it.

WRITING TO MEMORY - When the user asks you to remember, add, store, or update something, include a write instruction using this exact format at the start of your response:

%%WRITE%%
file: <filename>
content:
<content to append>
%%END%%

File selection:
- tasks.md - tasks, todos, action items
- projects.md - project details, context, status updates
- user.md - persistent facts about the user, preferences, working style
- agents.md - agent behavior notes

Then follow the write block with your confirmation message to the user.

READING FROM MEMORY - For queries, respond normally with no write block.

Behavior:
- Summarize and synthesize memory content - never reproduce files verbatim
- Be specific: what's active, what's pending, what was noted
- Proactively surface relevant pending items when asked about status"""


def _read_memory() -> str:
    sections = []
    for name in ("user.md", "projects.md", "tasks.md"):
        path = config.MEMORY_DIR / name
        if path.exists():
            content = path.read_text(encoding="utf-8").strip()
            sections.append(f"=== {name} ===\n{content or '(empty)'}")
    return "\n\n".join(sections) if sections else "(no memory content)"


def _parse_write_instruction(response: str) -> tuple[str | None, str | None, str]:
    """Extract a write instruction block from Claude's response.

    Returns (filename, content, clean_response) where clean_response
    has the write block stripped out.
    """
    pattern = r"%%WRITE%%\s*\nfile:\s*(\S+)\s*\ncontent:\s*\n(.*?)%%END%%\s*\n?"
    match = re.search(pattern, response, re.DOTALL)
    if not match:
        return None, None, response

    filename = match.group(1).strip()
    content = match.group(2).rstrip()
    clean_response = (response[: match.start()] + response[match.end() :]).strip()
    return filename, content, clean_response


def _write_to_memory(filename: str, content: str) -> bool:
    """Append content to a memory file. Returns True on success.

    Only writes to whitelisted filenames within MEMORY_DIR - no path traversal possible.
    """
    if filename not in _ALLOWED_FILES:
        logger.error("Memory write blocked - disallowed file: %s", filename)
        return False

    path = config.MEMORY_DIR / filename
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
    response = await call_claude(_SYSTEM_PROMPT, messages)

    filename, content, clean_response = _parse_write_instruction(response)

    if filename and content:
        success = _write_to_memory(filename, content)
        if not success:
            clean_response += "\n\n(Note: Memory write failed - details logged.)"

    return clean_response
