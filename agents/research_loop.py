import asyncio
import logging
from typing import Awaitable, Callable, Optional

import config

logger = logging.getLogger(__name__)

_loop_task: Optional[asyncio.Task] = None
_stop_event: Optional[asyncio.Event] = None

_MAX_LOOP_ITERATIONS = 100

_DEFAULT_MODE = """You are running in deep research mode - the autoresearch pattern.

Your job is to investigate the question exhaustively. NEVER STOP on your own.

Each iteration:
1. Review the current findings provided above - understand what has already been covered
2. Identify the most promising unexplored angle or thread
3. Use search, fetch_url, and python_run to pursue it aggressively
4. Write your new findings to research.md using memory_write in append mode
   - Be specific: cite sources, quote key passages, name exact projects/papers/people
   - Structure each entry: what you found, why it matters, what it points toward
5. End your response with one line: "Next: [what thread to pursue next iteration]"

Rules:
- Never search for something already covered in the findings
- Depth over breadth - one thread pursued thoroughly beats five skimmed
- If a search returns nothing useful, immediately try a different angle
- NEVER declare the research complete - there is always more to find"""


def _timestamp() -> str:
    from datetime import datetime, timezone
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")


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

        prompt = f"""{mode}

---

Question: {question}

Current findings:
{current}

---

Iteration {iteration}. Continue the research."""

        try:
            await react.handle(prompt, [], _dummy_caller, max_tokens=2048)
        except Exception as e:
            logger.error("Research loop iteration %d failed - %s: %s", iteration, type(e).__name__, e)

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
