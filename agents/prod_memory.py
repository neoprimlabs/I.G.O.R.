import logging
from typing import Awaitable, Callable

import config

logger = logging.getLogger(__name__)

_SYSTEM_PROMPT = """You are I.G.O.R.'s Prod+Memory agent — task tracking, organization, scheduling, and persistent memory.

You have access to the user's current memory files (tasks, projects, notes, user context). Use them to give accurate, contextual responses.

Behavior:
- Answer task and project queries using the provided memory content
- Proactively surface relevant pending items when asked about status
- When the user says to "remember" something or add a task, confirm exactly what you would store and in which file
- Summarize and synthesize memory content — do not reproduce files verbatim in your response
- Be specific: what's active, what's pending, what was noted
- Note: memory writes are read-only in this version — updates must be made manually to the markdown files"""


def _read_memory() -> str:
    sections = []
    for name in ("user.md", "projects.md", "tasks.md"):
        path = config.MEMORY_DIR / name
        if path.exists():
            content = path.read_text(encoding="utf-8").strip()
            sections.append(f"=== {name} ===\n{content or '(empty)'}")
    return "\n\n".join(sections) if sections else "(no memory content)"


async def handle(
    message: str,
    context: list[dict],
    call_claude: Callable[..., Awaitable[str]],
) -> str:
    try:
        memory_content = _read_memory()
    except Exception as e:
        logger.error("Memory read failed — %s: %s", type(e).__name__, e)
        memory_content = "(memory unavailable)"

    user_content = f"Current memory state:\n\n{memory_content}\n\nUser: {message}"
    messages = context + [{"role": "user", "content": user_content}]
    return await call_claude(_SYSTEM_PROMPT, messages)
