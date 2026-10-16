"""The overnight runner: one work session per hourly tick, on gpt-oss-20b only.

A session is a fixed pipeline in code. Model calls carry no tools, web search is done
by code, and files are written only inside memory/work/. tick() is the scheduled entry
point and never raises.
"""

import logging
import re
from datetime import datetime, timezone
from typing import Optional

import openai

import config
import goals
import llm
import work

logger = logging.getLogger(__name__)

_STEPS_PER_SESSION = 3
_PLAN_BUDGET = 1024
_DISTILL_BUDGET = 2048
_WRITE_BUDGET = 3072
_REASONING_EFFORT = "low"
_TRIGGER_FINDINGS = 12
_TRIGGER_SESSIONS = 3
# 20b is an 8000 TPM bucket and the write call asks for 3072 on top of its prompt.
_WRITE_INPUT_CHARS = 9000
_WRITE_RESERVE = _WRITE_BUDGET + _WRITE_INPUT_CHARS // 3 + 500

_WRITE_SYSTEM = """You write a short research paper from a list of sourced findings.

You receive a goal and findings gathered for it. Each finding ends with the URL it came from. Write the paper in a plain, neutral voice.

Format, in markdown:
- A title as a level-one heading
- A short summary paragraph
- Sections as level-two headings, each making its points from the findings
- Cite the source inline as a markdown link every time a claim is made, using the URLs given in the findings
- A final section headed exactly "## What I am unsure about" listing gaps, weak sources, vendor claims and anything the findings do not settle

Use only what the findings state. Do not add numbers, names or claims from general knowledge. A claim that has no URL in the findings does not go in the paper. Never say that something does not exist or that nobody is doing it; say that the findings do not cover it.

Style:
- No emojis
- No em dashes - use plain hyphens
- No exclamation points
- No casual filler phrases ("Sure!", "Of course!", "Happy to help!")"""

_SESSION_RE = re.compile(r"<!-- session (\d+) -->")
_WRITTEN_MARK = "<!-- paper written -->"
_LIMIT_RE = re.compile(r"Limit\s+(\d+)")
_LINK_RE = re.compile(r"https?://[^\s)>\]\"']+")


def paper_is_finished(text: str) -> tuple[bool, str]:
    if not re.search(r"^##\s+What I am unsure about\s*$", text, re.IGNORECASE | re.MULTILINE):
        return False, "no 'What I am unsure about' section"
    links = {m.rstrip(".,;") for m in _LINK_RE.findall(text)}
    if len(links) < 3:
        return False, f"only {len(links)} distinct source links, needs 3"
    return True, ""


def _real_search():
    from agents import research

    return research._run_search


def _real_complete():
    from agents import research_loop

    async def call(system: str, user: str, max_tokens: int) -> str:
        return await llm.complete(
            research_loop._get_client(), config.MODELS["research"], system, user,
            max_tokens=max_tokens, label="Overnight", reasoning_effort=_REASONING_EFFORT)

    return call


def _findings_state(text: str) -> tuple[int, int, int]:
    """(next session number, findings since the last paper, sessions since the last paper)."""
    numbers = [int(n) for n in _SESSION_RE.findall(text)]
    since = text.rsplit(_WRITTEN_MARK, 1)[-1]
    findings = sum(1 for line in since.splitlines() if line.startswith("- "))
    return (max(numbers) + 1 if numbers else 1), findings, len(_SESSION_RE.findall(since))


def _unique(folder, stem: str, night: str):
    path = folder / f"{stem}-{night}.md"
    n = 2
    while path.exists():
        path = folder / f"{stem}-{night}-{n}.md"
        n += 1
    return path


async def _research_session(goal: dict, state: dict, search, complete, night: str,
                            searches_left: int, token_room: int) -> str:
    from agents import evaluator, research, research_loop
    from sanitize import clean

    folder = work.folder(goal["id"])
    folder.mkdir(parents=True, exist_ok=True)
    findings_path = folder / "findings.md"
    existing = findings_path.read_text(encoding="utf-8") if findings_path.exists() else ""
    number, _, _ = _findings_state(existing)
    header = ""
    if not existing:
        header = f"# Findings: {goal['statement']}\n"
    start_tokens = work.overnight_tokens()

    next_action = goal["next_action"]
    added = 0
    marker_written = False
    search_error = ""
    for _ in range(min(_STEPS_PER_SESSION, searches_left)):
        if work.overnight_tokens() - start_tokens >= token_room:
            break
        current = findings_path.read_text(encoding="utf-8") if findings_path.exists() else ""
        current = research_loop._smart_truncate(current, max_chars=3000)
        threads = research_loop._extract_recent_threads(current)
        thread_section = f"\nAlready pursued, do not repeat:\n{threads}\n" if threads else ""
        hint = "" if next_action in ("", "start") else f"\nSuggested next thread: {next_action}\n"
        plan_user = (
            f"Question: {goal['statement']}\n{thread_section}{hint}\n"
            f"Findings so far:\n{current}\n\n"
            f"Give one search query for the next unexplored angle."
        )
        query = await complete(research_loop._PLAN_SYSTEM, plan_user, _PLAN_BUDGET)
        query = query.splitlines()[0].strip().strip('"').strip() if query else ""
        if not query:
            query = goal["statement"]

        state["searches"] += 1
        try:
            results = await search(query, max_results=5)
        except Exception as e:
            logger.error("Overnight search failed - %s: %s", type(e).__name__, e)
            search_error = f", search failed ({type(e).__name__})"
            break
        if not results:
            continue

        distill_user = (
            f"Question: {goal['statement']}\n\nSearch query: {query}\n\n"
            f"Results:\n{research._format_results(results)}"
        )
        findings = await complete(research_loop._DISTILL_SYSTEM, distill_user, _DISTILL_BUDGET)
        if not findings:
            continue

        block = ""
        if not marker_written:
            block += f"\n<!-- session {number} -->\n"
            marker_written = True
        block += clean(f"\n## {query}\n\n{findings}\n")
        with findings_path.open("a", encoding="utf-8") as f:
            if header:
                f.write(header)
                header = ""
            f.write(block)
        rel_findings = str(findings_path.relative_to(config.MEMORY_DIR))
        if rel_findings not in state["files"]:
            state["files"].append(rel_findings)
        added += sum(1 for line in findings.splitlines() if line.startswith("- "))
        for line in findings.splitlines():
            if line.strip().startswith("Next:"):
                thread = research_loop._clean_thread(line.strip())
                if thread:
                    next_action = thread

    summary = f"{added} new findings{search_error}"
    text = findings_path.read_text(encoding="utf-8") if findings_path.exists() else ""
    _, new_findings, new_sessions = _findings_state(text)
    attempt_mark = f"<!-- write attempted {night} -->"
    room = token_room - (work.overnight_tokens() - start_tokens)
    triggered = new_findings and (new_findings >= _TRIGGER_FINDINGS
                                  or new_sessions >= _TRIGGER_SESSIONS)
    if triggered and attempt_mark in text:
        summary += ", paper write already failed tonight"
    elif triggered and room < _WRITE_RESERVE:
        summary += ", paper write waits for budget"
    elif triggered:
        body = text[-_WRITE_INPUT_CHARS:]
        write_user = f"Goal: {goal['statement']}\n\nFindings:\n{body}"
        paper = await complete(_WRITE_SYSTEM, write_user, _WRITE_BUDGET)
        if paper:
            ok, why = paper_is_finished(paper)
            if ok:
                paper_path = _unique(folder, "paper", night)
                task = (f"Write a research paper for this goal: {goal['statement']}. Cite sources "
                        f"inline and end with a section headed 'What I am unsure about'.")
                passed, feedback = await evaluator.evaluate(task, paper, label="Overnight")
                if not passed and feedback:
                    paper = paper.rstrip() + f"\n\n## Reviewer note\n{feedback}\n"
                paper_path.write_text(clean(paper) + "\n", encoding="utf-8")
                moved = _unique(folder, "findings", night)
                findings_path.replace(moved)
                rel = str(paper_path.relative_to(config.MEMORY_DIR))
                state["files"].extend([rel, str(moved.relative_to(config.MEMORY_DIR))])
                state["finished"].append(rel)
                summary += f", paper finished ({paper_path.name})"
            else:
                draft_path = folder / f"draft-{night}.md"
                draft_path.write_text(clean(paper) + "\n", encoding="utf-8")
                with findings_path.open("a", encoding="utf-8") as f:
                    f.write(f"\n{_WRITTEN_MARK}\n")
                state["files"].append(str(draft_path.relative_to(config.MEMORY_DIR)))
                summary += f", draft kept: {why}"
        else:
            with findings_path.open("a", encoding="utf-8") as f:
                f.write(f"\n{attempt_mark}\n")
            summary += ", paper write returned nothing"
    state["next_action"] = next_action
    return summary


async def _self_improve_session(goal: dict, state: dict, search, complete, night: str,
                                searches_left: int, token_room: int) -> str:
    state["next_action"] = goal["next_action"]
    return "self-improvement arrives in Task 4"


async def tick(now: Optional[datetime] = None, search=None, complete=None) -> str:
    """One scheduled work session. Never raises."""
    now = now or datetime.now(timezone.utc)
    state: dict = {"searches": 0, "files": [], "finished": [], "stopped": "",
                   "next_action": ""}
    goal = None
    start_tokens = 0
    try:
        if not work.in_window(now):
            return "outside the overnight window"
        night = work.night_of(now)
        if work.stopped(night):
            return f"stopped tonight: {work.stopped(night)}"
        tokens, searches = work.spent(night)
        if tokens >= config.OVERNIGHT_TOKENS:
            return "token budget spent"
        if searches >= config.OVERNIGHT_SEARCHES:
            return "search budget spent"
        goal = goals.next_to_work()
        if goal is None:
            return "no active goals"

        search = search or _real_search()
        complete = complete or _real_complete()
        start_tokens = work.overnight_tokens()
        state["next_action"] = goal["next_action"]
        runner = _self_improve_session if goal["kind"] == "self_improve" else _research_session
        summary = await runner(goal, state, search, complete, night,
                               config.OVERNIGHT_SEARCHES - searches,
                               config.OVERNIGHT_TOKENS - tokens)
        used = max(0, work.overnight_tokens() - start_tokens)
        work.log_session(goal["id"], goal["kind"], summary, used,
                         state["searches"], state["files"], state["finished"], now=now)
        goals.note_progress(goal["id"], state["next_action"], now=now)
        return f"{goal['id']}: {summary}"
    except openai.RateLimitError as e:
        if llm.is_daily_cap(e):
            match = _LIMIT_RE.search(str(e))
            stopped = f"daily cap 20b: {match.group(1) if match else 'unknown'}"
        else:
            stopped = "rate limited"
        logger.error("Overnight stopped: %s", stopped)
        _log_failure(goal, state, start_tokens, now, f"stopped: {stopped}", stopped)
        return stopped
    except Exception as e:
        logger.error("Overnight tick failed - %s: %s", type(e).__name__, e, exc_info=True)
        _log_failure(goal, state, start_tokens, now, f"error: {type(e).__name__}", "")
        return f"error: {type(e).__name__}"


def _log_failure(goal: Optional[dict], state: dict, start_tokens: int, now: datetime,
                 summary: str, stopped: str) -> None:
    if goal is None:
        return
    try:
        used = max(0, work.overnight_tokens() - start_tokens)
        work.log_session(goal["id"], goal["kind"], summary, used, state["searches"],
                         state["files"], state["finished"], stopped, now=now)
    except Exception as e:
        logger.error("Could not log the failed session - %s: %s", type(e).__name__, e)
