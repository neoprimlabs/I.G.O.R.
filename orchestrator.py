import asyncio
import functools
import logging
import re
from typing import Awaitable, Callable

import anthropic

import config

logger = logging.getLogger(__name__)

# Agents are imported inside _route() to keep this module importable before the
# agents package is fully loaded, and to make the dependency graph explicit.

_CLASSIFICATION_PROMPT = """You are a routing classifier for I.G.O.R., a personal AI assistant.

Given a user message, return exactly one word:

Monitor - system health, monitoring status, scheduled reports, trigger digest, run digest, send digest, questions about what IGOR is monitoring or watching
React   - everything else: research, tasks, memory, writing, coding, conversation, questions

One word only. No punctuation. No explanation."""


_VALID_DESTINATIONS = frozenset({"Monitor", "React"})

_GATE_PROMPT = """You are a task completion evaluator.

Given an original task and an agent's latest response, determine if the task is complete.

Respond with exactly one word: DONE or CONTINUE.
- DONE: the task is fully addressed with no significant gaps remaining
- CONTINUE: the response is incomplete or requires additional steps

Be strict - only say DONE when the task is genuinely finished."""

_MAX_LOOP_ITERATIONS = 5

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

_SKILL_PATTERN = re.compile(
    r"%%SKILL%%\s*\nagent:\s*(\S+)\s*\ncontent:\s*\n(.*?)%%END%%",
    re.DOTALL,
)


_SKILL_FILES: dict[str, str] = {
    "React": "skills_react.md",
}


def _write_skill(agent_name: str, content: str) -> None:
    filename = _SKILL_FILES.get(agent_name)
    if not filename:
        logger.warning("Skill emitted by unknown agent '%s' - skipped", agent_name)
        return
    path = config.MEMORY_DIR / filename
    try:
        with path.open("a", encoding="utf-8") as f:
            f.write(f"{content}\n")
        logger.info("Skill captured for %s", agent_name)
    except Exception as e:
        logger.error("Skill write failed for %s - %s: %s", agent_name, type(e).__name__, e)


def _extract_skills(response: str) -> tuple[str, int]:
    """Strip %%SKILL%% blocks from response and persist each skill to skills.md.

    Returns (cleaned_response, number_of_skills_captured).
    """
    count = 0
    def _handle(match: re.Match) -> str:
        nonlocal count
        _write_skill(match.group(1).strip(), match.group(2).strip())
        count += 1
        return ""
    return _SKILL_PATTERN.sub(_handle, response).strip(), count


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
        Prompt injection protection: `content` is placed in the `user` role of
        the messages array and never interpolated into any system prompt.
        Prefixes stack in order: "file: loop: <task>"
        """
        if user_id != config.AUTHORIZED_USER_ID:
            return None

        task = content
        file_mode = task.lower().startswith("file:")
        if file_mode:
            task = task[5:].strip()

        loop_mode = task.lower().startswith("loop:")
        if loop_mode:
            task = task[5:].strip()

        destination = await self._classify(task)

        try:
            if loop_mode:
                await self._notify("Working on it...")
                response, iterations = await self._loop(destination, task, file_mode=file_mode)
            else:
                response = await self._route(destination, task, file_mode=file_mode)
        except Exception as e:
            logger.error("Route to %s failed - %s: %s", destination, type(e).__name__, e)
            return f"Something went wrong ({type(e).__name__}). Details have been logged.", False

        response, _ = _extract_skills(response)
        skill_captured = await self._critic_pass(destination, task, response)
        self._update_context(task, response)
        label = f"`[{destination}]`"
        if loop_mode:
            label += f" `[{iterations} loop iterations]`"
        if skill_captured:
            label += " `[Skill captured]`"
        return f"{response}\n\n{label}", file_mode

    async def _classify(self, content: str) -> str:
        # content is passed as a user-role message, never embedded in the system prompt
        messages = self._window() + [{"role": "user", "content": content}]
        try:
            raw = await call_claude(
                self._client,
                self._notify,
                _CLASSIFICATION_PROMPT,
                messages,
                max_tokens=10,
            )
            destination = raw.strip()
            return destination if destination in _VALID_DESTINATIONS else "React"
        except Exception as e:
            logger.error("Classification failed - %s: %s", type(e).__name__, e)
            return "React"

    async def _loop(self, destination: str, task: str, file_mode: bool = False) -> tuple[str, int]:
        call: CallClaude = functools.partial(call_claude, self._client, self._notify)  # gate only, always small
        loop_context = self._window()
        current_message = task
        response = ""

        for i in range(1, _MAX_LOOP_ITERATIONS + 1):
            response = await self._route(destination, current_message, loop_context, file_mode=file_mode)

            gate_messages = [{"role": "user", "content": f"Task: {task}\n\nLatest response:\n{response}"}]
            verdict = await call(_GATE_PROMPT, gate_messages, max_tokens=10)

            if verdict.strip().upper().startswith("DONE"):
                return response, i

            loop_context = loop_context + [
                {"role": "user", "content": current_message},
                {"role": "assistant", "content": response},
            ]
            current_message = task

        logger.warning("Loop hit max iterations (%d) for %s", _MAX_LOOP_ITERATIONS, destination)
        return response, _MAX_LOOP_ITERATIONS

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

    async def _route(self, destination: str, content: str, context: list[dict] | None = None, file_mode: bool = False) -> str:
        from agents import monitor, react

        if context is None:
            context = self._window()
        if file_mode:
            content = content + "\n\n[File output: Write a comprehensive detailed report with full prose, section headers, and thorough coverage. No bullet format constraints. No length limits.]"
        call = self._make_caller(file_mode=file_mode)
        max_tokens = 4096 if file_mode else 1024

        if destination == "Monitor":
            return await monitor.handle(content, context, call)
        return await react.handle(content, context, call, max_tokens=max_tokens)

    def _window(self) -> list[dict]:
        return self._context[-config.CONTEXT_WINDOW:]

    def _update_context(self, user_msg: str, assistant_msg: str) -> None:
        self._context.append({"role": "user", "content": user_msg})
        self._context.append({"role": "assistant", "content": assistant_msg})
        if len(self._context) > config.CONTEXT_WINDOW:
            self._context = self._context[-config.CONTEXT_WINDOW:]

    def reset_context(self) -> None:
        self._context.clear()
