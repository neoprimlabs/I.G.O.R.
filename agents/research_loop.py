import asyncio
import logging
import re
from typing import Awaitable, Callable, Optional

import openai

import config
import llm

logger = logging.getLogger(__name__)

_loop_task: Optional[asyncio.Task] = None
_stop_event: Optional[asyncio.Event] = None
_report_sent: bool = False

_MAX_LOOP_ITERATIONS = 100

# The research model is a reasoning model: max_tokens covers hidden reasoning
# plus output, so a tight cap returns empty content with a 200 OK rather than an
# error. CLAUDE.md puts the floor at 1024. Specced this at 200 and 600 first and
# both calls came back empty, exactly as documented.
_MIN_REASONING_BUDGET = 1024

# gpt-oss-20b accepts reasoning_effort low/medium/high. Measured on the live API
# 2026-08-10 with these exact prompts, completion tokens:
#
#   PLAN     n=4   low 42-72 (median 47)    unset 164-441 (median 355)   high 937-997
#   DISTILL  n=3   low 418-565 (median 419) unset 1328-1771 (median 1734)
#
# 4x to 7x fewer tokens. Output held: PLAN returned a valid query 4/4 at low, and
# DISTILL returned 5 findings with 5 sourced on every single run at both settings.
#
# Two things this is NOT. Groq's docs say gpt-oss defaults to high; measured, unset
# behaves nothing like high, so IGOR was not running at maximum. And a first
# single sample where high returned empty did not replicate at n=4 - that was
# variance, not a property. This is an efficiency win on a TPM-bound free tier,
# not a bug fix.
#
# The quality check is also weaker than it looks: 5 sourced findings is a format
# check, not a judgement that the findings are as good. Watch the next real run.
#
# Do NOT copy this to React without measuring. Deciding which tool to call is the
# case where reasoning plausibly earns its tokens, and it runs on a different model.
_REASONING_EFFORT = "low"

# Distillation writes 3-5 findings with URLs and reasoning, so it needs more room
# than the planner's one-line query. At 1024 roughly half of live calls came back
# empty and paid for a retry at 2048 anyway - cheaper to ask for it up front.
_DISTILL_BUDGET = 2048

# Gathering and synthesis run in separate contexts on purpose. The previous design
# handed one ReAct loop a batch of searches plus a write instruction, and ReAct
# appends every tool result to a single growing history, so raw material and the
# synthesis competed for one 8000 TPM budget. Raw material won: two runs in a row
# spent all 8 iterations searching, never reached the write, and recorded nothing.
# Here no context ever holds more than one search's results.
_PLAN_SYSTEM = """You plan one step of a research investigation.

Given the question and the findings so far, produce ONE web search query aimed at a specific angle that has not been covered yet.

Output the query alone, on one line. No quotes, no explanation, no preamble.

Search runs on Exa: do not put a year in the query, Exa ranks by recency on its own and a year suffix degrades results. Naming a source type ("research paper", "case study", "post mortem", "benchmark") gives sharper results than naming the topic alone."""

_DISTILL_SYSTEM = """You turn raw search results into durable research findings.

You are seeing the results of ONE search. They are discarded the moment you reply, so anything worth keeping has to appear in your answer.

Write 3 to 5 findings, each on its own line starting with "- ". Each finding:
- states a specific fact, number, name, or claim, not a summary of what a page is about
- ENDS with the source URL in parentheses. A finding you cannot attribute to one of the results in front of you does not get written. Drop it rather than writing it unsourced.
- says why it matters, where that is not obvious

Report only what these results actually say. You are looking at a handful of results from a single query, which tells you nothing about what exists in the world beyond them. Never write that something does not exist, that nobody is doing something, that no study covers it, or that a field is missing something. If an angle is not covered here, write "these results do not cover X" - that is a statement about the results and is fine. Anything stronger is a claim you have no way to support, and it is worse than writing nothing.

A figure a company publishes about its own product is advertising, not evidence. You may still record it, because it is often the only source of a number, but you MUST mark it: put "(vendor claim)" immediately before the URL. Never present a company's own savings, ROI or hours-saved figure as though it were independent.

NEVER write a number you inferred, estimated, extrapolated or calculated from general knowledge, and saying that you inferred it does not make it acceptable. "This figure is not cited directly in the results but is inferred from common market rates" is exactly the sentence that must never appear. If the results in front of you do not state it, it does not go in the findings.

Three well-sourced findings beat five padded ones. If nothing here is worth keeping, say so in one line rather than inventing findings.

End with one final line beginning "Next: " followed by the specific thread worth pursuing. Write the actual thread as plain text. Do not copy this instruction back, and do not use angle brackets.
For example:
Next: whether dental insurance verification vendors publish per-seat pricing

Style:
- No emojis
- No em dashes, en dashes, or any dash other than a plain hyphen
- No exclamation points
- No bold, italics, or headings. Plain lines only"""

_client: Optional[openai.AsyncOpenAI] = None


def _get_client() -> openai.AsyncOpenAI:
    global _client
    if _client is None:
        _client = openai.AsyncOpenAI(
            api_key=config.GROQ_API_KEY,
            base_url="https://api.groq.com/openai/v1",
            max_retries=5,
        )
    return _client


async def _call(system: str, user: str, max_tokens: int) -> str:
    """One isolated model call. Nothing persists between calls.

    The research model is a reasoning model, so it hits both silent failures llm
    handles. RateLimitError propagates to the loop, which stops and reports what it
    has rather than losing the run.
    """
    return await llm.complete(
        _get_client(),
        config.MODELS["research"],
        system,
        user,
        max_tokens=max_tokens,
        label="Research",
        reasoning_effort=_REASONING_EFFORT,
    )


def _timestamp() -> str:
    from datetime import datetime, timezone
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")


# The distill prompt used to end with an angle-bracket placeholder under the word
# "exactly", and the model copied it verbatim in 8 of 51 iterations on 2026-08-14.
# The prompt is fixed, but these threads are fed straight into the planner's
# do-not-repeat list, so a junk thread there wastes budget and tells the planner not
# to repeat something that was never a topic. Filtering in code as well, because the
# prompt rule is what already failed.
_PLACEHOLDER_THREAD_RE = re.compile(
    r"^(the\s+)?(single\s+)?most[- ]promising\s+thread", re.IGNORECASE
)


def _clean_thread(line: str) -> str:
    """Strip the "Next:" prefix and reject a thread that is only the placeholder."""
    thread = line[5:].strip().strip("<>").strip()
    if not thread:
        return ""
    if _PLACEHOLDER_THREAD_RE.match(thread):
        # "the most promising thread to pursue next: pet grooming salon intake" -
        # the model echoed the placeholder and then wrote a real thread after it.
        # Keep the half it actually wrote; drop it if there is nothing after.
        return thread.partition(":")[2].strip()
    return thread


def _extract_recent_threads(content: str, n: int = 5) -> str:
    threads = [
        cleaned
        for line in content.splitlines()
        if line.strip().startswith("Next:")
        for cleaned in [_clean_thread(line.strip())]
        if cleaned
    ]
    recent = threads[-n:]
    return "\n".join(f"- {t}" for t in recent) if recent else ""


def _smart_truncate(content: str, max_chars: int = 6000) -> str:
    if len(content) <= max_chars:
        return content
    lines = content.splitlines()
    header = "\n".join(lines[:5])
    cleaned = [
        t
        for line in lines
        if line.strip().startswith("Next:")
        for t in [_clean_thread(line.strip())]
        if t
    ]
    thread_block = "\n".join(f"  Next: {t}" for t in cleaned) if cleaned else "(none)"
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
    from agents import research

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
            # Raw and unfiltered, deliberately. The user's designed flow is funnel,
            # then the user sees the findings, then filtering - not a model deciding
            # on their behalf what was worth keeping. A 19KB unfiltered result was
            # collapsed and then permanently lost that way once already.
            if notify_file:
                await notify_file(contents)
                if notify:
                    await notify(
                        'Raw findings attached, nothing filtered out. '
                        'Say "synthesize research" if you want a condensed read.'
                    )
            elif notify:
                await notify(contents)

    consecutive_empty = 0
    for iteration in range(1, max_iterations + 1):
        if stop_event.is_set():
            break

        logger.info("Research loop iteration %d", iteration)

        current = research_path.read_text(encoding="utf-8") if research_path.exists() else ""
        current = _smart_truncate(current, max_chars=3000)

        threads = _extract_recent_threads(current)
        thread_section = f"\nAlready pursued, do not repeat:\n{threads}\n" if threads else ""

        size_before = research_path.stat().st_size if research_path.exists() else 0

        try:
            # 1. PLAN - sees the question and prior findings, never raw results.
            plan_user = (
                f"Question: {question}\n{thread_section}\n"
                f"Findings so far:\n{current}\n\n"
                f"Give one search query for the next unexplored angle."
            )
            query = await _call(_PLAN_SYSTEM, plan_user, max_tokens=_MIN_REASONING_BUDGET)
            query = query.splitlines()[0].strip().strip('"').strip() if query else ""
            if not query:
                query = question
                logger.warning("Iteration %d: planner returned nothing, searching the question itself", iteration)
            logger.info("Iteration %d query: %s", iteration, query[:100])

            if stop_event.is_set():
                break

            # 2. SEARCH - no model involved.
            results = await research._run_search(query, max_results=5)
            if not results:
                logger.warning("Iteration %d: search returned no results", iteration)
                results = []

            if stop_event.is_set():
                break

            findings = ""
            if results:
                # 3. DISTILL - sees only this one search. Raw results end here and
                # never reach another context.
                distill_user = (
                    f"Question: {question}\n\nSearch query: {query}\n\n"
                    f"Results:\n{research._format_results(results)}"
                )
                findings = await _call(_DISTILL_SYSTEM, distill_user, max_tokens=_DISTILL_BUDGET)

            # 4. APPEND - code, not a model call. Only writer of research.md.
            if findings:
                # Cleaned on the way in, not just on the way out to Discord. This
                # file syncs to the user's phone and gets archived, and the style
                # rules cover memory files too.
                from sanitize import clean
                block = clean(
                    f"\n## Iteration {iteration} - {_timestamp()}\n"
                    f"Query: {query}\n\n{findings}\n"
                )
                try:
                    with research_path.open("a", encoding="utf-8") as f:
                        f.write(block)
                except Exception as e:
                    logger.error("Iteration %d: could not append findings - %s: %s",
                                 iteration, type(e).__name__, e)
            else:
                logger.warning("Iteration %d: distillation produced nothing", iteration)

        except openai.RateLimitError:
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
