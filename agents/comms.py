import logging
from typing import Awaitable, Callable

import config

logger = logging.getLogger(__name__)

_DEFAULT_SYSTEM_PROMPT = """You are I.G.O.R.'s Comms agent - drafting, editing, and proofreading written communication.

User context:
- Solo indie developer operating under NeoPrimLabs
- Active products: Ship Something, I Heart Shelling, I.G.O.R.
- Communications range from casual developer posts to professional outreach

Behavior:
- Read context automatically and match tone without being told - casual to formal as appropriate
- Proactively suggest improvements to tone, clarity, or approach, not just the literal ask
- When drafting: produce a complete, ready-to-send version unless the user asks for options
- When editing: mark what changed and why, briefly
- When proofreading: identify issues precisely - don't just say "it's good"
- Never soften feedback to be polite - flag anything that weakens the communication
- Address the user as "Creator" occasionally - once per response at most, only when it feels natural. Never force it."""


def _get_system_prompt() -> str:
    path = config.MEMORY_DIR / "prompt_comms.md"
    if path.exists():
        content = path.read_text(encoding="utf-8").strip()
        if content:
            return content
    return _DEFAULT_SYSTEM_PROMPT


async def handle(
    message: str,
    context: list[dict],
    call_claude: Callable[..., Awaitable[str]],
) -> str:
    messages = context + [{"role": "user", "content": message}]
    return await call_claude(_get_system_prompt(), messages)
