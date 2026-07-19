import asyncio
import logging
from typing import Awaitable, Callable, Optional

import openai

import config

logger = logging.getLogger(__name__)

_loop_task: Optional[asyncio.Task] = None
_stop_event: Optional[asyncio.Event] = None
_report_sent: bool = False

_MAX_LOOP_ITERATIONS = 100

_WORKER_SYSTEM_PROMPT = """You are a research worker executing one iteration of an autonomous research loop. Follow the instructions in the user message exactly. Use tools efficiently and keep any prose brief.

Style:
- No emojis
- No em dashes - use plain hyphens
- No exclamation points"""

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

Prohibited actions - do not call these under any circumstances:
- send_message (the loop handles user notification when complete)
- memory_write to any file other than research.md
- restart_self

Do not repeat any thread listed under "Recently pursued threads" above."""


def _timestamp() -> str:
    from datetime import datetime, timezone
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")


def _extract_recent_threads(content: str, n: int = 5) -> str:
    lines = [l.strip() for l in content.splitlines() if l.strip().startswith("Next:")]
    recent = lines[-n:] if lines else []
    return "\n".join(f"- {l[5:].strip()}" for l in recent) if recent else ""


def _smart_truncate(content: str, max_chars: int = 6000) -> str:
    if len(content) <= max_chars:
        return content
    lines = content.splitlines()
    header = "\n".join(lines[:5])
    next_lines = [l.strip() for l in lines if l.strip().startswith("Next:")]
    thread_block = "\n".join(f"  {l}" for l in next_lines) if next_lines else "(none)"
    budget = max_chars - len(header) - len(thread_block) - 150
    recent = content[-budget:] if budget > 0 else ""
    return (
        f"{header}\n\n"
        f"[Earlier findings truncated - all pursued threads below]\n"
        f"Pursued threads:\n{thread_block}\n\n"
        f"[Most recent findings:]\n{recent}"
    )


async def start(question: str, notify: Optional[Callable[[str], Awaitable[None]]] = None, notify_file: Optional[Callable[[str], Awaitable[None]]] = None, max_iterations: int = _MAX_LOOP_ITERATIONS) -> str:
    global _loop_task, _stop_event, _report_sent

    if _loop_task and not _loop_task.done():
        return "Research loop already running. Send 'stop research' to stop it and get results."

    research_path = config.MEMORY_DIR / "research.md"
    if research_path.exists() and research_path.stat().st_size > 100:
        from datetime import datetime, timezone
        stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
        archive_path = config.MEMORY_DIR / f"research_{stamp}.md"
        research_path.rename(archive_path)
        logger.info("Archived previous research to %s", archive_path.name)
    research_path.write_text(
        f"# Research: {question}\n\nStarted: {_timestamp()}\n\n---\n\n",
        encoding="utf-8",
    )

    _stop_event = asyncio.Event()
    _report_sent = False
    _loop_task = asyncio.create_task(_run(question, _stop_event, notify, notify_file, max_iterations))
    logger.info("Research loop started: %s (%d iterations)", question[:80], max_iterations)

    if max_iterations == _MAX_LOOP_ITERATIONS:
        return f"Research loop started on: {question}\n\nSend 'stop research' when you want the results."
    return f"Research loop started on: {question}\n\nRunning {max_iterations} iteration(s). Results will be sent automatically when complete."


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

    if _report_sent:
        return "Research loop already completed - results were sent automatically."

    research_path = config.MEMORY_DIR / "research.md"
    if research_path.exists():
        return research_path.read_text(encoding="utf-8")
    return "Research loop stopped. No findings were recorded."


def is_running() -> bool:
    return _loop_task is not None and not _loop_task.done()


async def _run(question: str, stop_event: asyncio.Event, notify: Optional[Callable[[str], Awaitable[None]]] = None, notify_file: Optional[Callable[[str], Awaitable[None]]] = None, max_iterations: int = _MAX_LOOP_ITERATIONS) -> None:
    from agents import react

    async def _dummy_caller(system: str, messages: list, max_tokens: int = 1024) -> str:
        return ""

    research_path = config.MEMORY_DIR / "research.md"

    async def _stop_with_report(reason: str) -> None:
        global _report_sent
        if _report_sent:
            return
        _report_sent = True
        logger.info("Research loop stopping: %s", reason)
        stop_event.set()
        if notify:
            await notify(f"Research stopped: {reason}")
        contents = research_path.read_text(encoding="utf-8") if research_path.exists() else None
        if contents:
            if notify_file:
                await notify_file(contents)
            elif notify:
                await notify(contents)

    mode_path = config.MEMORY_DIR / "research_mode.md"
    mode = mode_path.read_text(encoding="utf-8").strip() if mode_path.exists() else _DEFAULT_MODE

    consecutive_empty = 0
    for iteration in range(1, max_iterations + 1):
        if stop_event.is_set():
            break

        logger.info("Research loop iteration %d", iteration)

        current = research_path.read_text(encoding="utf-8") if research_path.exists() else ""
        current = _smart_truncate(current, max_chars=3000)

        threads = _extract_recent_threads(current)
        thread_section = f"\nRecently pursued threads (do not repeat these):\n{threads}\n" if threads else ""

        size_before = research_path.stat().st_size if research_path.exists() else 0

        prompt = f"""{mode}

---

Question: {question}
{thread_section}
Current findings:
{current}

---

Iteration {iteration}. Run your searches, fetch, write findings, stop."""

        try:
            await react.handle(
                prompt, [], _dummy_caller, max_tokens=1280, thinking=False, max_iterations=8,
                model=config.MODELS["research"],
                allowed_tools=["search", "fetch_url", "python_run", "memory_read", "memory_write", "search_memory"],
                system_override=_WORKER_SYSTEM_PROMPT,
            )
        except openai.RateLimitError as e:
            await _stop_with_report(f"rate limit on iteration {iteration} - try again later")
            break
        except Exception as e:
            await _stop_with_report(f"{type(e).__name__} on iteration {iteration}: {e}")
            break

        size_after = research_path.stat().st_size if research_path.exists() else 0
        if size_after <= size_before:
            consecutive_empty += 1
            if consecutive_empty >= 2:
                await _stop_with_report(f"2 consecutive iterations produced no findings - stopping")
                break
            logger.warning("Research loop iteration %d produced no findings - allowing one retry", iteration)
        else:
            consecutive_empty = 0

        if iteration == max_iterations:
            await _stop_with_report(f"completed {max_iterations} iteration(s)")
            break

        if iteration % 25 == 0 and notify:
            current = research_path.read_text(encoding="utf-8") if research_path.exists() else ""
            threads = _extract_recent_threads(current)
            await notify(
                f"Research checkpoint - {iteration} iterations complete.\n\n"
                f"Recent threads:\n{threads}\n\n"
                f"Still running. Send 'stop research' to get the full report."
            )

        try:
            await asyncio.wait_for(stop_event.wait(), timeout=20)
            break
        except asyncio.TimeoutError:
            pass
