# ARCHITECTURE.md - What IGOR Actually Is

**IGOR: this section answers questions about yourself.** It fits one `read_file`
window on purpose; the rest is reachable with `offset`. Describe nothing unread.

## The whole system, in one page

A Discord bot, DMs only, one authorized user. Python 3.10 on an Oracle
`VM.Standard.E2.1.Micro` (x86_64, Ubuntu 22.04, 1 OCPU / 956 MB RAM plus 2 GB swap),
root `/opt/igor`, systemd services `igor` and `igor-watchdog`. Models are Groq free
tier through the `openai` SDK. Search is Exa. Persistence is markdown files plus
SQLite. No database server, no web UI, no admin panel.

**Routing.** Five exact-match or regex fast paths, then one router call
(`llama-3.1-8b-instant`, `max_tokens=10`) returning one word. Six destinations. Any
router failure falls through to React.

| Destination | Handles | Model | Tools |
|---|---|---|---|
| Direct | `CHAT` | llama-3.3-70b | none, by design |
| React | `TASK`, router failure | gpt-oss-120b | all 12 |
| Monitor | `MONITOR`, digest commands | llama-3.1-8b | none |
| ConfigEdit | `CONFIG` | llama-3.3-70b | none, writes 3 files |
| ResearchLoop | `deep research` prefix | gpt-oss-20b | none, fixed pipeline |
| SelfDescribe | questions about IGOR itself | llama-3.3-70b | none, reads this file |

**Questions about IGOR go to SelfDescribe, not React**, which carries this whole
document and no tools so it has room to be accurate. It returns `NOT_ABOUT_IGOR` for
messages that are really tasks, and those go on to React.

**React's 12 tools, the complete list:** `search`, `memory_read`, `search_memory`,
`python_run`, `read_file`, `patch_file`, `write_file`, `restart_self`, `shell`,
`fetch_url`, `send_message`, `memory_write`. There are no others.

**Scheduling.** APScheduler, in-process. Three jobs, all registered *in code* in
`monitor.setup()`: the morning digest (13:00 UTC), a Groq model-availability check
(Mondays 09:00), and an advocacy draft (Mondays 15:00). There is no scheduler config
file, and no way to add or retime a job without a code change and a deploy.

**Config and memory** are markdown files in `/opt/igor/memory/`: `digest_config.md`
(which digest sections run), `tasks.md`, `projects.md`, `user.md`, `agents.md`,
`watchlist.md`, `research.md`, `corrections.md`, `drafts.md`, plus `context.db`
(SQLite conversation history). `memory_write` takes a filename from a fixed list
plus content - it is not a key/value store. `corrections.md` and `drafts.md` are
readable by no agent: both hold text derived from untrusted input, so pulling them
into a tool-bearing context would be a stored injection path.

**Deployment** runs through a root-owned script at
`/usr/local/lib/igor-deploy/deploy.sh`, outside `/opt/igor` and beyond IGOR's reach.
It compile-checks, imports every module, restarts, waits for the gateway, and
reverts on failure.

## What does NOT exist

Say so plainly rather than describing these as though they work:

- **No content filter, moderation pipeline, or safety classifier.** Nothing screens
  generated text for accuracy or harm.
- **No `scheduler.yaml`, no `run_agent()`, no `igor` module, no admin UI.**
- **No connection to any external platform.** No posting, no email, no social or
  publishing accounts. IGOR drafts; a human publishes.
- **No working self-modification.** `restart_self` writes a sentinel nothing acts
  on. IGOR cannot deploy its own code changes.
- **No sandbox.** `shell` and `python_run` run as the `igor` user on the live host.

---

Describes the system **as it exists today**, verified against the source on
2026-08-13. Nothing aspirational appears here.

- `IGOR_SPEC.md` is the vision. Some of it has never been built.
- `GAMEPLAN.md` is the plan. All of it is unbuilt by definition.
- **This file is reality.** If it disagrees with the code, the code wins and this
  file is wrong - fix it in the same commit.

> That last rule was broken once already. Between 2026-08-02 and 2026-08-03 this
> file went 24 commits without an update while R2.0 through R3.3 changed most of
> what it describes. IGOR reads this file to answer questions about itself, so it
> confidently told the user it used keyword routing days after the model router
> shipped. **Updating this file is part of the change, not a follow-up to it.**
>
> On 2026-08-13 it failed the other way: the file was correct but 23KB, and
> `read_file` capped at 4000 chars with no way to page, so IGOR received 17% of it
> and invented the rest - including a content filter it has never had. Hence the
> summary above. **Keep it under 3400 characters or IGOR stops seeing the end of
> it.**

---

## Stack and host

Self-hosted personal AI assistant. A Discord bot, DMs only, single authorized
user.

- **Language:** Python 3.10
- **Interface:** discord.py
- **Models:** Groq free tier through the `openai` SDK, per-agent models
- **Scheduling:** APScheduler
- **Search:** exa-py
- **Persistence:** markdown files plus SQLite, no database server
- **Host:** Oracle Cloud `VM.Standard.E2.1.Micro`, **x86_64**, Ubuntu 22.04,
  1 OCPU / 956 MB RAM plus a 2 GB swapfile, `/opt/igor`, systemd

The host was an ARM A1 with 4 OCPU / 24 GB until 2026-07, when Oracle cut the
Always Free A1 allowance and terminated the instance for exceeding the new
limit. Anything describing IGOR as ARM is out of date.

Formerly Anthropic-powered; migrated 2026-06-22 for cost. `anthropic` is still
installed and imported by nothing - it is the deliberate hook for GAMEPLAN R4.1.

**The runtime is the server.** There is no local run; discord.py is not installed
locally. Test pure logic with standalone Python snippets instead.

---

## Message lifecycle

A Discord DM travels this path every time:

1. **`interfaces/discord_bot.py` `on_message`** - ignores its own messages and
   anything that is not a DM, caches the channel for proactive sends.
2. **`orchestrator.process`** - security gate. `user_id != AUTHORIZED_USER_ID`
   returns `None`: silent drop, no reply, no acknowledgment. This is the only
   auth check in the system; everything downstream trusts it.
3. **File-mode sniff** - a `file:` prefix sets `file_mode` and is stripped. A
   `file:` request skips the router entirely and goes to React, because it is
   document work regardless of what it asks for.
4. **`orchestrator._classify`** - fast paths first, no model call:

   ```
   contains    _SYNTH_TRIGGERS          -> SynthesizeResearch
   startswith  _RESEARCH_LOOP_TRIGGERS  -> ResearchLoop
   contains    _STOP_RESEARCH_TRIGGERS  -> StopResearch
   exact match _DIGEST_COMMANDS         -> Monitor
   ```

   Anything else goes to **one router call**: `MODELS["router"]`
   (`llama-3.1-8b-instant`), `temperature=0`, `max_tokens=10`, 15s timeout, on a
   mostly idle bucket. It returns one word, mapped by `_VERDICT_MAP`:

   | Verdict | Destination |
   |---|---|
   | `CHAT` | Direct |
   | `TASK` | React |
   | `MONITOR` | Monitor |
   | `CONFIG` | ConfigEdit |
   | `RESEARCH` | ResearchLoop |

   Any failure - unparseable output, exception, timeout - falls through to React.
   Fail toward capability, never toward dropping the message. The verdict is
   logged as `Router: <verdict> -> <destination>`.

   The router is called directly rather than through `call_claude`, because
   `call_claude`'s rate-limit path notifies the user and sleeps 30s or more, which
   is wrong for classification. Failing fast to React beats making the user wait
   to be routed.

5. **`orchestrator._route`** - builds a bound `call_claude`, sets max_tokens
   (2048 chat, 3072 file mode), dispatches to the destination.
6. **Evaluator** - file mode only. One PASS/FAIL contract check, one retry with
   feedback, then delivers with an `[Evaluator warning: ...]` prefix. Fails open.
7. **`orchestrator.process` tail** - critic (disabled), context update (both
   sides truncated to 1500 chars, written to the in-memory list and SQLite),
   `[Monitor]` suffix when applicable.
8. **`discord_bot`** - `_sanitize` converts em dashes to spaced hyphens and maps
   remaining typographic punctuation to ASCII, then chunks to Discord's 2000-char
   limit, hard-splitting over-long single lines.

---

## What actually runs

| Component | Reachable via | Model role | Tools |
|---|---|---|---|
| Orchestrator | every message | `router` | none |
| **Direct** (`agents/direct.py`) | `CHAT` | `chat` | **none, by design** |
| **React** (`agents/react.py`) | `TASK`, `file:`, router failure | `react` | all 12 |
| **Monitor** (`agents/monitor.py`) | `MONITOR`, digest commands, APScheduler | `summary` | none |
| **ConfigEdit** (`agents/prod_memory.py`) | `CONFIG` | `chat` | none, writes 3 files |
| **ResearchLoop** (`agents/research_loop.py`) | `deep research` prefix | `research` | none, fixed pipeline |
| Evaluator (`agents/evaluator.py`) | file mode only | `evaluator` | none |

`agents/research.py` is an Exa search wrapper (`_run_search`, `_format_results`),
called by React and Monitor. It is not routable and not an agent.

### llm.complete - the shared text-completion wrapper

`llm.py` holds the only copy of "how a Groq text completion fails". Two failures
arrive as a 200 OK and are invisible to the caller: empty content, and non-empty
content with `finish_reason` `length`, which reads as a complete answer but was cut
mid-sentence. Both retry once at double budget (cap 4096), then return whatever came
back. A caller already at the cap makes one request, not two.

Routed through it: `orchestrator.call_claude` (so Direct, ConfigEdit and the critic
are covered), Monitor's three synthesis calls, Evaluator, and ResearchLoop's `_call`.

It also carries `reasoning_effort`, sent only when a caller sets it. gpt-oss-20b and
gpt-oss-120b accept low/medium/high; the llama models do not take the parameter at
all. Groq's docs say gpt-oss defaults to high, but measured, unset behaves nothing
like high - treat the documented default as unconfirmed. ResearchLoop sets `low`,
measured. React does not set it.

Two call sites stay out, on purpose:

- **React's main loop** - its `finish_reason` handling is entangled with tool
  dispatch and `tool_use_failed` retries, and is already correct.
- **React's forced final answer** and **the router** - both already do the right
  thing, and a doubled retry would be actively harmful. React trims that prompt to
  fit `max_tokens`, so doubling could push it past the bucket and lose the turn; the
  router reserves 10 tokens, reads one word, and fails fast to React by design.

Exceptions propagate. Callers handle rate limits differently on purpose: the
orchestrator notifies and sleeps, the evaluator fails open, Monitor drops the digest
section, the research loop stops and reports what it has.

This module exists because the knowledge lived as prose in CLAUDE.md and every new
call site re-derived it. Six of eight had no handling at all. Tested by
`tests/test_llm.py`.

### Direct

One model call, no tool schemas at all, on `llama-3.3-70b-versatile`. It uses the
`call_claude` passed to it, so chat gets rate-limit backoff and user notification
that React does not have.

Its prompt forbids claiming anything about IGOR's own state, features or
performance, because it cannot check. Asked something of that kind it must give a
two-part answer: the limitation, then the specific check that would answer it.

### React

System prompt is rebuilt on every call from the current UTC datetime plus
`prompt_react.md` if present, otherwise the built-in default. Messages become
`[system] + context window + user message`. All 12 tools attach unless
`allowed_tools` narrows them.

**The 12 tools:** `search`, `memory_read`, `search_memory`, `python_run`,
`read_file`, `patch_file`, `write_file`, `restart_self`, `shell`, `fetch_url`,
`send_message`, `memory_write`.

Up to 8 iterations:

| `finish_reason` | Behavior |
|---|---|
| `stop` | return content, done |
| `tool_calls` | dedupe against `seen_calls`, run in parallel, cap each result at 4000 chars, append, loop |
| `length` | return partial content, or retry once with doubled budget (max 4096) |
| `BadRequestError` + `tool_use_failed` | feed the rejection text back as a user note, retry up to 3 times |

On iteration exhaustion, one final tool-free call forces a real answer.

**Budget guard.** Before every request, `_trim_to_budget` estimates prompt plus
`max_tokens` and, above 7000, blanks the oldest tool results (protecting the
newest two) until it fits. Only tool results are touched. If it still does not
fit, the loop breaks to the forced-final-answer path, which trims with
`keep_recent=0`. Without this, long tool sessions crossed the model's TPM limit
around iteration 7 and 413'd, losing the whole turn.

**Search behaviour.** Queries containing both a recency word and an absolute date
have the date stripped, because the model writes its training-era year into
queries despite the real date being in its prompt. A recency query with no
`recency_days` gets a 180-day window automatically. Results carry `Published`
dates.

### ConfigEdit

Rebuilt from the old ProdMem write helper. Edits exactly three files:
`digest_config.md`, `schedule_config.md`, `watchlist.md`. Rejects any other
filename, validates digest section names against the set the digest actually
reads, keeps one rolling `.bak` per file, and reports whether a restart is needed.

**If its output matches the current file, it writes nothing and says so.** The
router is an 8B model and questions have classified as `CONFIG`; a write agent
reachable by a question needs a guard that does not depend on classification being
right.

`prod_memory._write_to_memory` is still used separately by React's `memory_write`
tool, with a wider allowlist.

### ResearchLoop

**Not a ReAct loop.** Each iteration is a fixed pipeline of isolated calls:

1. **PLAN** - sees the question and prior findings, returns one search query
2. **SEARCH** - no model, `research._run_search`
3. **DISTILL** - sees only this one search's raw results, returns 3-5 sourced
   findings plus a `Next:` thread. The raw results are discarded here and never
   enter another context
4. **APPEND** - code, not a model, writes to `research.md`

Every model call goes through `_call`, a thin wrapper over `llm.complete`. Both
silent failures of a reasoning model on a tight budget are handled there. The
truncation check was missing until 2026-08-10 and two findings in a 20-iteration run
were written to `research.md` cut mid-sentence, because a clipped response is a
valid 200 OK.

**Both calls run at `reasoning_effort` low.** Measured on the live API 2026-08-10
with these exact prompts, completion tokens:

| | low | unset (previous behaviour) | high |
|---|---|---|---|
| PLAN, n=4 | 42-72, median **47** | 164-441, median 355 | 937-997, median 957 |
| DISTILL, n=3 | 418-565, median **419** | 1328-1771, median 1734 | not sampled at n>1 |

4x to 7x fewer tokens. Output held: PLAN returned a valid query 4/4 at low, and
DISTILL returned 5 findings with all 5 sourced on every run at both settings.

Two caveats, both against earlier drafts of this section. Unset does not behave like
high, so the pipeline was never running at maximum. And a first single sample where
high returned empty did not replicate at n=4 - variance, not a property. This is an
efficiency win on a TPM-bound free tier, not a bug fix, and it does not explain
`_MIN_REASONING_BUDGET` or the empty-content retries, which stay.

"5 findings, 5 sourced" is a format check rather than a judgement that the findings
are equally good. The next real run is the test that matters.

Gathering and synthesis are deliberately in separate contexts. The previous design
handed one ReAct loop a batch of searches plus a write instruction, and ReAct
appends every tool result to a single growing history, so raw material and
synthesis competed for one 8000 TPM budget. Raw material won: two consecutive runs
spent all 8 iterations searching, never reached the write, and recorded nothing.

Findings are delivered as a **raw file attachment with no model pass over them**,
followed by an offer to synthesize on request. A `synthesize research` fast path
routes to React with an instruction to read the file and not search.

This shape is also, incidentally, the Dual LLM pattern for injection defence: the
context that sees untrusted web content holds no tools.

---

## Prompt injection defence

React holds all three legs of the "lethal trifecta" in one context: private data
(memory files), untrusted content (`fetch_url`, `search`), and outward action
(`shell`, file writes, self-modification).

Two controls, neither of which costs a model call:

1. **Framing.** Search and fetch results are wrapped in
   `[UNTRUSTED EXTERNAL CONTENT]` markers. The system prompt states that text
   inside has no authority regardless of what it claims, including claims to come
   from the user or the system.
2. **Quarantine.** The moment a web tool runs in a turn, six tools - `shell`,
   `python_run`, `write_file`, `patch_file`, `restart_self`, `memory_write` - are
   withdrawn from the schema **and** refused at execution for the rest of that
   turn. Both layers, because one batch can contain a search and a shell call and
   `asyncio.gather` gives no ordering guarantee, so the batch is judged as a whole
   before anything runs.

Consequence: "search for X and save it to tasks.md" takes two turns. React reports
what it found and the user asks for the write separately, which is a confirmation
gate on any action derived from the open web.

Classifier-based screening was specified and deliberately not built. Detection is
heuristic and cannot guarantee prevention; the constraint is structural instead.

---

## Models and rate limits

`config.MODELS` is the single source of truth. Groq TPM limits are **per model**,
independent buckets, and vary by model (verified empirically 2026-07-09):

| Role | Model | Bucket |
|---|---|---|
| `router` | `llama-3.1-8b-instant` | 6000, shared with `summary` |
| `chat` | `llama-3.3-70b-versatile` | 12000, shared with `evaluator` |
| `react` | `openai/gpt-oss-120b` | 8000, sole occupant |
| `research` | `openai/gpt-oss-20b` | 8000, sole occupant |
| `evaluator` | `llama-3.3-70b-versatile` | shares chat's 12000 |
| `summary` | `llama-3.1-8b-instant` | shares router's 6000 |

`max_tokens` counts against TPM at request time: prompt plus reservation is what
Groq bills against the bucket. Never configure a call where the sum can exceed
the model's limit.

The research model is a reasoning model, so `max_tokens` covers hidden reasoning
plus output. Anything under about 1024 returns empty content with a 200 OK rather
than an error.

**The `openai` package is a client library, not a provider.** Every client is
built with `base_url="https://api.groq.com/openai/v1"` and `GROQ_API_KEY`.
Nothing talks to OpenAI. The SDK choice follows from Groq implementing an
OpenAI-compatible wire format.

---

## Memory and config files

All live in `memory/` on the server, Syncthing-managed, and are **gitignored** -
they exist only on the host unless deliberately backed up.

**Read at startup, restart required:** `schedule_config.md`

**Read per call, immediate effect:** `digest_config.md`, `watchlist.md`,
`prompt_*.md`

**Data:** `context.db` (SQLite rolling context, `CONTEXT_WINDOW = 6`, 200 rows
retained on disk), plus `tasks.md`, `projects.md`, `user.md`, `agents.md`,
`research*.md`.

**Memory holds preferences, history and user facts. It does not hold
architecture.** `agents.md` and `projects.md` both accumulated architecture
descriptions that nobody maintained, drifted months out of date, and were reported
to the user as current fact. Anything verifiable against source belongs in this
file and is read with `read_file`.

`skills_react.md` and the critic that wrote it are both gone. Nothing modifies
React's prompt at runtime except `prompt_react.md`, which does not currently
exist.

### Agent prompt override pattern

Every agent uses `_DEFAULT_SYSTEM_PROMPT` with a file-based override:

```python
def _get_system_prompt() -> str:
    path = config.MEMORY_DIR / "prompt_<agent>.md"
    if path.exists():
        content = path.read_text(encoding="utf-8").strip()
        if content:
            return content
    return _DEFAULT_SYSTEM_PROMPT
```

Overrides take effect immediately, no restart. Reset by deleting or emptying the
file. Prompt files are edited through Claude Code sessions only, never through
Discord: ProdMem's legacy `%%WRITE%%` regex truncates at the first `%%END%%`.

**No `prompt_*.md` files exist on the server.** Every agent runs its built-in
default, so the prompt in the source is the prompt in play.

---

## Startup checks

`main._smoke_test()` runs after `_ensure_memory_files()` and exits 1 rather than
raising, so `start.sh` crash recovery restores the last good commit. It verifies:

- all six `config.MODELS` roles present and non-empty
- `DISCORD_BOT_TOKEN`, `GROQ_API_KEY`, `AUTHORIZED_USER_ID` set, `CONTEXT_WINDOW`
  at least 1
- four routing fast paths resolve correctly (all on fast paths, so no API call)
- **every agent module imports.** `py_compile` checks syntax, not names, and
  agents are imported lazily, so a missing stdlib import otherwise passes the
  commit and fails on the first message that routes there

An unset `AUTHORIZED_USER_ID` is the failure this exists for: the bot would
connect, report healthy, and silently drop every message.

---

## Safety stack

**Read this section before trusting any of it. Most of the documented stack does
not execute.** Verified on the running server 2026-08-03.

| Layer | Documented | Reality |
|---|---|---|
| 1. `start.sh` compile check and revert | runs before launch | **never runs.** `igor.service` is `ExecStart=/opt/igor/venv/bin/python main.py`, so start.sh is bypassed entirely |
| 2. `watchdog.py` restart on sentinel | restarts within 5s | **service is active but powerless.** It shells `sudo systemctl restart igor` as user `igor`, and `igor` has no sudoers entry. Zero restarts in the watchdog's entire journal |
| 3. `start.sh` crash recovery | reverts to last good commit | **never runs**, same cause as Layer 1 |

The files exist and contain correct logic. Nothing invokes them. This was
documented as working because the files were checked for content rather than for
being executed - a distinction worth making explicitly when verifying anything
here.

**What actually protects the system today**, all of it added 2026-08-02/03:

- `main._smoke_test()` - config sanity plus importing every agent module, exits 1
  rather than raising so a broken commit refuses to start
- systemd `Restart=always` - a broken deploy becomes a visible crash loop instead
  of quiet breakage
- `scripts/backup_memory.ps1` - compares systemd's restart count against the
  previous run and alerts on a crash loop, within a day

So a bad change will not run and will be noticed.

### Health-gated deploy (S.1, built 2026-08-10)

**This is the layer that actually executes**, and it is the one that reverts.
`/usr/local/lib/igor-deploy/deploy.sh`, root-owned and deliberately outside
`/opt/igor` so `write_file` cannot reach it. Logs every attempt to
`/var/log/igor-deploy.log`, which the `igor` user also cannot write.

Three gates in order. The first two run before any restart, so a change that fails
them never takes IGOR down:

| Gate | Catches | On failure |
|---|---|---|
| syntax | compiles every `.py` in memory | reset to previous commit, no restart |
| imports | the NameError class `py_compile` cannot see | reset to previous commit, no restart |
| **gateway** | `I.G.O.R. online` in the journal within 90s of restart | roll back, restart, alert |

The third gate is the point. `systemctl is-active` reports a process, and a process
that starts and never reaches Discord is not a working assistant - the gap in
"Known broken" item 3. The probe is anchored to a timestamp taken before the
restart, or the previous run's `online` line satisfies it and every deploy passes.

Verified on 2026-08-10 by deploying a commit that imports cleanly and hangs before
`bot.start`. Both gates passed correctly, the gateway missed its window, the deploy
rolled back and IGOR was healthy again 7 seconds later. The webhook alert failed on
the first run - the URL file, copied from Windows, carried a UTF-8 BOM and a CR that
survive `$(cat)` - so the script now strips both and `--test-alert` exercises that
path without breaking anything.

**Still not covered:** a crash that happens later, after a healthy deploy. That is
`Restart=always` plus the backup script's crash-loop alert, same as before.

**Consequence for self-modification:** React can write code and can write the
restart sentinel, but the restart never happens. Changes sit on disk unloaded
until someone restarts the service by hand. `restart_self` has never been called
in 30 days, so this has been true without being noticed.

None of these operate below the host either. When Oracle terminated the instance
in July 2026, every layer was irrelevant. `scripts/backup_memory.ps1` covers that
case from outside: a daily off-host backup of `memory/` and `.env` plus a service
health check, alerting to a Discord webhook.

---

## Dead weight

- `config.ANTHROPIC_API_KEY` is read and unused. Deliberate - the standing hook
  for GAMEPLAN R4.1.
- **Critic is off** (`ENABLE_CRITIC = False`), so `_critic_pass` and
  `_CRITIC_PROMPT` never execute. `_write_skill` and `skills_react.md` are gone
  entirely. IGOR currently cannot learn anything; GAMEPLAN V.1 addresses that.

---

## Known sharp edges

1. **Monitoring detects a dead process, not a dead gateway.** If IGOR is running
   and systemd reports active but the Discord connection has silently dropped, the
   health check reports healthy while the green dot is out. GAMEPLAN C.5.
2. **The router is an 8B model and misroutes at the margins.** 19 of 20 test cases
   pass; "what tools do you have access to" lands on Direct rather than React.
   Structural guards exist where a misroute would be harmful (ConfigEdit's no-op
   check), and the rest degrade into a worse answer rather than a wrong action.
3. **Still on Oracle Always Free.** An entitlement change can terminate the
   instance again, as it did in July 2026.

---

## Deliberately not built

Described in `IGOR_SPEC.md` or `GAMEPLAN.md`, absent from the code:

- **Dev and Comms as separate specialists.** The spec describes five; there are
  five routed destinations but Dev and Comms remain absorbed into React.
- **The improvement loop.** The critic is disabled and nothing writes learned
  skills, so IGOR cannot improve itself. GAMEPLAN V.1.
- **Any UI beyond Discord.** No Flutter or web interface. Spec Phase 2.
- **Voice.** qwen3-tts is planned, not started.
- **Multi-user anything.** `AUTHORIZED_USER_ID` is a single int, memory files are
  global, `context.db` has no user column. Single-user is assumed throughout.
