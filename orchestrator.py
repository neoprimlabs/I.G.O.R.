import asyncio
import functools
import logging
from typing import Awaitable, Callable

import openai

import config

logger = logging.getLogger(__name__)

# Exact commands only. The broad substring triggers this replaced sent any message
# containing "digest", "watchlist" or "scheduler" to the read-only Monitor, so
# "deep research on digest formats" never reached the research branch and
# "drop weather from the digest" hit an agent that cannot write. The router owns
# intent now; these are here purely so a known command never costs a model call.
_DIGEST_COMMANDS = frozenset({
    "trigger digest", "run digest", "send digest",
    "fire digest", "morning digest",
})

# Deviates from the GAMEPLAN text, which ended the CONFIG line with "preferences".
# Live testing showed llama-3.1-8b reading opinion questions ("what do you think
# about self hosting") as CONFIG, because "preferences" reads as "opinions" as
# easily as "saved settings". CONFIG is now stated as an action on stored config,
# and CHAT explicitly claims opinion questions.
_ROUTER_PROMPT = """Classify the user message into exactly one word from this list:
CHAT - greetings, small talk, opinions, what you think about something, questions about yourself, anything social
TASK - requests to do work: search, write, analyze, code, read files, produce documents, calculations
MONITOR - questions asking about scheduler status, watchlist contents, digest contents, system health
CONFIG - requests to CHANGE saved settings: add or remove a digest section, change a schedule time, edit the watchlist
RESEARCH - requests to start deep or long-running research
Reply with the single word only."""

_VERDICT_MAP = {
    "CHAT": "Direct",
    "TASK": "React",
    "MONITOR": "Monitor",
    "CONFIG": "ConfigEdit",
    "RESEARCH": "ResearchLoop",
}

_ROUTER_TIMEOUT_S = 15

_RESEARCH_LOOP_TRIGGERS = frozenset({
    "deep research",
    "research loop",
    "autoresearch",
    "start research loop",
})

_STOP_RESEARCH_TRIGGERS = frozenset({
    "stop research",
    "stop the research",
    "end research",
    "halt research",
})

_CRITIC_PROMPT = """You are a skill evaluator for an AI assistant system.

Given a task and a response, decide if the approach used is worth capturing as a reusable skill.

Capture if the response shows:
- A multi-step search sequence that worked well (e.g. checked memory first, then searched for X before Y)
- A domain insight or constraint that filtered results usefully (e.g. applied hardware limits to narrow recommendations)
- A user-specific adaptation that improved relevance

Skip if:
- The response is a single lookup or a factual answer from training data
- The approach was obvious given the question
- Nothing about the method would help future responses
- The approach is just an output format or document structure (tables, section headings, bullet layouts, checklists) - formatting is not a skill
- A similar skill already appears in the existing skills list below

Respond with exactly one line:
CAPTURE: [one concrete sentence - what to do, not just what happened]
SKIP

One line only. No explanation."""

def _extract_research_question(content: str) -> tuple[str, int]:
    import re
    lower = content.lower()
    for trigger in sorted(_RESEARCH_LOOP_TRIGGERS, key=len, reverse=True):
        if lower.startswith(trigger):
            remainder = content[len(trigger):].lstrip(": ").strip()
            match = re.match(r'^\[(\d+)\]\s*', remainder)
            if match:
                n = max(1, min(int(match.group(1)), 100))
                return remainder[match.end():].strip(), n
            return remainder, 100
    return content.strip(), 100


# Intentionally empty. The critic below is disabled (config.ENABLE_CRITIC) and
# skills_react.md is gone: it injected unreviewed captures into every React prompt
# with nothing maintaining it, and one stale formatting entry spent weeks steering
# answers toward tables while prompt edits failed to override it. _critic_pass
# returns immediately on an empty map, so the machinery below is inert rather than
# writing to a file nothing reads. GAMEPLAN R4.4 replaces this with review files
# and explicit sign-off, and will not reuse this storage.
_SKILL_FILES: dict[str, str] = {}


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
    client: openai.AsyncOpenAI,
    notify: Callable[[str], Awaitable[None]],
    system: str,
    messages: list[dict],
    max_tokens: int = 1024,
    model: str | None = None,
) -> str:
    all_messages = [{"role": "system", "content": system}] + messages
    max_retries = 3
    for attempt in range(max_retries):
        try:
            response = await client.chat.completions.create(
                model=model or config.MODELS["chat"],
                messages=all_messages,
                max_tokens=max_tokens,
            )
            return response.choices[0].message.content or ""
        except openai.RateLimitError:
            if attempt < max_retries - 1:
                wait = 30 * (2 ** attempt)
                logger.error("Rate limit hit (attempt %d/%d), retrying in %ds", attempt + 1, max_retries, wait)
                await notify(f"Rate limit reached. Retrying in {wait} seconds...")
                await asyncio.sleep(wait)
            else:
                logger.error("Rate limit exhausted after %d attempts", max_retries)
                return "Rate limit exhausted. Please try again in a few minutes."
        except (openai.APIStatusError, openai.APIConnectionError) as e:
            logger.error("Groq API error %s: %s", type(e).__name__, e)
            raise
    return "Unexpected error reaching Groq API."


class Orchestrator:
    def __init__(self, notify: Callable[[str], Awaitable[None]], notify_file: Callable[[str], Awaitable[None]] | None = None) -> None:
        self._client = openai.AsyncOpenAI(
            api_key=config.GROQ_API_KEY,
            base_url="https://api.groq.com/openai/v1",
            max_retries=5,
        )
        self._notify = notify
        self._notify_file = notify_file or notify
        from context_store import load
        self._context: list[dict] = load()

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

        # A "file:" request is always document work regardless of what it asks
        # for, so it skips the router and goes straight to the tool agent.
        destination = "React" if file_mode else await self._classify(task)
        if destination == "StopResearch":
            file_mode = True

        try:
            response = await self._route(destination, task, file_mode=file_mode)
        except openai.RateLimitError:
            logger.error("Route to %s rate limited after all retries", destination)
            return "Groq free tier rate limit hit. Wait a minute and try again - shorter requests recover faster.", False
        except openai.APIStatusError as e:
            if "rate_limit_exceeded" in str(e) or "Request too large" in str(e):
                logger.error("Route to %s request too large for TPM budget: %s", destination, str(e)[:200])
                return "That request plus conversation context exceeds the per-minute token budget. Wait a minute, then try a shorter message - or split the task into smaller steps.", False
            logger.error("Route to %s failed - %s: %s", destination, type(e).__name__, e)
            return f"Something went wrong ({type(e).__name__}). Details have been logged.", False
        except Exception as e:
            logger.error("Route to %s failed - %s: %s", destination, type(e).__name__, e)
            return f"Something went wrong ({type(e).__name__}). Details have been logged.", False

        if destination in ("ResearchLoop", "StopResearch"):
            return response, file_mode

        skill_captured = False
        if config.ENABLE_CRITIC:
            skill_captured = await self._critic_pass(destination, task, response)
        self._update_context(task, response)
        if file_mode:
            return response, file_mode
        parts = []
        if destination == "Monitor":
            parts.append("`[Monitor]`")
        if skill_captured:
            parts.append("`[Skill captured]`")
        suffix = "\n\n" + " ".join(parts) if parts else ""
        return f"{response}{suffix}", file_mode

    async def _classify(self, content: str) -> str:
        lower = content.lower().strip()

        if any(lower.startswith(trigger) for trigger in _RESEARCH_LOOP_TRIGGERS):
            return "ResearchLoop"
        if any(trigger in lower for trigger in _STOP_RESEARCH_TRIGGERS):
            return "StopResearch"
        if lower in _DIGEST_COMMANDS:
            return "Monitor"

        # The router runs on its own mostly-idle 6000 TPM bucket and reserves 10
        # tokens, so it costs almost nothing. Called directly rather than through
        # call_claude because call_claude's rate-limit path notifies the user and
        # sleeps 30s or more, which is the wrong behaviour for classification -
        # better to fail fast to React than to make the user wait to be routed.
        try:
            response = await asyncio.wait_for(
                self._client.chat.completions.create(
                    model=config.MODELS["router"],
                    messages=[
                        {"role": "system", "content": _ROUTER_PROMPT},
                        {"role": "user", "content": content[:1000]},
                    ],
                    max_tokens=10,
                    temperature=0,
                ),
                timeout=_ROUTER_TIMEOUT_S,
            )
            raw = (response.choices[0].message.content or "").strip().upper()
        except Exception as e:
            logger.warning("Router unavailable (%s: %s) - defaulting to React", type(e).__name__, e)
            return "React"

        verdict = raw.split()[0].strip(".,:;\"'") if raw.split() else ""
        destination = _VERDICT_MAP.get(verdict, "React")
        logger.info("Router: %s -> %s", verdict or "(empty)", destination)
        return destination

    async def _critic_pass(self, destination: str, task: str, response: str) -> bool:
        if destination not in _SKILL_FILES:
            return False
        call = functools.partial(call_claude, self._client, self._notify)
        skills_path = config.MEMORY_DIR / _SKILL_FILES[destination]
        existing = skills_path.read_text(encoding="utf-8").strip() if skills_path.exists() else "(none)"
        messages = [{"role": "user", "content": f"Existing skills:\n{existing}\n\nTask: {task}\n\nResponse:\n{response}"}]
        try:
            verdict = await call(_CRITIC_PROMPT, messages, max_tokens=1024, model=config.MODELS["summary"])
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
        from agents import monitor, react, research_loop

        if destination == "ResearchLoop":
            question, iterations = _extract_research_question(content)
            return await research_loop.start(question, self._notify, notify_file=self._notify_file, max_iterations=iterations)

        if destination == "StopResearch":
            return await research_loop.stop()

        if file_mode:
            content = content + "\n\n[File output: Write a comprehensive detailed report with full prose, section headers, and thorough coverage. Return the complete document as your response text - do not write it to a server file, do not call write_file, do not mention restarts.]"
        call = self._make_caller(file_mode=file_mode)
        max_tokens = 3072 if file_mode else 2048

        if destination == "Monitor":
            return await monitor.handle(content, self._window(), call)

        if destination == "Direct":
            from agents import direct
            return await direct.handle(content, self._window(), call)

        if destination == "ConfigEdit":
            from agents import prod_memory
            return await prod_memory.handle(content, call)

        react.set_notify(self._notify)
        response = await react.handle(content, self._window(), max_tokens=max_tokens)

        if file_mode:
            from agents import evaluator
            passed, feedback = await evaluator.evaluate(content, response)
            if not passed:
                retry_content = (
                    f"{content}\n\n[Your previous attempt was rejected by an independent evaluator: "
                    f"{feedback}. Produce a corrected, complete response.]"
                )
                response = await react.handle(retry_content, self._window(), max_tokens=max_tokens)
                passed, feedback = await evaluator.evaluate(content, response)
                if not passed:
                    response = f"[Evaluator warning: {feedback}]\n\n{response}"
        return response

    def _window(self) -> list[dict]:
        return self._context[-config.CONTEXT_WINDOW:]

    def _update_context(self, user_msg: str, assistant_msg: str) -> None:
        from context_store import append
        if len(user_msg) > 1500:
            user_msg = user_msg[:1500] + "\n[truncated in context]"
        if len(assistant_msg) > 1500:
            assistant_msg = assistant_msg[:1500] + "\n[truncated in context]"
        self._context.append({"role": "user", "content": user_msg})
        self._context.append({"role": "assistant", "content": assistant_msg})
        if len(self._context) > config.CONTEXT_WINDOW:
            self._context = self._context[-config.CONTEXT_WINDOW:]
        append("user", user_msg)
        append("assistant", assistant_msg)

    def reset_context(self) -> None:
        self._context.clear()
        from context_store import clear
        clear()
