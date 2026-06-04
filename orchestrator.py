import asyncio
import functools
import logging
from typing import Awaitable, Callable

import anthropic

import config

logger = logging.getLogger(__name__)

# Agents are imported inside _route() to keep this module importable before the
# agents package is fully loaded, and to make the dependency graph explicit.

_CLASSIFICATION_PROMPT = """You are a routing classifier for I.G.O.R., a personal AI assistant.

Given a user message, return exactly one word - the name of the agent that should handle it:

Dev        - programming, debugging, architecture, code review, technical questions, dev tools
Research   - web search, fact-finding, looking things up, current events, article summaries
ProdMemory - ALWAYS use this for: add a task, remember, store, note this, add to my list, what are my tasks, what am I working on, project status, add a project, update my notes, what's pending, scheduling, reminders
Comms      - drafting messages, emails, posts, editing writing, proofreading
Monitor    - system health, monitoring status, scheduled reports
Direct     - general conversation, questions about I.G.O.R., anything that doesn't fit above

IMPORTANT: Any message containing "add a task", "remember", "add a project", or "note this" MUST route to ProdMemory. Never route these to Direct.

One word only. No punctuation. No explanation."""

_DIRECT_SYSTEM_PROMPT = """You are I.G.O.R. (Interactive Guidance and Operational Recognition) - a personal AI assistant.

Personality: Formal but warm. Confident, composed, precise. Hyper-aware and always thinking ahead. Completely focused on serving the user.

Principles:
- Truth over comfort. Push back. Flag issues. Deliver honest assessments without softening them.
- Agreement is earned, not given by default.
- When you don't know something, say so immediately and offer options. Never guess or bluff.
- Concise by default. Thorough when asked.
- Never robotic, never vague.
- Address the user as "Creator" occasionally - once per response at most, only when it feels natural. Never force it.
- You have web search capability via the Research agent. Do not tell the user you cannot browse the internet."""

_VALID_DESTINATIONS = frozenset({"Dev", "Research", "ProdMemory", "Comms", "Monitor", "Direct"})

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
    max_retries = 3
    for attempt in range(max_retries):
        try:
            response = await client.messages.create(
                model=config.MODEL,
                system=system,
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

    async def process(self, user_id: int, content: str) -> str | None:
        """Entry point for every incoming message.

        Returns None for unauthorized users (silent drop, no acknowledgment).
        Prompt injection protection: `content` is placed in the `user` role of
        the messages array and never interpolated into any system prompt.
        """
        if user_id != config.AUTHORIZED_USER_ID:
            return None

        destination = await self._classify(content)

        try:
            response = await self._route(destination, content)
        except Exception as e:
            logger.error("Route to %s failed - %s: %s", destination, type(e).__name__, e)
            return f"Something went wrong ({type(e).__name__}). Details have been logged."

        self._update_context(content, response)
        return response

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
            return destination if destination in _VALID_DESTINATIONS else "Direct"
        except Exception as e:
            logger.error("Classification failed - %s: %s", type(e).__name__, e)
            return "Direct"

    async def _route(self, destination: str, content: str) -> str:
        from agents import comms, dev, monitor, prod_memory, research

        context = self._window()
        call: CallClaude = functools.partial(call_claude, self._client, self._notify)

        handlers: dict[str, Callable] = {
            "Dev": dev.handle,
            "Research": research.handle,
            "ProdMemory": prod_memory.handle,
            "Comms": comms.handle,
            "Monitor": monitor.handle,
        }

        handler = handlers.get(destination)
        if handler is None:
            return await self._handle_direct(content, context, call)
        return await handler(content, context, call)

    async def _handle_direct(self, content: str, context: list[dict], call: CallClaude) -> str:
        messages = context + [{"role": "user", "content": content}]
        return await call(_DIRECT_SYSTEM_PROMPT, messages)

    def _window(self) -> list[dict]:
        return self._context[-config.CONTEXT_WINDOW:]

    def _update_context(self, user_msg: str, assistant_msg: str) -> None:
        self._context.append({"role": "user", "content": user_msg})
        self._context.append({"role": "assistant", "content": assistant_msg})
        if len(self._context) > config.CONTEXT_WINDOW:
            self._context = self._context[-config.CONTEXT_WINDOW:]

    def reset_context(self) -> None:
        self._context.clear()
