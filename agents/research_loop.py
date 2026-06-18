import asyncio
import logging
from typing import Awaitable, Callable, Optional

import config

logger = logging.getLogger(__name__)

_loop_task: Optional[asyncio.Task] = None
_stop_event: Optional[asyncio.Event] = None

_MAX_LOOP_ITERATIONS = 100

_DEFAULT_MODE = """You are running one iteration of a deep research loop.

Your tool budget this iteration is STRICT: 3 searches + 2 fetches + 1 write = 6 tool calls maximum.

Follow this exact sequence - do not deviate:
1. Run 2-3 searches on ONE specific unexplored angle (check current findings to avoid repeating)
2. Fetch 1-2 of the most relevant URLs from those results
3. Call memory_write to append your findings to research.md - THIS IS REQUIRED, do it before you run out of calls
4. End with "Next: [thread to pursue next iteration]"

Writing findings is not optional. If you use all your tool calls on searches and fetches without writing, the iteration produces nothing.

When writing findings:
- Be specific: cite sources, quote exact numbers, name companies and papers
- Explain why it matters and what it points toward
- Structure each finding clearly so the next iteration can build on it

Do not repeat searches already covered in the current findings above."""


def _timestamp() -> str:
    from datetime import datetime, timezone
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")


def _extract_recent_threads(content: str, n: int = 5) -> str:
    lines = [l.strip() for l in content.splitlines() if l.strip().startswith("Next:")]
    recent = lines[-n:] if lines else []
    return "\n".join(f"- {l[5:].strip()}" for l in recent) if recent else "(no thread summaries yet)"


async def start(question: str, notify: Optional[Callable[[str], Awaitable[None]]] = None) -> str:
    global _loop_task, _stop_event

    if _loop_task and not _loop_task.done():
        return "Research loop already running. Send 'stop research' to stop it and get results."

    research_path = config.MEMORY_DIR / "research.md"
    research_path.write_text(
        f"# Research: {question}\n\nStarted: {_timestamp()}\n\n---\n\n",
        encoding="utf-8",
    )

    _stop_event = asyncio.Event()
    _loop_task = asyncio.create_task(_run(question, _stop_event, notify))
    logger.info("Research loop started: %s", question[:80])

    return f"Research loop started on: {question}\n\nSend 'stop research' when you want the results."


async def stop() -> str:
    global _loop_task, _stop_event

    if _stop_event:
        _stop_event.set()
    if _loop_task and not _loop_task.done():
        _loop_task.cancel()
        try:
            await _loop_task
        except (asyncio.CancelledError, Exception):
            pass

    logger.info("Research loop stopped")

    research_path = config.MEMORY_DIR / "research.md"
    if research_path.exists():
        return research_path.read_text(encoding="utf-8")
    return "Research loop stopped. No findings were recorded."


def is_running() -> bool:
    return _loop_task is not None and not _loop_task.done()


async def _run(question: str, stop_event: asyncio.Event, notify: Optional[Callable[[str], Awaitable[None]]] = None) -> None:
    from agents import react

    async def _dummy_caller(system: str, messages: list, max_tokens: int = 1024) -> str:
        return ""

    mode_path = config.MEMORY_DIR / "research_mode.md"
    mode = mode_path.read_text(encoding="utf-8").strip() if mode_path.exists() else _DEFAULT_MODE

    for iteration in range(1, _MAX_LOOP_ITERATIONS + 1):
        if stop_event.is_set():
            break

        logger.info("Research loop iteration %d", iteration)

        research_path = config.MEMORY_DIR / "research.md"
        current = research_path.read_text(encoding="utf-8") if research_path.exists() else ""
        if len(current) > 15000:
            current = "[Earlier findings truncated]\n\n" + current[-15000:]

        prompt = f"""{mode}

---

Question: {question}

Current findings:
{current}

---

Iteration {iteration}. Run your searches, fetch, write findings, stop."""

        try:
            await react.handle(prompt, [], _dummy_caller, max_tokens=2048, thinking=False, max_iterations=8)
        except Exception as e:
            logger.error("Research loop iteration %d failed - %s: %s", iteration, type(e).__name__, e)

        if iteration % 25 == 0 and notify:
            current = (config.MEMORY_DIR / "research.md").read_text(encoding="utf-8") if (config.MEMORY_DIR / "research.md").exists() else ""
            threads = _extract_recent_threads(current)
            await notify(
                f"Research checkpoint - {iteration} iterations complete.\n\n"
                f"Recent threads:\n{threads}\n\n"
                f"Still running. Send 'stop research' to get the full report."
            )

        if iteration == _MAX_LOOP_ITERATIONS:
            logger.info("Research loop hit max iterations (%d), auto-stopping", _MAX_LOOP_ITERATIONS)
            if notify:
                await notify(f"Research loop complete after {_MAX_LOOP_ITERATIONS} iterations. Send 'stop research' to retrieve the full report.")
            break

        try:
            await asyncio.wait_for(stop_event.wait(), timeout=20)
            break
        except asyncio.TimeoutError:
            pass
