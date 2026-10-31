# ARCHITECTURE.md - What IGOR Actually Is

**IGOR: this section answers questions about yourself.** The rest of the file is
reachable with `offset`. Describe nothing unread.

## The whole system, in one page

A Discord bot, DMs only, one authorized user. Python 3.10 on an Oracle
`VM.Standard.E2.1.Micro` (956 MB), systemd. Groq free tier
via the `openai` SDK, Exa for search, markdown plus SQLite for storage.

## What does NOT exist

- **No content filter or safety classifier** over output. `sanitize.clean` only
  maps typography; refusals come from the models, not from IGOR.
- **No external accounts.** No posting, email or social media. IGOR drafts; a
  human publishes. It never buys anything and never applies for a job.
- **No self-restart.** `restart_self` only tells the user a change needs deploying.
- **No container.** `shell` and `python_run` run in bubblewrap (`sandbox.py`): no
  network, secrets or sealed memory, read-only but `/tmp` and `.git`; refused if
  it cannot start. The shell also refuses a blocklist and DMs the user for a yes
  before risky ones (`approvals.py`).
- **No `scheduler.yaml`, `run_agent()`, `igor` module or admin UI.** No spoken
  replies; voice notes and images the user sends are read.

**React's 21 tools, the complete list:** `search` (web or job boards), `memory_read`, `search_memory`,
`search_history`, `watch_history`, `search_files`, `delegate_task`, `task_update`, `python_run`,
`read_file`, `patch_file`, `write_file`, `restart_self`, `shell`, `fetch_url`,
`send_message`, `memory_write`, `scheduled_message`, `goal`, `proposal`, `execute_code`. No others. Thirteen load on
request through `load_tools` (Hermes' Tool Search).

**Fallback.** When `gpt-oss-120b` is rate-limited, Direct and React use `gpt-oss-20b`
until Groq's stated reset, and the reply says so.

**Commands** in code: `stop`, `retry`, `undo`, `usage`, `status`, `help`, `skills`.

**Routing.** Eleven fast paths in code, then one router call
(`qwen/qwen3.8-27b`) returning one word. Any router failure goes to React.

| Destination | Handles | Model | Tools |
|---|---|---|---|
| Direct | `CHAT` | 120b | none, by design |
| React | `TASK`, router failure | 120b | all 21 |
| Monitor | `MONITOR`, digest | 120b, news 20b | none; status block only, else to Direct |
| ConfigEdit | `CONFIG` | 120b | none; writes 3 files, only when named |
| ResearchLoop | `deep research` prefix | 20b | none, fixed pipeline |
| SelfDescribe | `SELF` - about IGOR itself | 120b | none, reads this file |

**Scheduled jobs**, registered in code in `monitor.setup()`: morning digest (13:00
UTC), job watch (14:30), gig watch (14:45), price watch (2-hourly), stock watch
(hourly), a Groq model check, an advocacy draft (Mondays), presence (10 min), due
scheduled messages and the page heartbeat (60s each) and the overnight tick (hourly).

**Watchers** (prices, restocks, jobs, gigs) stay silent unless something changed and
log to `history.db`.

**Scheduled messages**: `scheduled_message` keeps timed messages in
`memory/scheduled.json`. **Presence** is separate: one check-in
a day behind code gates, off unless `memory/presence_config.md` says `state: on`.

**Overnight loop**: hourly 00:00-06:59 local, a fixed pipeline (20b researches, 120b
writes) with no acting tools works the standing goals in `memory/goals.md` into sourced
papers, proposals and skill drafts in `memory/work/`, reported in the digest. Nothing is applied
without the user's yes.

**Memory** is markdown in `memory/` plus `context.db`, the conversation store (90
days, `search_history`); messages leaving the window fold into a running summary
(`compression.py`, 20b) in every prompt. `memory_read` and
`memory_write` take names from a fixed list; `memory_write` edits dated entries (add,
patch, remove), several in one call saved together or not at all, as Hermes. `corrections.md` and `drafts.md` are sealed. `resume.md` and `memory/private/` are guarded: readable only in a turn
with no web content, and reading one closes the web for that turn.

**Checkpoints**: `write_file`, `patch_file` and `memory_write` keep a file's old copy
first (`checkpoints.py`, newest 20); the user's `rollback` restores them.

**Follow-through** (`followthrough.py`, Hermes' /goal, but IGOR decides): a React
reply that ran out of steps or offers to go on is judged by 20b. On `continue`, up to
3 more rounds of 4 steps, each reply sent as it lands; stops at 45000 tokens or under
60000 left today. A round keeps the turn's web and private lockouts; any user message stops it.

**Cost**: Groq's free tier caps tokens per model per day; 120b's daily token budget
(200000) is shared by Direct and React. `usage.py` counts what IGOR spends.

**Deployment**: a root-owned script outside `/opt/igor` compiles, imports, runs the
offline tests, restarts, waits for Discord and rolls back on failure.

**MCP**: `mcp_server.py` serves six read-only tools to the user's other agents, over SSH on stdio. No port,
no writes, no private files.

<!-- END SELF SUMMARY -->

---

Describes the system **as it exists today**, verified against the source on
2026-10-17. **This file is reality**; when it disagrees with the code it is wrong -
fix it in the same commit. IGOR quotes it. **Keep the summary above short**,
and the whole file inside ten 3700-character `read_file` windows.

---

## Stack and host

- **Language:** Python 3.10
- **Interface:** discord.py
- **Models:** Groq free tier through the `openai` SDK, per-agent models
- **Scheduling:** APScheduler
- **Search:** exa-py
- **Persistence:** markdown files plus SQLite, no database server
- **Host:** Oracle Cloud `VM.Standard.E2.1.Micro`, **x86_64**, Ubuntu 22.04,
  1 OCPU / 956 MB RAM plus a 2 GB swapfile, `/opt/igor`, systemd

---

## Message lifecycle

A Discord DM travels this path every time:

1. **`interfaces/discord_bot.py` `on_message`** - ignores its own messages and
   anything that is not a DM, caches the channel for proactive sends. The user's
   attachments go through `agents/inbound.py` first: voice notes become text
   (Whisper), up to 3 images are described by qwen inside untrusted markers, and
   PDFs, Word, Excel, notebooks and text are saved to `memory/inbox/` (untrusted)
   with the text in the message; a long one goes to React with a 20b summary instead.
2. **`orchestrator.process`** - security gate. `user_id != AUTHORIZED_USER_ID`
   returns `None`: silent drop, no reply, no acknowledgment. This is the only
   auth check in the system; everything downstream trusts it. A yes or no to a
   waiting approval comes next, then `commands.py` (Hermes' gateway commands: the whole
   message, no model). Other messages take one turn at a time, in order (Hermes' queue
   mode); `stop` ends the turn in flight at its next step.
3. **File-mode sniff** - a `file:` prefix sets `file_mode` and is stripped. A
   `file:` request skips the router entirely and goes to React, because it is
   document work regardless of what it asks for. Run-together clock times are
   then spelled out in code (`100am` -> `1:00am`, `_spell_out_times`); models read
   the digits as a number and scheduled "100am" for 10:00.
4. **`orchestrator._classify`** - fast paths first, no model call:

   ```
   contains    _SYNTH_TRIGGERS          -> SynthesizeResearch
   startswith  _RESEARCH_LOOP_TRIGGERS  -> ResearchLoop
   contains    _STOP_RESEARCH_TRIGGERS  -> StopResearch
   exact match _DIGEST_COMMANDS         -> Monitor
   regex       proposal decision / list -> ProposalDecision / ProposalList (code)
   regex       "save that as a skill"   -> SkillSave (code, one 20b call)
   parse       checkpoints / heartbeats -> Rollback / Heartbeat (code, no model)
   regex       _SCHEDULING              -> Schedule (React, tool required)
   regex       _REMEMBERING, skip a job -> Remember (React, memory_write required)
   regex       found jobs, "more about" -> React
   ```

   `_SCHEDULING` matches request shapes ("message me at", "every weekday", "make
   it 11:30"), never digest or watch changes; `_REMEMBERING` matches "remember
   that" and a correction right after one. Each requires its tool (`tool_choice`)
   on the first call, released if refused twice.

   Anything else goes to **one router call**: `MODELS["router"]`
   (`qwen/qwen3.8-27b`), `temperature=0`, `max_tokens=10`, `reasoning_effort`
   `none`, 15s timeout, on a bucket it has to itself. It returns one word, mapped
   by `_VERDICT_MAP`. The effort setting is load-bearing: without it the 10-token
   budget is spent on hidden reasoning before any verdict is emitted.

   Any failure - unparseable output, exception, timeout - falls through to React.
   The verdict is logged as `Router: <verdict> -> <destination>`.

5. **`orchestrator._route`** - builds a bound `call_claude`, sets max_tokens
   (2048 chat, 3072 file mode), dispatches to the destination.
6. **Evaluator** - file mode only. One PASS/FAIL contract check, one retry with
   feedback, then delivers with an `[Evaluator warning: ...]` prefix. Fails open.
7. **`orchestrator.process` tail** - critic (disabled), context update (each
   side stored up to 20000 chars, in memory and in SQLite), `[Monitor]` suffix
   when applicable. Each agent gets the running summary, the last 6 conversation
   messages, then the last 3 posts, within its budget, stamped with their time.
8. **`discord_bot`** - `_sanitize` maps typographic punctuation to ASCII, then
   chunks to Discord's 2000-char limit, hard-splitting over-long single lines.
9. **`orchestrator.follow_through`** - once a React reply is sent, `followthrough.py`
   may run more rounds on it (see the summary), typing in Discord while one runs.

---

## What actually runs

| Component | Reachable via | Model role | Tools |
|---|---|---|---|
| Orchestrator | every message | `router` | none |
| **Direct** (`agents/direct.py`) | `CHAT` | `chat` | **none, by design** |
| **React** (`agents/react.py`) | `TASK`, `file:`, router failure | `react` | all 21 |
| **Monitor** (`agents/monitor.py`) | `MONITOR`, digest commands, APScheduler | `summary` | none |
| **ConfigEdit** (`agents/prod_memory.py`) | `CONFIG` | `chat` | none, writes 3 files |
| **ResearchLoop** (`agents/research_loop.py`) | `deep research` prefix | `research` | none, fixed pipeline |
| Evaluator (`agents/evaluator.py`) | file mode only | `evaluator` | none |

`agents/research.py` is an Exa search wrapper (`_run_search`, `_format_results`),
called by React and Monitor.

### llm.complete - the shared text-completion wrapper

`llm.py` holds the only copy of "how a Groq text completion fails". Two failures
arrive as a 200 OK and are invisible to the caller: empty content, and non-empty
content with `finish_reason` `length`, which reads as a complete answer but was cut
mid-sentence. Both retry once at double budget (cap 4096), then return whatever came
back. A caller already at the cap makes one request, not two.

Routed through it: `orchestrator.call_claude` (so Direct, ConfigEdit and the critic
are covered), Monitor's three synthesis calls, Evaluator, and ResearchLoop's `_call`.

It also owns `llm.model_params`, which attaches the per-model request parameters
that fail as a 200 OK when omitted, keyed by model so no call site has to remember:
`reasoning_format` `hidden` for qwen, `reasoning_effort` `low` for both gpt-oss
models. An explicit caller value still wins, so ResearchLoop keeps the `low` it
measured. gpt-oss accepts low/medium/high and rejects `none` with a 400; qwen
accepts `none`. React relies on `model_params`. `reasoning_format` has no
openai-client parameter, so it goes in `extra_body`; as a keyword it raises TypeError.

Two call sites stay out, on purpose:

- **React's main loop** - its `finish_reason` handling is entangled with tool
  dispatch and `tool_use_failed` retries.
- **React's forced final answer** and **the router** - doubling could push React's
  trimmed prompt past the bucket; the router reads one word and fails fast.

Exceptions propagate. Callers handle rate limits differently on purpose: the
orchestrator notifies and sleeps on a per-minute limit but answers at once on the
daily cap, quoting the wait from Groq's 429 body (`_rate_limit_reply`), the evaluator fails open, Monitor drops the digest
section, the research loop stops and reports what it has.

### Direct

One model call, no tool schemas, on `openai/gpt-oss-120b`, through the
`call_claude` passed to it (rate-limit backoff and user notification). Its prompt
ends with `memory/user.md` (Hermes' USER.md) and how full it is; `memory_write` refuses
a write past 1500 characters, judged on a batch's result, and lists the entries.

Its prompt forbids claims about IGOR's state, features or performance it cannot
check (it names the check that would answer), saying IGOR lacks a capability, and
promising a future action.

### React

System prompt rebuilt each call: `clock.time_line()` and the next seven dates, then
`prompt_react.md` if present, else the built-in default; messages are `[system] +
context + user`. `search`, `fetch_url`, `read_file`, `memory_read`, `search_memory`,
`memory_write`, `scheduled_message` and `shell` attach, with `load_tools` for the other
thirteen (it returns their rules too; a direct call loads the tool and retries). Workers
and scheduled runs name their own list through `allowed_tools`. Each reply ends with subtext lines of what its saving tools really did,
and a save that failed is flagged whatever the reply says (Hermes' verifier).

`search_history` finds every word of a query in `context.db` (90 days, at most 10000
messages, both sides plus proactive sends), else the messages with the most of them:
numbered, dated excerpts, newest first; `around` reads two either side of a number.

`shell` checks `_hardline_refusal` before anything runs: Hermes' floor (root or
home wipes, fork bomb, `mkfs`, `dd` onto a device, a download piped into a shell)
plus the repo, memory, sudoers, the deploy machinery and force-push. Commands are
split on `; && || |` and quote-stripped with `shlex`, `sh -c` strings are checked
too, and unparseable quoting fails closed. Any part blocked means nothing runs.
Hermes' middle tier (`approvals.py`): recursive
deletes, `kill`, permission or key-file changes, table drops, `sh -c`/`python -c`
and git resets wait for the user's yes in Discord; 5 minutes of silence is a no.
`scheduled_message` is switched off after a web read, like the other tools that
change state (`agents/scheduled.py`). `goal` (`goals.py`) is too.

**Price watch, in full.** Rejected listings are marked seen; a failed judge fails
open and sends unfiltered (an unreadable 200 OK would otherwise swallow a real drop).

**Stock watch, in full.** Shopify's `/products/<handle>.js` gives variants with an
`available` boolean and a price in cents: keyless, no model call. `aoostar.com`
blocks Oracle's IPs (429s), so fetches fall back to the `r.jina.ai` relay (third-party,
public URLs only). A failed fetch carries the old state
forward (or the next success fakes a restock).

**Watch history** (`history.py`, `memory/history.db`) keeps every successful
observation (stock and price per variant, the price watch's cheapest match, each new
job or gig posting); a failed fetch writes nothing. About 130 rows a day, no model calls. React's `watch_history`
returns changes only, newest first, so a week of hourly "available" is one row.

**Checkpoints** (Hermes' rollback) are taken once per React turn, in
`memory/.checkpoints/`. In code: `rollback` lists, `rollback diff N` previews,
`rollback N [all|<file>]` restores, keeping files edited since unless `all`.

`task_update` adds a task to `tasks.md`, marks one done, or lists the open ones. An ambiguous `done` is refused with the candidates listed rather than
guessed, and it shares `monitor._parse_tasks` with the digest so the two can never
disagree about what is open.

`delegate_task` runs one isolated worker: `handle()` again with an empty context, a
restricted tool list, 8 iterations and `MODELS["worker"]`. Three constraints,
each covered by `tests/test_delegation.py`:

- **Worker tools are intersected with what the parent still holds**, not the full
  list, so a parent that read the web cannot hand a write to a worker.
- **Granting a worker `search` or `fetch_url` counts as the parent reading the web.**
  The worker returns text the parent acts on, so `_delegation_wants_web` folds into
  `batch_reads_web` and the parent's state-changing tools close as they would on a
  direct read. `delegate_task` is itself in `_QUARANTINED_AFTER_WEB`.
- **One worker at a time** (`_delegation_gate`), on 20b, not react's 120b: fanning
  out 429s the hot path while the user waits. Depth is capped at 1
  so workers cannot delegate, and at 3 delegations per turn. Workers never hold a
  tool in `_WORKER_FORBIDDEN`.

`execute_code` (`script_tool.py`, Hermes' tool of that name) runs a script in the sandbox with
memory hidden. It calls 8 read-only tools over stdin and stdout, under the turn's rules, 12
lookups and 60s at most; only what it prints returns, as untrusted. No private files or user.md.

`search_files` greps IGOR's own source for a regex and returns `path:line: text`,
with up to 6 lines of `context`. It skips private files, `.git` and the venv.

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
newest two), then shortens or blanks the history before this message, oldest
first. If it still does not fit, the loop breaks to the forced-final-answer path, which trims with
`keep_recent=0`.

**Search behaviour.** A date in a recency query is stripped (the model writes its
training-era year). A recency query with no `recency_days` gets a 180-day window
automatically. Each result is Exa's answer to the query from that page, not its first
500 characters, which were mostly navigation. These models make one tool call a step, so
`queries` takes up to 8 lookups in one call. `fetch_url` returns a page's text a window at
a time, or with `question` what it says on one thing; a page it cannot read goes through
Exa. A failed search says so, rather than reading as no results; DDGS backs Exa once the
`ddgs` package is installed, which it is not yet.

### Scheduled messages and presence

React can only give a `brief`, written to be self-contained. When due,
`compose.write` runs React fresh - no history, read-only tools (`search`,
`fetch_url`, `memory_read`, `search_memory`), 180s timeout; a brief naming IGOR
gets the self summary. If that fails or returns
nothing, the tool-less `compose()` writes it; if that fails too, the brief itself is
sent. Words the user dictated go in double quotes and are kept verbatim. A brief can ask
to speak only if something is so; the run answers `[SILENT]` and nothing goes (Hermes' cron).

A second brief due within 5 minutes of a pending one is merged into it. A correction
is `update`, which edits a pending message in place by id. `repeat` (daily, weekdays, weekly) moves
the entry to the next occurrence on the local calendar, so 09:00 stays 09:00 across
daylight saving. After downtime it sends one late message, never a backlog; resuming
a paused entry skips what was missed. Repeating messages are never merged, and each
delivery spends daily tokens on 120b. Each run sees the series' last three
messages, marked sent, never built. An IGOR
series gets its subject from code, by date.

Presence writes on 120b. Gates in code before any model call: silent 03:00-10:00 local, 4h
between messages, 2 a day, 6 model calls a day, never within 20 minutes of a live
conversation; the daily check-in waits until 2h after the user last spoke. It sees
the user's own messages from 3 days, `user.md` and its last 5 check-ins, no tasks.

**Heartbeat** (`heartbeats.py`, Hermes' /heartbeat; not `heartbeat.txt`):
`heartbeat every 1h <request with links>`, 15m to 7d, up to 3 pages, one at a time,
plus `pause`, `resume`, `clear`. Code diffs each page's visible text, ignoring times;
only a change reaches 20b, tool-less, which writes a line or `NO_REPLY`. 12 a day.

The job watch reads RemoteOK, WWR, Greenhouse and Remotive; code filters, 20b
picks by what each posting asks; picks go out with those asks, no note. On those remote-only boards a location naming
the US counts as open to the user. React's `search` (`source: jobs`) reads them
live, pay only as each posting states it; a job request forces that search first,
with user.md in the prompt and no read_file. The gig watch (`agents/gigs.py`)
reads the n8n forum Jobs board, r/forhire [Hiring] and HN "Who is hiring?" contract
posts; red flags drop in code or a daily 120b screen, blocking the poster; a failed
screen holds the gigs; picks go out in the post's own words.

### ConfigEdit

Edits exactly three files:
`digest_config.md`, `schedule_config.md`, `watchlist.md`. Rejects any other
filename, validates digest section names against the set the digest actually
reads, keeps one rolling `.bak` per file, and reports whether a restart is needed.

**If its output matches the current file, it writes nothing and says so**: a write
agent a question can reach needs a guard that does not depend on routing.

React's `memory_write` uses `memory_entries.py` (duplicates, injection patterns and
whole-file overwrite refused), against `prod_memory._ALLOWED_FILES`.

### ResearchLoop

**Not a ReAct loop.** Each iteration is a fixed pipeline of isolated calls:

1. **PLAN** - sees the question and prior findings, returns one search query
2. **SEARCH** - no model, `research._run_search`
3. **DISTILL** - sees only this one search's raw results, returns 3-5 sourced
   findings plus a `Next:` thread. The raw results are discarded here and never
   enter another context
4. **APPEND** - code, not a model, writes to `research.md`

Every model call goes through `_call`, a wrapper over `llm.complete`. **Both calls run at `reasoning_effort` low**,
4x to 7x fewer completion tokens than unset.

Findings are delivered as a **raw file attachment with no model pass over them**,
followed by an offer to synthesize on request. A `synthesize research` fast path
routes to React with an instruction to read the file and not search.

This is also the Dual LLM pattern: the context that sees web content holds no tools.

### Overnight loop

Standing goals worked while the user is away, on `gpt-oss-20b` only (never the 120b
chat bucket).

**Goals** (`goals.py`, `memory/goals.md`): blocks `## g<n> <statement>` with `kind`
(`research` or `self_improve`), `status` (active or paused), `created`,
`last_reviewed`, `next_action`. Retired goals move to `goals_archive.md`. A block
missing a field is skipped with a warning and left as written. React's `goal` tool
(add, list, pause, resume, retire) is the only writer besides the loop.

**Tick** (`monitor._overnight_tick`, cron at minute 20 of every hour, one instance):
`overnight.tick()` returns at once outside 00:00-06:59 local, after a stop, at the
budget, or with no active goal. Otherwise it runs one session for the active goal
reviewed longest ago, logs it, and saves `next_action`, so a restart loses at most
one session. A failed session also marks its goal reviewed, so it cannot wedge the
rotation. It never raises. A session is a fixed pipeline: model calls with no tools,
web search done by code, files written by code only under `memory/work/` plus each
goal's progress line in `memory/goals.md`. Nothing it runs can message anyone; its only
outbound call is the web search. Calls use `config.OVERNIGHT_MODEL`, the paper write
`OVERNIGHT_WRITE_MODEL` (120b); the budget counts both.

**Research session**: up to 3 PLAN / SEARCH / DISTILL steps (ResearchLoop prompts)
append sourced findings to `memory/work/<goal>/findings.md`. At 12 new findings or 3
sessions the goal's one living `paper.md` is written, then revised (under 7500 chars;
code cuts a long draft's least-sourced sections, never the shortlist or unsure).
Code checks a "What I am unsure about" heading, 3 distinct links from the
findings or paper and a revision keeping a third of its links, one retry told why,
then the Evaluator (FAIL keeps a draft). 20b, not the writer, labels each shortlist
item (independent, self-reported, no income shown; unchecked if that fails); code
sorts strongest first, and PLAN is told which ideas still lack independent proof.
A pass moves the old paper to `versions/`; the change note comes from headings, which
PLAN also sees. A failed write waits a night.

**Self-improvement session**: `corrections.md` entries and ERROR lines in
`config.LOG_FILE` newer than `seen.json` go in as flattened, capped data (12, 25;
`rate-limited` skipped), with 40 recent non-docs commit subjects so it does not
re-propose fixed mistakes. At most 5 JSON proposals, stored in
`work/self-improvement/proposals.md` (30 pending, 40 decided kept).

**Proposals** (`proposals.py`, `proposal` tool: list, approve, reject): kinds
`preference`, `prompt`, `code`, `skill`. Approving a `preference` appends one dated line to
`memory/preferences.md`, one of two paths out of the work folder. `with_preferences`
adds those lines to the prompts of Direct and React and to
the job ranking profile. Approving a `prompt` or `code` proposal only marks it for a
Claude Code session. `proposal list` counts as an untrusted read: for the rest of that
turn the quarantine above closes `proposal` itself, so approval needs a fresh message.
`goal list`, `read_file` under `memory/work/`, and reading `research*.md` or `goals.md`
count the same (Hermes: everything read is untrusted); `search_files` and
`search_memory` skip those files.

**Skills** (`skills.py`, Hermes' skills and curator with write approval on):
`work/self-improvement/tasks.jsonl` records each turn's tool calls. At night (turns of 5+
calls, 2 a night), or on "save that as a skill", a model drafts a procedure beside it and
raises a `skill` proposal showing the steps. Only the user's own "approve" installs it in
`memory/skills/`; the `proposal` tool cannot, and the file tools refuse both folders. React's
prompt indexes them under 200 tokens; `read_file` opens one and counts a use. The curator
calls no model: 14 days unused is stale, 30 moves to `.archive/`, nothing is deleted.

**Work folder and budget** (`work.py`): `memory/work/<goal>/` plus `log.json`
(300 entries). Per night, `OVERNIGHT_TOKENS` 60000 (counted from the ledger under label
`Overnight`) and `OVERNIGHT_SEARCHES` 12. A 429 stops the night (not one that hits
the Evaluator, which fails open); a daily-cap 429 logs
`daily cap 20b: <limit>`. 20b's daily cap is unmeasured; that limit belongs in
`config.DAILY_TOKEN_CAPS`.

**Digest** (`overnight.digest_section`, no model call): an `overnight` section in
`digest_config.md` adds finished papers (sent as attachments), the latest draft per goal, pending proposals,
tokens and searches spent, and any early stop (a capped night reports even with no
files).

---

## Prompt injection defence

React holds all three legs of the "lethal trifecta" in one context: private data
(memory files), untrusted content (`fetch_url`, `search`), and outward action
(`shell`, file writes, self-modification).

Four controls, none costing a model call:

1. **Framing.** Search and fetch results are wrapped in
   `[UNTRUSTED EXTERNAL CONTENT]` markers. The system prompt states that text
   inside has no authority regardless of what it claims, including claims to come
   from the user or the system.
2. **Quarantine.** The moment a web tool runs in a turn, or if the message carries a
   document or image, every state-changing tool
   (`_QUARANTINED_AFTER_WEB`: `shell`, `python_run`, the file writers, `restart_self`,
   `memory_write`, `scheduled_message`, `delegate_task`, `task_update`, `goal`,
   `proposal`) is withdrawn from the schema **and** refused at execution for the rest of that
   turn. Both layers, because one batch can hold a search and a shell call and runs in
   no set order, so it is judged whole.
3. **Secrets.** No tool reads or changes `.env`, key files, `.git/` or `*.local.*`
   (`_is_secret_file`), and `sanitize.redact` (Hermes' `redact_secrets`) strips key shapes
   and IGOR's own keys from tool results, outbound text and logs.
4. **Addresses.** `url_safety.py`, as Hermes': `fetch_url` and the heartbeat refuse
   loopback, private, link-local and metadata addresses on every redirect hop. DNS
   rebinding is the known gap.

Consequence: "search for X and save it to tasks.md" takes two turns, a confirmation
gate on any action derived from the open web.

---

## Models and rate limits

`config.MODELS` is the single source of truth. Groq TPM limits are **per model**
and independent buckets. Every general-purpose model Groq serves is 8000 TPM
(re-measured 2026-08-18):

| Role | Model | Bucket |
|---|---|---|
| `router` | `qwen/qwen3.8-27b` | 8000, sole occupant, ~28 tokens a call |
| `chat`, `react` | `openai/gpt-oss-120b` | 8000, shared |
| `research`, `evaluator`, `summary` | `openai/gpt-oss-20b` | 8000, shared |
| `worker` | `openai/gpt-oss-20b` | shares research's 8000 |
| `judge` | `openai/gpt-oss-20b` | shares research's 8000; follow-through, skill drafts |
| `screening` | `openai/gpt-oss-120b` | shares chat's 8000; one gig screen a day |
| `presence` | `openai/gpt-oss-120b` | shares chat's 8000; check-ins |
| `vision` | `qwen/qwen3.8-27b` | shares router's 8000; 2048 an image, when sent |
| `speech` | `whisper-large-v3-turbo` | audio seconds, not tokens |

`chat` and `react` share a bucket deliberately: the router sends each message to
one, never both, so they alternate instead of stacking. qwen is the only model that
answers inside the router's 10-token budget; a router outage falls back to React.

**Fallback chain** (Hermes' `fallback_providers`). `llm.create` makes every chat
completion for `llm.complete` and React. A 429 after retries benches the model to
Groq's named reset (60s if none), a missing model or refused key for an hour, a
5xx or dropped connection for a minute, and `config.FALLBACK_MODELS` is tried in
order: 120b, Cerebras' gpt-oss-120b (skipped until `CEREBRAS_API_KEY` is set), 20b.
A 400 on the primary is raised. A turn that used a backup gets a one-line notice.

**Daily caps and the token ledger.** Groq also caps tokens per day, per model, and
says so only in a 429 body. `config.DAILY_TOKEN_CAPS` holds the measured ones:
`gpt-oss-120b` is 200000 (2026-10-16). `usage.record`
adds every response's token count to `memory/token_ledger.json` hourly, per model
and agent (from `llm.complete`, React and the router); Monitor reports the last 24h
against the cap. It is IGOR's own count: spend that bypasses `record` is invisible.

`max_tokens` counts against TPM at request time, so prompt plus reservation must stay
under the model's limit.

Every model here reasons, so `max_tokens` covers hidden reasoning plus output;
under about 1024 returns empty content with a 200 OK.


---

## Memory and config files

All live in `memory/` on the server and are **gitignored** - they exist only on the
host, plus the daily archive `scripts/backup_memory.ps1` pulls to the user's PC (30
kept) and an hourly push of the `*.md` notes, minus `private/` and `resume.md`, to
the private repo `WoahBanz/igor-memory` by root's `memory-push.sh`, which alerts on
a failed push.

**Read at startup, restart required:** `schedule_config.md`

**Read per call, immediate effect:** `digest_config.md`, `watchlist.md`,
`prompt_*.md`

**Overnight loop files:** `goals.md` and `goals_archive.md`, `preferences.md`
(approved standing preferences, 1500 character cap, read per call) and `work/`.

**Data:** `context.db` (`CONTEXT_WINDOW = 6`, `CONTEXT_POSTS = 3`; kept 90 days,
`HISTORY_RETENTION_DAYS`), plus `tasks.md`, `projects.md`, `user.md`, `agents.md`,
`research*.md`.

**Compression** (`compression.py`, as Hermes): once 4 messages leave the window,
20b folds them into a running summary (`conversation_summary.json`, under 1800
chars) in the background, backing off 60/300/900s on failure. `_window` puts it
first as a system record, at most a fifth of the budget; not for SelfDescribe.

**Memory holds preferences, history and user facts, never architecture.**
`memory_write`'s description says so. Anything checkable against source belongs in
this file and is read with `read_file`.

### Agent prompt override pattern

Every agent's `_get_system_prompt()` returns `memory/prompt_<agent>.md` when it
exists and is not empty, else `_DEFAULT_SYSTEM_PROMPT`. Overrides take effect immediately, no restart. Reset by deleting or emptying the
file. Prompt files are edited through Claude Code sessions only: no tool can write one.

**No `prompt_*.md` files exist on the server.** Every agent runs its built-in
default, so the prompt in the source is the prompt in play.

---

## Startup checks

`main._smoke_test()` runs after `_ensure_memory_files()` and exits 1 rather than
raising, so the deploy gate rolls back. It verifies:

- seven `config.MODELS` roles present and non-empty (`worker` is not checked)
- `DISCORD_BOT_TOKEN`, `GROQ_API_KEY`, `AUTHORIZED_USER_ID` set, `CONTEXT_WINDOW`
  at least 1
- four routing fast paths resolve correctly (all on fast paths, so no API call)
- **every agent module imports.** `py_compile` checks syntax, not names, and
  agents are imported lazily, so a missing stdlib import otherwise passes the
  commit and fails on the first message that routes there
- the sandbox probe, which logs an ERROR but never refuses to start

---

## Safety stack

**What executes today.** `igor.service` runs `main.py`. User `igor` has no sudoers
entry, so only root-owned scripts restart IGOR. What protects the system:

- `main._smoke_test()` - config sanity plus importing every agent module; exits 1
  rather than raising, so a broken commit refuses to start
- systemd `Restart=always` - a bad deploy is a visible crash loop
- `heartbeat.sh`, 2-minute timer: Discord silent past 300s restarts IGOR and alerts, once per half hour, never during a deploy
- `prestart.sh`, systemd `ExecStartPre`, root: a start runs only the tree the last
  deploy approved (commit, tracked diff, untracked `.py`). Anything else is set
  aside for review, reset and alerted; it fails closed. The file tools refuse
  `venv/`, `.git/` and caches, which git cannot see

### Health-gated deploy (S.1, built 2026-08-10)

`/usr/local/lib/igor-deploy/deploy.sh`, root-owned and deliberately outside
`/opt/igor` so `write_file` cannot reach it. Logs every attempt to
`/var/log/igor-deploy.log`, which the `igor` user also cannot write.

Four gates in order. The first three run before any restart, so a change that fails
them never takes IGOR down:

| Gate | Catches | On failure |
|---|---|---|
| syntax | compiles every `.py` in memory | reset to previous commit, no restart |
| imports | the NameError class `py_compile` cannot see | reset to previous commit, no restart |
| tests | every offline `tests/test_*.py`, as igor, ~150s | reset to previous commit, no restart |
| **gateway** | `I.G.O.R. online` in the journal within 90s of restart | roll back, restart, alert |

**After a healthy deploy:** a crash is `Restart=always`, a dropped connection the
heartbeat check above. Self-written code runs only once a deploy passes it; any
other start sets it aside. Below the host (terminated once, July 2026), the PC's
`scripts/backup_memory.ps1` (daily backup, health check, alerts) and the hourly
`memory-push.sh` cover it.

---

## Dead weight

- **Critic is off** (`ENABLE_CRITIC = False`), so `_critic_pass` and
  `_CRITIC_PROMPT` never execute. GAMEPLAN V.1.

---

## Known sharp edges

1. **The router can misroute at the margins** (35 of 35 live, 2026-10-22). Guards
   cover the harmful cases; Monitor and SelfDescribe hand off what is not theirs.
2. **Still on Oracle Always Free.** An entitlement change can terminate the
   instance again, as it did in July 2026.

---

## Deliberately not built

Described in `IGOR_SPEC.md` or `GAMEPLAN.md`, absent from the code:

- **Dev and Comms as separate specialists.** The spec describes five; there are
  five routed destinations but Dev and Comms remain absorbed into React.
- **Any UI beyond Discord.** No Flutter or web interface. Spec Phase 2.
- **Spoken replies.** Voice notes are transcribed; speech waits for IGOR's own app,
  the user's choice.
- **Multi-user anything.** `AUTHORIZED_USER_ID` is a single int, memory files are
  global, `context.db` has no user column.
