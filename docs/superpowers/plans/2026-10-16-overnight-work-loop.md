# Overnight Work Loop Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** IGOR works on standing goals overnight on gpt-oss-20b, writes results as files in `memory/work/`, and reports them in the morning digest.

**Architecture:** Fixed pipelines in code, no tools that act (the research loop's shape). `goals.py` owns `memory/goals.md` (GAMEPLAN A.1 format). `work.py` owns the work folder, the per-night log and the budget. `agents/overnight.py` runs one session per hourly tick. Approved preferences live in `memory/preferences.md` and are read by Direct, React and job ranking.

**Tech Stack:** Python 3.10, APScheduler (existing), Groq via `llm.complete`, Exa via `agents/research.py`. No new dependencies.

**Spec:** `docs/superpowers/specs/2026-10-16-overnight-work-loop-design.md`

## Global Constraints

- Never call `openai/gpt-oss-120b`. Every overnight model call uses `config.MODELS["research"]` (gpt-oss-20b) via `llm.complete(..., label="Overnight")`.
- Budget per night: `config.OVERNIGHT_TOKENS = 60000`, `config.OVERNIGHT_SEARCHES = 12`.
- Window: local hours 00:00-06:59 (`clock.USER_TZ`). Outside it a tick does nothing and costs nothing.
- Files are written only inside `config.MEMORY_DIR / "work"`. Nothing is sent to Discord overnight.
- Every scheduled entry point never raises; failures are logged.
- A paper is finished only if it has a `## What I am unsure about` section and at least 3 distinct `http` links (checked in code). The Evaluator is advisory - it fails open.
- Paper (re)write trigger: 12 new findings, or 3 research sessions since the last write.
- Clone-ready: no user names, persona or personal facts in code.
- Every prompt carries the CLAUDE.md style block (no emojis, no em dashes, no exclamation points, no filler).
- Tests are stdlib scripts in the repo's style (`_check`, `python tests/<file>.py`, `all pass`), no API calls.
- Any change to ARCHITECTURE.md must keep `tests/test_read_file_paging.py` and `tests/test_self_describe.py` passing (the file is at its size limit; condense to make room).

## Review Focus

1. 20b answers 429 on a night's first call: the night stops, a daily-cap 429 is recorded with its limit, nothing raises. Test in Task 3.
2. `goals.md` edited by hand with a missing or misspelled field: that goal is skipped and logged, others still run. Test in Task 1.
3. Restart between sessions: the next tick continues from `next_action` and the existing `findings.md`, nothing is lost or repeated. Test in Task 3.
4. A goal id containing `../` or `/`: no file is written outside `memory/work/`. Test in Task 2.
5. A quiet night (no active goals, or all sessions made no progress): the digest section is empty and nothing is attached. Test in Task 5.

---

### Task 1: Goals file and the `goal` tool

**Files:**
- Create: `goals.py`
- Modify: `agents/react.py` (tool schema, dispatch, one prompt line)
- Test: `tests/test_goals.py`

**Interfaces:**
- Produces:
  - `goals.load() -> list[dict]` - keys `id, statement, kind, status, created, last_reviewed, next_action`; malformed blocks skipped with a warning.
  - `goals.add(statement: str, kind: str, now: datetime | None = None) -> str` - kind in `("research", "self_improve")`; id `g<n>`.
  - `goals.set_status(goal_id: str, status: str) -> str` - status in `("active", "paused")`; `"retired"` moves the block to `goals_archive.md`.
  - `goals.next_to_work() -> dict | None` - the active goal with the oldest `last_reviewed`.
  - `goals.note_progress(goal_id: str, next_action: str, now: datetime | None = None) -> None`
  - `goals.run_tool(inputs: dict) -> str` - actions `add, list, pause, resume, retire`.

File format, one block per goal:

```
## g1 Research autonomous systems towards a feature the user could build for income
kind: research
status: active
created: 2026-10-16
last_reviewed: 2026-10-16T00:00:00+00:00
next_action: start
```

- [ ] **Step 1: Write the failing tests** `test_add_and_load`, `test_next_to_work_picks_oldest_active`, `test_pause_skips`, `test_retire_archives`, `test_malformed_block_skipped` (a block missing `kind:` is skipped, the other goal still loads), `test_run_tool_actions`.
- [ ] **Step 2: Run** `python tests/test_goals.py` - expect ImportError.
- [ ] **Step 3: Implement `goals.py`** with atomic writes (write `.tmp`, `os.replace`), as `agents/scheduled.py` does. Add the `goal` tool to React (read-and-write tool; add it to `_QUARANTINED_AFTER_WEB` and `_WORKER_FORBIDDEN`, and update `tests/test_delegation.py`'s asserted set).
- [ ] **Step 4: Run** the new test plus `tests/test_delegation.py` and `tests/test_quarantine.py` - `all pass`.
- [ ] **Step 5: Commit** `goals.py`, `agents/react.py`, the tests.

### Task 2: Work folder, night log and budget

**Files:**
- Create: `work.py`
- Modify: `config.py` (add `OVERNIGHT_TOKENS = 60000`, `OVERNIGHT_SEARCHES = 12`)
- Test: `tests/test_work.py`

**Interfaces:**
- Consumes: `usage.last_24h()`.
- Produces:
  - `work.folder(goal_id: str) -> Path` - `MEMORY_DIR/work/<safe id>`; the id is reduced to `[a-z0-9_-]`, an empty result raises `ValueError`.
  - `work.night_of(now: datetime) -> str` - local date the night started (`YYYY-MM-DD`; 00:00-06:59 belongs to the same date).
  - `work.in_window(now: datetime) -> bool`
  - `work.log_session(goal_id: str, kind: str, summary: str, tokens: int, searches: int, files: list[str], finished: list[str], stopped: str = "", now: datetime | None = None) -> None` - appends to `MEMORY_DIR/work/log.json`, keeps the newest 300 entries.
  - `work.spent(night: str) -> tuple[int, int]` - tokens and searches logged for that night.
  - `work.stopped(night: str) -> str` - the `stopped` reason if any session that night set one, else `""`.
  - `work.overnight_tokens() -> int` - current `usage.last_24h()["openai/gpt-oss-20b"].get("Overnight", 0)`, used for before/after deltas.

- [ ] **Step 1: Write the failing tests** `test_folder_cannot_escape` (ids `"../x"`, `"a/b"`, `"..\\x"` all land inside `work/`; `""` raises), `test_night_of_and_window` (Sat 06:59 local -> window, same night as Sat 00:00; Sat 07:00 -> out; Fri 23:59 -> out), `test_log_and_spent`, `test_log_is_bounded` (301 entries -> 300), `test_stopped_reason`.
- [ ] **Step 2: Run** `python tests/test_work.py` - expect ImportError.
- [ ] **Step 3: Implement `work.py`.**
- [ ] **Step 4: Run** - `all pass`.
- [ ] **Step 5: Commit.**

### Task 3: The overnight runner and research sessions

**Files:**
- Create: `agents/overnight.py`
- Modify: `agents/evaluator.py` (`evaluate(task, response, label: str = "Evaluator")`, passed through to its model call)
- Test: `tests/test_overnight.py`

**Interfaces:**
- Consumes: Task 1 `goals.*`, Task 2 `work.*`, `research_loop._PLAN_SYSTEM`, `research_loop._DISTILL_SYSTEM`, `research._run_search(query, max_results=5)`, `research._format_results(results)`, `llm.complete`, `llm.is_daily_cap`, `evaluator.evaluate`.
- Produces:
  - `async overnight.tick(now: datetime | None = None, search=None, complete=None) -> str` - the scheduled entry point. Returns what it did (for logs and tests). `search` and `complete` are injectable for tests and default to the real ones.
  - `overnight.paper_is_finished(text: str) -> tuple[bool, str]` - the code check from Global Constraints.

`tick`: outside the window, already stopped tonight, or over either budget -> return without a model call. Else `goals.next_to_work()`; research -> `_research_session`, self_improve -> Task 4's `_self_improve_session`. Measure tokens as the `work.overnight_tokens()` delta and log the session. On `openai.RateLimitError`: log the session with `stopped="daily cap 20b: <limit>"` (parse `Limit N` from the body) when `llm.is_daily_cap`, else `stopped="rate limited"`; never raise.

`_research_session(goal)`: up to `min(3, searches left)` PLAN/SEARCH/DISTILL steps (prompts from the research loop, `llm.complete(... label="Overnight", reasoning_effort="low")`, PLAN 1024, DISTILL 2048), appending findings to `findings.md` with a `<!-- session N -->` marker. When the trigger is met, WRITE: one call (max_tokens 3072) producing a markdown paper with title, sections, inline source links and `## What I am unsure about`; save as `paper-<night>.md`; finished if `paper_is_finished`, else saved as `draft-<night>.md` with the reason in the log. The Evaluator runs on finished papers with `label="Overnight"`; its feedback is appended under `## Reviewer note`, never blocks. When a paper is finished, `findings.md` is moved to `findings-<night>.md` (the paper's source material, kept) and a fresh `findings.md` starts, so the working file never grows without bound. Update `next_action` to the planner's next thread.

- [ ] **Step 1: Write the failing tests** with stub `search`/`complete`: `test_outside_window_costs_nothing`, `test_research_appends_findings_and_moves_next_action`, `test_paper_written_after_trigger`, `test_paper_without_unsure_section_stays_draft`, `test_budget_stops_the_night` (log shows 60000 spent -> no call), `test_search_cap`, `test_daily_cap_429_stops_and_records_limit` (Review Focus 1), `test_restart_resumes` (Review Focus 3: a second `tick` continues `findings.md` and does not repeat the session marker number), `test_tick_never_raises` (stub raising `RuntimeError`), `test_findings_rotate_after_finished_paper`.
- [ ] **Step 2: Run** `python tests/test_overnight.py` - expect ImportError.
- [ ] **Step 3: Implement** `agents/overnight.py` and the evaluator label. Prompts include the style block.
- [ ] **Step 4: Run** the new tests and `tests/test_llm.py` - `all pass`.
- [ ] **Step 5: Commit.**

### Task 4: Self-improvement sessions, proposals and preferences

**Files:**
- Modify: `agents/overnight.py` (`_self_improve_session`)
- Create: `proposals.py`
- Modify: `agents/react.py` (`proposal` tool; preferences block in the system prompt), `agents/direct.py` (preferences block), `agents/jobs.py` (ranking reads preferences), `orchestrator.py` (`_CONTEXT_BUDGET_REACT` reduced by 1500 to pay for the block, per GAMEPLAN A.1)
- Test: `tests/test_proposals.py`

**Interfaces:**
- Produces:
  - `proposals.add(kind: str, problem: str, evidence: str, change: str, test: str, now=None) -> str` - kind in `("preference", "prompt", "code")`; numbered `p<n>` in `memory/work/self-improvement/proposals.md`.
  - `proposals.pending() -> list[dict]`
  - `proposals.decide(pid: str, approve: bool) -> str` - approving a `preference` appends its `change` line, dated, to `memory/preferences.md`; refused with a clear message if the file would pass 1500 chars. Approving `prompt`/`code` only marks it approved for a Claude Code session.
  - `proposals.preferences_block() -> str` - `""` or `"Standing preferences the user approved:\n- ..."`.
  - `proposals.run_tool(inputs) -> str` - actions `list, approve, reject` (state-changing: add to both quarantine sets).

`_self_improve_session(goal)`: inputs are `corrections.md` entries newer than `memory/work/self-improvement/seen.json`'s timestamp (at most 20) and up to 40 `ERROR` lines from `config.LOG_FILE` since the last session. One `llm.complete` (max_tokens 3072, label "Overnight") returns proposals as JSON lines `{"kind","problem","evidence","change","test"}`; unparseable lines are dropped and counted. Nothing is applied. No new input -> no model call.

- [ ] **Step 1: Write the failing tests** `test_no_new_input_no_call`, `test_proposals_written_not_applied`, `test_bad_json_lines_dropped`, `test_approve_preference_reaches_prompts` (after approve, `preferences_block()` contains it and `react._get_system_prompt()`-built text and Direct's prompt include it), `test_preferences_capped`, `test_prompt_proposal_never_edits_files`.
- [ ] **Step 2: Run** - expect failures.
- [ ] **Step 3: Implement.**
- [ ] **Step 4: Run** the new tests plus `tests/test_delegation.py`, `tests/test_quarantine.py`, `tests/test_resume_access.py` - `all pass`.
- [ ] **Step 5: Commit.**

### Task 5: Morning digest section, papers attached, scheduler

**Files:**
- Modify: `agents/monitor.py` (`setup(send_fn, send_file_fn=None)`; register `overnight.tick` hourly at minute 20; `overnight` digest section), `agents/prod_memory.py` (`_VALID_DIGEST_SECTIONS` adds `"overnight"`), `interfaces/discord_bot.py` (pass `send_file_fn=self.send_file_to_user`)
- Create: `overnight.digest_section(night: str) -> tuple[str, list[Path]]` in `agents/overnight.py`
- Test: `tests/test_overnight.py` (extend)

**Interfaces:**
- Consumes: `work.spent`, the log, `proposals.pending()`.
- Produces: `digest_section(night) -> (text, finished_papers)`. Code-built, no model call. Text lists: papers finished (title), drafts in progress, proposals waiting (count), tokens and searches spent, any `stopped` reason. Quiet night -> `("", [])`.

- [ ] **Step 1: Write the failing tests** `test_quiet_night_section_empty` (Review Focus 5), `test_section_lists_finished_and_waiting`, `test_digest_attaches_finished_papers` (stub `send_file_fn` receives each finished paper's text once).
- [ ] **Step 2: Run** - expect failures.
- [ ] **Step 3: Implement.** The digest only attaches files when `send_file_fn` was given; without it, it lists them.
- [ ] **Step 4: Run** the new tests plus `tests/test_monitor_status.py`, `tests/test_config_guard.py` - `all pass`.
- [ ] **Step 5: Commit.**

### Task 6: PC copy, docs, deploy, seed goals, one live session

**Files:**
- Modify: `scripts/backup_memory.ps1` (after a successful archive pull, extract `memory/work/` into `$env:USERPROFILE\Documents\IGOR Work`; a missing folder in the archive is not an error)
- Modify: `ARCHITECTURE.md`, `STATE.md`, `GAMEPLAN.md` (A.4 ticked when live)

- [ ] **Step 1:** Edit the backup script; run it with an archive lacking `memory/work/` and with one containing it; both exit 0 and the second populates the folder.
- [ ] **Step 2:** Document in ARCHITECTURE.md (tools count, the loop, the digest section), condensing to stay inside the size tests; update STATE.md.
- [ ] **Step 3:** Run every offline suite (`for f in tests/test_*.py`) - none failing.
- [ ] **Step 4:** Commit, push, deploy through the gate; expect `=== deploy OK`.
- [ ] **Step 5:** On the server as `igor`: add `overnight` to `memory/digest_config.md` Sections; seed the two goals with `goals.add`.
- [ ] **Step 6:** Run **one** session by hand inside a forced window (`overnight.tick(now=<local 01:00>)`), read its spend from the log and the files it wrote. Record the spend in STATE.md. Do not repeat it.
- [ ] **Step 7:** When a night first stops with `daily cap 20b: <limit>`, put that limit in `config.DAILY_TOKEN_CAPS` and STATE.md - it is the first measurement of 20b's cap.
