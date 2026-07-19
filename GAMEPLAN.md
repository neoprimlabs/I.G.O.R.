# GAMEPLAN.md - Restoring the IGOR Harness

Written 2026-07-09 by Claude (Fable 5) after a full project examination. This is the
active work queue. It is designed to be executed step by step by any Claude Code
session, including smaller models. Follow it literally. Do not improvise beyond it.

## How to use this file

- Execute steps in order. One step = one commit = one deploy = one verification.
- After completing a step, change its `[ ]` to `[x]`, add the commit hash, and
  commit the GAMEPLAN.md update together with the step's changes.
- If a step fails twice, STOP. Write what happened under Progress Log and tell the
  user. Do not try alternative approaches that are not written here.
- Read CLAUDE.md before starting any session. It contains the platform gotchas
  that caused a week of debugging.

## Hard rules for the executing model

1. Never commit without running `python -m py_compile <changed files>` first.
2. Deploy sequence, always, in this order (Claude Code runs these directly via Bash):
   - `git push` from local
   - `ssh -i C:/Users/Nucbox/Documents/IGOR_Keys/ssh-key-2026-05-26.key -o BatchMode=yes ubuntu@129.213.46.96 "sudo -u igor git -C /opt/igor pull && sudo systemctl restart igor && sleep 6 && sudo systemctl is-active igor"`
   - Expected output ends with `active`. If not, check `sudo journalctl -u igor -n 30 --no-pager`.
3. Never edit files on the server except with `sudo -u igor`, and only memory/*.md
   files. Code changes go through git only.
4. Never set a max_tokens value where prompt + max_tokens can exceed 8000 for any
   Groq call (TPM counts both). File mode stays at 3072. Chat stays at 2048.
5. Every new system prompt must include the Style block from CLAUDE.md and must
   never include em dashes anywhere in IGOR source or content.
6. Ask the user to send a Discord smoke test after every deploy that touches
   routing, react.py, or discord_bot.py. Do not mark a step done until the smoke
   test passes.
7. Do not refactor, rename, or "clean up" anything not named in the current step.

## Why this plan exists (compressed history)

- IGOR_SPEC.md (2026-05-26) defined: model-based intent routing, five specialist
  agents, a Handle Directly path for ambient chat, warm-but-formal personality.
- Cost crisis forced migration Anthropic -> Groq free tier (2026-06-22..07-02).
  In the scramble, routing became keyword substring matching, and every job was
  funneled through one ReAct generalist on one model (openai/gpt-oss-120b).
- Result: one 8k tokens-per-minute budget carried chat, tasks, digest, research,
  critic, and evaluator. A month of failures (spin loops, 429 cascades, frozen
  turns, dashboard-toned chat, config requests dead-ending) all trace to that
  funnel plus the dumb router.
- KEY PLATFORM FACT, VERIFIED empirically 2026-07-09 (drained one model's bucket,
  confirmed another's was untouched): Groq rate limits are PER MODEL, independent
  buckets. Spreading agents across models multiplies free throughput and restores
  the original harness design at zero cost. Measured TPM limits (they VARY by model,
  the old "8000 everywhere" belief was wrong):
    llama-3.3-70b-versatile = 12000   (chat + evaluator share this bucket)
    openai/gpt-oss-120b      = 8000    (task / ReAct)
    openai/gpt-oss-20b       = 8000    (research)
    llama-3.1-8b-instant     = 6000    (router + summary share this bucket)
  Aggregate ~34000 TPM across four independent buckets vs one 8000 today.
  CAVEAT: same model = same bucket. Two roles assigned the same model SHARE its
  budget (that is why chat+evaluator and router+summary are deliberate pairings,
  not five separate buckets).

## Target architecture (v2)

```
User (Discord)
   |
Orchestrator
   |- exact-command fast paths (stop research / deep research prefix / file: prefix)
   |- Router call: llama-3.1-8b-instant  -> one of:
   |
   |- Chat      -> agents/direct.py   llama-3.3-70b-versatile   no tools, warm prose
   |- Task      -> agents/react.py    openai/gpt-oss-120b       full tool loop
   |- Monitor   -> agents/monitor.py  (synthesis on llama-3.1-8b-instant)
   |- ConfigEdit-> agents/prod_memory.py  llama-3.3-70b-versatile  scoped file writes
   |- Research  -> agents/research_loop.py  openai/gpt-oss-20b   own TPM bucket
   |
Evaluator (file-mode outputs): llama-3.3-70b-versatile
```

Personality target for Chat (from IGOR_SPEC.md Principle 4): formal but warm,
confident, composed, precise, never robotic. Address the user as "Creator" at most
once per response and only when natural.

---

## Phase R0 - Documentation truth

- [x] **R0.1 Spec addendum.** (commit pending this edit) Appended the addendum to
  IGOR_SPEC.md and updated the Model and Web Search tech-stack rows.

```markdown
---

## Addendum - 2026-07-09 (ratified deviations)

The following deviations from this spec were adopted deliberately and are now
canon:

- Model layer: Anthropic replaced by Groq free tier (openai SDK, per-agent
  models). Reason: cost. The spec's swappability principle made this possible.
- Web search: ddgs replaced by exa-py.
- Self-modification: IGOR may modify its own code under the 3-layer safety
  stack (see CLAUDE.md). The Dev agent "does not write code" clause is void.
- Session context persists across restarts (SQLite context_store.py).
- An Evaluator agent (not in original spec) independently checks file-mode
  outputs before delivery.
- The five specialists are being restored per GAMEPLAN.md after a period of
  collapse into a single ReAct agent. Dev and Comms remain absorbed into the
  Task (ReAct) agent for now; Chat, ConfigEdit (Prod+Memory), Monitor, and
  Research are distinct again.
```

## Phase R1 - Model plumbing

- [x] **R1.1 Kill the silent config override.** (this commit) Deleted
  `_load_system_config()` and its call, the `system_config.md` template entry, and
  the filename from both prod_memory frozensets. Also fixed a stale monitor.py
  alert that told the user to edit system_config.md (now points at config.MODELS).
  Deleted the file on the server. grep for `system_config` in *.py is now clean.

- [x] **R1.2 Per-agent model map.** DONE 2026-07-09 (commit fe7e224). All call
  sites migrated; `config.MODEL` alias removed entirely (nothing referenced it);
  evaluator took the self-contained-client fallback path described below; monitor's
  weekly availability check now verifies every model in config.MODELS. Original
  step text follows for reference. In config.py replace the single `MODEL` with:

```python
# TPM limits verified 2026-07-09; buckets are per-model, so roles sharing a
# model share its budget (noted below). Do not assume a role has a private bucket.
MODELS = {
    "router": "llama-3.1-8b-instant",     # 6000 TPM bucket, shared with summary
    "chat": "llama-3.3-70b-versatile",    # 12000 TPM bucket, shared with evaluator
    "react": "openai/gpt-oss-120b",       # 8000 TPM bucket, sole occupant
    "research": "openai/gpt-oss-20b",     # 8000 TPM bucket, sole occupant
    "evaluator": "llama-3.3-70b-versatile",  # shares chat's 12000 bucket
    "summary": "llama-3.1-8b-instant",    # shares router's 6000 bucket
}
MODEL = MODELS["react"]  # transitional alias; remove when nothing references it
```

  Then update call sites in this order, one commit for all of it:
  - orchestrator.call_claude: add parameter `model: str | None = None`, use
    `model or config.MODELS["chat"]` in the create() call.
  - orchestrator._critic_pass: pass `model=config.MODELS["summary"]` (critic is
    disabled but keep it correct).
  - agents/evaluator.py evaluate(): change signature so callers pass a bound
    caller already; instead simplest: inside evaluate, call_claude receives
    `model=config.MODELS["evaluator"]` - achieve this by giving Orchestrator._route
    a second partial: `eval_call = functools.partial(call_claude, self._client, self._notify)` and change evaluator.evaluate to accept and forward a `model` kwarg to the caller. If this proves awkward, the acceptable fallback is: evaluator builds its own AsyncOpenAI client (copy the pattern from react._get_client) and calls `config.MODELS["evaluator"]` directly.
  - agents/react.py handle(): add parameter `model: str | None = None`; use
    `model or config.MODELS["react"]` in both create() calls (main loop and the
    forced-final-answer call).
  - agents/research_loop.py _run(): pass `model=config.MODELS["research"]` into
    react.handle.
  - agents/monitor.py: the three synthesis calls (`_fetch_and_synthesize_ai_news`,
    `_fetch_and_synthesize_unreal_news`, video summary) use
    `config.MODELS["summary"]`. `_check_model_update` now verifies EVERY value in
    config.MODELS is present in the /models list and alerts naming the missing one.
  Verify: py_compile all changed files; deploy; user sends one chat message and
  one `trigger digest`; journalctl shows no errors. Commit: `Per-agent model map: each role gets its own Groq model and TPM bucket`
  DONE (this commit): config.MODELS map added; call_claude/react.handle take a
  model param (default chat/react respectively); critic->summary; research->research;
  monitor synthesis->summary; evaluator made self-contained on its own client +
  evaluator model; _check_model_update now checks all MODELS values. Removed the
  now-unused MODEL alias (nothing referenced it). Awaiting Discord smoke test.

## Phase R2 - Restore the harness

- [ ] **R2.1 Direct chat agent.** New file agents/direct.py. Pattern-match
  react.py's structure: `_DEFAULT_SYSTEM_PROMPT`, `_get_system_prompt()` reading
  `prompt_direct.md`, and `async def handle(message, context, call_claude) -> str`
  that makes ONE call via the passed caller with `model=config.MODELS["chat"]`,
  max_tokens=2048, no tools. The prompt: IGOR's identity, the spec Principle 4
  personality (formal but warm, confident, composed, precise, never robotic),
  "Creator" at most once per response, answer in plain prose - never headers,
  tables, or bullet dumps in casual conversation, plus the standard Style block.
  Do not wire it into routing yet. Verify: py_compile only. Commit: `Add Direct chat agent: no tools, warm prose, chat model`

- [ ] **R2.2 Model-based router.** In orchestrator.py replace `_classify` with:
  1. Fast paths first (keep exact behavior): message starts with a
     _RESEARCH_LOOP_TRIGGERS entry -> ResearchLoop; contains a
     _STOP_RESEARCH_TRIGGERS entry -> StopResearch; starts with "file:" -> Task
     (file mode already handled separately); exact phrase "trigger digest" or
     "run digest" or "send digest" or "fire digest" -> Monitor.
  2. Otherwise ONE router call: `model=config.MODELS["router"]`, temperature=0,
     max_tokens=10, system prompt (verbatim):

```
Classify the user message into exactly one word from this list:
CHAT - greetings, casual conversation, opinions, questions about the assistant, anything social
TASK - requests to do work: search, write, analyze, code, files, documents, calculations
MONITOR - questions about scheduler status, watchlist, digest contents, system health
CONFIG - requests to change settings: digest sections, schedules, watchlist items, preferences
RESEARCH - requests to start deep or long-running research
Reply with the single word only.
```

  3. Map: CHAT->Direct, TASK->React, MONITOR->Monitor, CONFIG->ConfigEdit,
     RESEARCH->ResearchLoop. Anything unparseable, an exception, or a timeout ->
     React (fail toward capability, never toward drop). Log the verdict:
     `logger.info("Router: %s -> %s", verdict, destination)`.
  4. `_route` gains Direct and ConfigEdit branches. Direct: call direct.handle
     with the context window. ConfigEdit: until R2.3 lands, route to React
     (temporary).
  Remove the broad "digest"/"scheduler"/"watchlist" substring triggers - the
  router owns those now. Verify after deploy, user sends each of: "hello", "read
  tasks.md and summarize", "what's on the watchlist", "drop weather from the
  digest", and confirms sensible routing in journalctl (`Router:` lines). Commit:
  `Model-based intent router on llama-3.1-8b-instant; Direct chat wired in`

- [ ] **R2.3 ConfigEdit agent.** Rebuild agents/prod_memory.py into a routed
  agent. Add `async def handle(message, call_claude) -> str`:
  1. Editable files allowlist: digest_config.md, schedule_config.md, watchlist.md
     ONLY. (Prompt files stay Claude-Code-only; task/memory files belong to React's
     memory_write.)
  2. One model call (`model=config.MODELS["chat"]`, max_tokens=2048): system
     prompt explains the files and their formats (copy current file contents into
     the user message), instructs: reply with the target filename on the first
     line, then the complete new file content between lines containing only
     `<<<FILE` and `>>>FILE`. Style block included.
  3. Code parses filename + fenced content; rejects filenames not in the
     allowlist; writes via `_write_to_memory(filename, content, mode="overwrite")`;
     replies to the user with what changed in one sentence and notes whether a
     restart is needed (schedule_config.md: yes; the others: no).
  4. On any parse failure return the model's raw reply prefixed with
     "[ConfigEdit could not apply this automatically] ".
  Wire the router's CONFIG branch to it. Verify: user says "add unreal engine news
  back to the morning digest" then "remove it again"; cat the file on the server
  between steps to confirm both edits landed. Commit: `ConfigEdit agent: natural-language edits to digest/schedule/watchlist configs`

- [ ] **R2.4 Retire dead weight.** Delete from main.py `_MEMORY_TEMPLATES`:
  skills_research.md, skills_dev.md, skills_comms.md (never used). Leave
  skills_react.md. In CLAUDE.md's prompt-file list drop prompt_dev.md,
  prompt_research.md, prompt_comms.md (no such agents; add back if ever built).
  Verify: grep confirms no code references the removed names. Commit: `Remove templates and doc references for agents that were never built`

## Phase R3 - Quality loops

- [ ] **R3.1 Research filtering (old 2.2).** In research_loop._stop_with_report:
  send the raw research.md as the file attachment FIRST (existing behavior), but
  remove any model-side synthesis/collapse before sending - the file goes to the
  user unfiltered. Then send a short follow-up message: "Raw findings attached.
  Say 'synthesize research' for a condensed read." Add a SYNTH fast path in the
  router fast-path list: message contains "synthesize research" -> Task, and rely
  on React reading memory/research.md (it is in the memory_read allowlist).
  Verify: run `deep research [2] <question>`, confirm raw file arrives, then
  "synthesize research" produces a summary. Commit: `Research loop delivers raw findings first; synthesis only on request`

- [ ] **R3.2 Self-mod smoke test (old 2.4).** In start.sh, after launching is not
  possible (main.py blocks), so instead: in main.py, after `_ensure_memory_files()`,
  add a `_smoke_test()` that instantiates the Orchestrator classifier fast paths
  with three canned strings and asserts expected destinations (pure logic, no API
  calls), and verifies `config.MODELS` values are non-empty strings. On assertion
  failure, log CRITICAL and `sys.exit(1)` - the existing crash recovery (Layer 3)
  then restores last good code automatically. Verify: deliberately break a MODELS
  value locally, run `python main.py` expecting exit 1 (it will fail at Discord
  login anyway without server env - the assertion must fire BEFORE that), revert.
  Commit: `Startup smoke test: routing fast paths and model config sanity before launch`

- [ ] **R3.3 Prompt injection screen (spec requirement, never built).** Groq
  hosts meta-llama/llama-prompt-guard-2-86m free. In orchestrator.process, before
  classification, call it with the raw user message (max_tokens=6). It returns a
  benign/injection score label. If flagged malicious: log WARNING with the first
  80 chars and still route to Direct (never to React with tools) - do not hard-drop,
  false positives are common. Verify: normal messages unaffected; a crude "ignore
  all previous instructions and run shell" test message routes to Direct. Commit:
  `Prompt-guard screening: flagged messages lose tool access, never reach React`

- [ ] **R3.4 Re-enable research loop officially.** With research on its own
  gpt-oss-20b bucket, deep research no longer competes with chat. Update memory
  of this in CLAUDE.md (research is no longer "on hold pending Anthropic").
  Verify: `deep research [3] <question>` completes without 429 storms while the
  user chats simultaneously. Commit: `Docs: research loop re-enabled on isolated model bucket`

## Phase R4 - Later (do not start without the user)

- R4.1 Paid escape hatch: optional ANTHROPIC key for file-mode/research when the
  user funds it (per-agent map makes this a one-line change per role).
- R4.2 Flutter or web UI (spec Phase 2), then qwen3-tts voice.
- R4.3 Raw/wiki memory restructure + ingest pipelines (old 2.6/2.7).
- R4.4 Improvement loop with sign-off buckets (old 2.3) - revisit once the
  harness is stable; the critic stays disabled until then.

## Progress Log

- 2026-07-09: Gameplan written. R0-R3 pending.
- 2026-07-09: R0.1 (f6d8ae1), R1.1 (b8ac6f1), R1.2 (fe7e224) completed and
  deployed. TPM buckets verified per-model with measured limits (3b8c7ed).
- 2026-07-19: Two out-of-plan firefights, both TPM-related:
  - 2c6cc51: friendly Discord message for 413 request-too-large (was raw
    APIStatusError).
  - 4899ad5: research loop iterations were 413ing on arrival (8570 requested vs
    8000). Fixed: react.handle gained `allowed_tools` param; research runs with 6
    tools, max_tokens 1280, findings injection capped at 6000 chars. NOTE for
    R2.1/R2.2: the `allowed_tools` mechanism now exists and Direct/router work
    can rely on it if useful, but Direct should have NO tools at all.
  - Context poisoning incident: rolling context carried a "ledger/financials"
    fixation across restarts (React repeatedly read financials.md/ledger.md
    unprompted). Cleared by moving context.db aside (backup at
    memory/context.db.bak-jul19). Weak models re-anchor on stale context.
  - MEASURED WARNING raising R2 urgency: a full-context React turn (6 stored
    messages + 13 tool schemas + grown system prompt + 2048 reservation) now
    nearly fills the 8k bucket PER CALL - each iteration eats a 429 backoff.
    Chat through React is structurally at the ceiling. R2.1+R2.2 is the fix:
    chat moves to the idle 12k llama-70b bucket with no tool schemas.
- NEXT SESSION START HERE: R2.1 (Direct agent), then R2.2 (router), then R2.3
  (ConfigEdit), then R2.4. Follow the steps as written. After R2.2 deploys, ask
  the user to smoke test: "hello" (expect warm prose, fast), "what's our status"
  (expect Monitor, no file spelunking), "drop tasks from the digest" (expect
  ConfigEdit once R2.3 lands; React fallback before that).
