import asyncio
import functools
import logging
from typing import Awaitable, Callable

import anthropic

import config

logger = logging.getLogger(__name__)

_MONITOR_TRIGGERS = frozenset({
    "trigger digest", "run digest", "send digest",
    "monitor status", "monitoring status",
    "what are you monitoring", "what is being monitored",
    "watchlist", "scheduler", "scheduled jobs", "next run",
    "system health",
})

_CRITIC_PROMPT = """You are a skill evaluator for an AI assistant system.

Given a task and an agent's response, determine if the agent applied a non-obvious technique worth capturing for future use.

Worth capturing:
- A search or query strategy that improved results beyond the obvious approach
- A synthesis method that produced unusually clear or structured output
- A technical pattern or approach that solved a non-trivial problem in a reusable way

Not worth capturing:
- Routine lookups or standard responses
- Generic advice applicable to any situation
- Common patterns any competent agent would use

Respond with exactly one line:
CAPTURE: [one sentence describing the reusable technique]
SKIP

One line only. No explanation."""

_SKILL_FILES: dict[str, str] = {
    "React": "skills_react.md",
}


def _write_skill(agent_name: str, content: str) -> None:
    filename = _SKILL_FILES.get(agent_name)
    if not filename:
        return
    path = config.MEMORY_DIR / filename
    try:
        with path.open("a", encoding="utf-8") as f:
            f.write(f"{content}\n")
        logger.info("Skill captured for %s", agent_name)
    except Exception as e:
        logger.error("Skill write failed for %s - %s: %s", agent_name, type(e).__name__, e)


# Type alias: a bound call_claude with client and notify already applied.
# Signature: async (system: str, messages: list[dict], max_tokens: int = 1024) -> str
CallClaude = Callable[..., Awaitable[str]]


async def call_claude(
    client: anthropic.AsyncAnthropic,
    notify: Callable[[str], Awaitable[None]],
    system: str,
    messages: list[dict],
    max_tokens: int = 1024,
) -> str:
    """Call Claude API with rate-limit retry, backoff, and error logging.

    Rate limit behavior: notifies user via `notify`, waits, then retries up to
    3 times before returning a user-facing error string (never raises on rate limit).
    All other API errors are logged and re-raised for the orchestrator to handle.
    """
    system_param = [{"type": "text", "text": system, "cache_control": {"type": "ephemeral"}}]
    max_retries = 3
    for attempt in range(max_retries):
        try:
            response = await client.messages.create(
                model=config.MODEL,
                system=system_param,
                messages=messages,
                max_tokens=max_tokens,
            )
            return response.content[0].text
        except anthropic.RateLimitError:
            if attempt < max_retries - 1:
                wait = 30 * (2 ** attempt)  # 30s, 60s
                logger.error("Rate limit hit (attempt %d/%d), retrying in %ds", attempt + 1, max_retries, wait)
                await notify(f"Rate limit reached. Retrying in {wait} seconds...")
                await asyncio.sleep(wait)
            else:
                logger.error("Rate limit exhausted after %d attempts", max_retries)
                return "Rate limit exhausted. Please try again in a few minutes."
        except (anthropic.APIStatusError, anthropic.APIConnectionError) as e:
            logger.error("Claude API error %s: %s", type(e).__name__, e)
            raise
    return "Unexpected error reaching Claude API."


class Orchestrator:
    def __init__(self, notify: Callable[[str], Awaitable[None]]) -> None:
        self._client = anthropic.AsyncAnthropic(api_key=config.ANTHROPIC_API_KEY)
        self._notify = notify
        self._context: list[dict] = []

    async def process(self, user_id: int, content: str) -> tuple[str, bool] | None:
        """Entry point for every incoming message.

        Returns None for unauthorized users (silent drop, no acknowledgment).
        Returns (response, as_file) where as_file signals the bot to send a file attachment.
        Prefixes: "file: <task>" sends response as a downloadable file.
        """
        if user_id != config.AUTHORIZED_USER_ID:
            return None

        file_mode = content.lower().startswith("file:")
        task = content[5:].strip() if file_mode else content

        destination = self._classify(task)

        try:
            response = await self._route(destination, task, file_mode=file_mode)
        except Exception as e:
            logger.error("Route to %s failed - %s: %s", destination, type(e).__name__, e)
            return f"Something went wrong ({type(e).__name__}). Details have been logged.", False

        skill_captured = await self._critic_pass(destination, task, response)
        self._update_context(task, response)
        label = f"`[{destination}]`"
        if skill_captured:
            label += " `[Skill captured]`"
        return f"{response}\n\n{label}", file_mode

    def _classify(self, content: str) -> str:
        lower = content.lower()
        if any(trigger in lower for trigger in _MONITOR_TRIGGERS):
            return "Monitor"
        return "React"

    async def _critic_pass(self, destination: str, task: str, response: str) -> bool:
        if destination not in _SKILL_FILES:
            return False
        call = functools.partial(call_claude, self._client, self._notify)
        messages = [{"role": "user", "content": f"Task: {task}\n\nResponse:\n{response}"}]
        try:
            verdict = await call(_CRITIC_PROMPT, messages, max_tokens=60)
            verdict = verdict.strip()
            logger.info("Critic verdict for %s: %s", destination, verdict[:80])
            if verdict.upper().startswith("CAPTURE:"):
                _write_skill(destination, verdict[8:].strip())
                return True
        except Exception as e:
            logger.error("Critic pass failed for %s - %s: %s", destination, type(e).__name__, e)
        return False

    def _make_caller(self, file_mode: bool = False) -> CallClaude:
        base = functools.partial(call_claude, self._client, self._notify)
        if not file_mode:
            return base

        async def _file_caller(system: str, messages: list, max_tokens: int = 4096) -> str:
            return await base(system, messages, max_tokens)

        return _file_caller

    async def _route(self, destination: str, content: str, file_mode: bool = False) -> str:
        from agents import monitor, react

        if file_mode:
            content = content + "\n\n[File output: Write a comprehensive detailed report with full prose, section headers, and thorough coverage. No bullet format constraints. No length limits.]"
        call = self._make_caller(file_mode=file_mode)
        max_tokens = 4096 if file_mode else 1024

        if destination == "Monitor":
            return await monitor.handle(content, self._window(), call)
        return await react.handle(content, self._window(), call, max_tokens=max_tokens)

    def _window(self) -> list[dict]:
        return self._context[-config.CONTEXT_WINDOW:]

    def _update_context(self, user_msg: str, assistant_msg: str) -> None:
        self._context.append({"role": "user", "content": user_msg})
        self._context.append({"role": "assistant", "content": assistant_msg})
        if len(self._context) > config.CONTEXT_WINDOW:
            self._context = self._context[-config.CONTEXT_WINDOW:]

    def reset_context(self) -> None:
        self._context.clear()
