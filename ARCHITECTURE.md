# ARCHITECTURE.md - What IGOR Actually Is

**IGOR: this section answers questions about yourself.** The rest of the file is
reachable with `offset`. Describe nothing unread.

## The whole system, in one page

A Discord bot, DMs only, one authorized user. Python 3.10 on a 956 MB Oracle host,
systemd. Groq free tier via the `openai` SDK, Exa for search, markdown and SQLite.

## What does NOT exist

- **No content filter or safety classifier** over output. `sanitize.clean` only
  maps typography; refusals come from the models, not from IGOR.
- **No external accounts.** No posting, email or social media. IGOR drafts; a
  human publishes. It never buys anything and never applies for a job.
- **No self-restart.** `restart_self` only tells the user a change needs deploying.
- **No container.** `shell` and `python_run` run in bubblewrap (`sandbox.py`): no
  network, secrets or sealed memory, read-only but `/tmp`, `.git` and
  `memory/workspace`; refused if it cannot start. The shell also refuses a blocklist and DMs the user for a yes
  before risky ones (approvals).
- **No `scheduler.yaml`, `run_agent()` or `igor` module.** Spoken replies only
  on the dashboard; voice notes, images and videos are read.

**React's 27 tools:** `search` (web, jobs), `clarify`, `memory_read`, `search_memory`,
`search_history`, `watch_history`, `search_files`, `delegate_task`, `task_update`, `python_run`,
`read_file`, `patch_file`, `write_file`, `restart_self`, `shell`, `process`, `fetch_url`,
`send_message`, `send_file`, `memory_write`, `scheduled_message`, `routine_schedule`, `goal`, `proposal`, `execute_code`, `board`, `browser`. No others. Twenty load on
request through `load_tools`. `send_file` sends Word, PDF, Excel or CSV.

**Fallback.** On a 120b rate limit, Direct and React use `gpt-oss-20b` until
Groq's reset.

**Commands** in code: `stop`, `retry`, `undo`, `usage`, `status`, `help`, `skills`, `steer`, `btw`, `board`, `plugins`.

**Routing.** Fourteen fast paths in code, then one router call
(`qwen/qwen3.8-27b`) returning one word. Any router failure goes to React.

| Destination | Handles | Model | Tools |
|---|---|---|---|
| Direct | `CHAT` | 120b | none; lookups go to React |
| React | `TASK`, router failure | 120b | all 27 |
| Monitor | `MONITOR`, digest | 120b, news 20b | none; status block only, else to Direct |
| ConfigEdit | `CONFIG` | 120b | none; digest sections, when named |
| ResearchLoop | `deep research` prefix | 20b | none, fixed pipeline |
| SelfDescribe | `SELF` - about IGOR itself | 120b | none; this file, Hermes ledger if named |

**Scheduled jobs** in `monitor.setup()`: morning digest (10:00
local), job watch (11:30), gig watch (11:45), price watch (2-hourly), stock watch
(hourly), these five retimed live on request; a Groq model check, an advocacy draft (Mondays), presence (10 min), due
scheduled messages and the page heartbeat (60s each) and the overnight tick (hourly).

**Watchers** (prices, restocks, jobs, gigs) stay silent unless something changed.

**Scheduled messages**: `scheduled_message` keeps timed messages in
`memory/scheduled.json`; one can run a script first or read another's output.
**Presence** is separate: one check-in
a day behind code gates, off unless `memory/presence_config.md` says `state: on`.

**Overnight loop**: hourly 00:00-06:59 local, a fixed pipeline with no acting tools works the standing goals in `memory/goals.md` into sourced
papers, proposals and skill drafts in `memory/work/`, reported in the digest. Nothing is applied
without the user's yes.

**Memory** is markdown in `memory/` plus `context.db`, the conversation store (90
days, `search_history`); messages leaving the window fold into a running summary
(`compression.py`, 20b) in every prompt. `memory_read` and
`memory_write` take names from a fixed list; `memory_write` edits dated entries. `corrections.md` and `drafts.md` are sealed. `resume.md` and `memory/private/` are guarded: readable only in a turn
with no web content, and reading one closes the web for that turn.

**Checkpoints**: `write_file`, `patch_file` and `memory_write` keep a file's old copy
first (`checkpoints.py`); the user's `rollback` restores them.

**Follow-through** (`followthrough.py`, Hermes' /goal, but IGOR decides): a React
reply that ran out of steps or offers to go on is judged by 20b. On `continue`, up to
3 more rounds of 4 steps; stops at 45000 tokens or under
60000 left today. A round keeps the turn's web and private lockouts; any user message stops it.

**Cost**: Groq caps tokens per model per day; Direct and React share 120b's
200000 budget. `usage.py` counts spend.

**Deployment**: a root-owned script outside `/opt/igor` tests, restarts, waits for
Discord and rolls back on failure.

**MCP**: `mcp_server.py` serves six read-only tools over SSH; `mcp_client.py` uses the user's servers.

**Dashboard**: status, keyed chat and voice, `/dashboard` on loopback.

**Off until configured**: `http_server.py`'s API and webhooks, `hooks.py`.

<!-- END SELF SUMMARY -->

---

Describes the system **as it exists today**. **This file is reality**; when it disagrees with the code it is wrong -
fix it in the same commit. IGOR quotes it. **Keep the summary above short**,
and the whole file inside ten 3700-character `read_file` windows.

---

## Stack and host

Python 3.10, discord.py, APScheduler, exa-py, per-agent Groq models. Host: **x86_64**,
Ubuntu 22.04, 1 OCPU, 956 MB plus a 2 GB swapfile, `/opt/igor`, systemd.

---

## Message lifecycle

A Discord DM travels this path every time:

1. **`interfaces/discord_bot.py` `on_message`** - ignores its own messages and
   anything that is not a DM, caches the channel for proactive sends. The user's
   attachments go through `agents/inbound.py` first: voice notes become text
   (Whisper; a stock no-speech phrase is silence unless the clip has speech), up to 3 images are described by qwen inside untrusted markers, and
   PDFs, Word, Excel, PowerPoint, EPUB, OpenDocument, notebooks and text are saved to `memory/inbox/` (untrusted)
   with the text in the message; a long one goes to React with a 20b summary instead. A video goes the same way:
   ffmpeg, sandboxed, gives Whisper its sound and qwen 3 frames.
2. **`orchestrator.process`** - security gate. `user_id != AUTHORIZED_USER_ID`
   returns `None`, a silent drop. The only auth check: everything downstream
   trusts it. A yes or no to a
   waiting approval comes next, then `commands.py` (Hermes' gateway commands: the whole
   message, no model). Other messages take one turn at a time, in order (Hermes' queue
   mode); `stop` ends the turn in flight at its next step. `steer <note>` reaches it there as
   a user message, or gets its own turn if too late (Hermes' /steer); `btw <question>` while
   one runs is answered by Direct on 20b (Hermes' /btw). Outside text can use neither.
3. **File-mode sniff** - a `file:` prefix sets `file_mode` and is stripped. A
   `file:` request skips the router and goes to React. Run-together times are
   spelled out (`100am` -> `1:00am`).
4. **`orchestrator._classify`** - fast paths first, no model call:

   ```
   contains    _SYNTH_TRIGGERS          -> SynthesizeResearch
   startswith  _RESEARCH_LOOP_TRIGGERS  -> ResearchLoop
   contains    _STOP_RESEARCH_TRIGGERS  -> StopResearch
   exact match _DIGEST_COMMANDS         -> Monitor
   regex       "send that as a PDF"     -> FileExport (code, no model)
   regex       "second opinion on ..."  -> SecondOpinion (see Second opinion)
   regex       proposal decision / list -> ProposalDecision / ProposalList (code)
   regex       "save that as a skill"   -> SkillSave (code, one 20b call)
   parse       checkpoints / heartbeats -> Rollback / Heartbeat (code, no model)
   regex       _SCHEDULING              -> Schedule (React, tool required)
   regex       _REMEMBERING, skip a job -> Remember (React, memory_write required)
   regex       found jobs / watch finds -> React / Watches (watch_history required)
   ```

   `_SCHEDULING` matches request shapes, never digest or watch changes; `_REMEMBERING`
   matches "remember that" and a correction right after one. Each requires its tool (`tool_choice`)
   on the first call, released if refused twice.

   Anything else goes to **one router call**: `MODELS["router"]`
   (`qwen/qwen3.8-27b`), `temperature=0`, `max_tokens=10`, `reasoning_effort`
   `none`, 15s timeout, on a bucket it has to itself. It returns one word, mapped
   by `_VERDICT_MAP`.

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
   chunks to Discord's 2000-char limit.
9. **`orchestrator.follow_through`** - once a React reply is sent, `followthrough.py`
   may run more rounds on it (see the summary), typing in Discord while one runs.

---

## What actually runs

| Component | Reachable via | Model role | Tools |
|---|---|---|---|
| Orchestrator | every message | `router` | none |
| **Direct** (`agents/direct.py`) | `CHAT` | `chat` | **none, by design** |
| **React** (`agents/react.py`) | `TASK`, `file:`, router failure | `react` | all 27 |
| **Monitor** (`agents/monitor.py`) | `MONITOR`, digest commands, APScheduler | `summary` | none |
| **ConfigEdit** (`agents/prod_memory.py`) | `CONFIG` | `chat` | none, writes 1 file |
| **ResearchLoop** (`agents/research_loop.py`) | `deep research` prefix | `research` | none, fixed pipeline |
| Evaluator (`agents/evaluator.py`) | file mode only | `evaluator` | none |

### llm.complete - the shared text-completion wrapper

`llm.py` holds how a Groq text completion fails: empty content, or `finish_reason` `length`, cut
mid-sentence, both as a 200 OK. Both retry once at double budget (cap 4096), then return what came back.

It also owns `llm.model_params`, the per-model parameters that fail as a 200 OK when omitted:
`reasoning_format` `hidden` for qwen, `reasoning_effort` `low` for both gpt-oss
models. An explicit caller value still wins, so ResearchLoop keeps the `low` it
measured. gpt-oss accepts low/medium/high and rejects `none` with a 400; qwen
accepts `none`. React relies on `model_params`; `reasoning_format` goes in `extra_body`.

React's main loop and forced final answer stay out (each handles `length` itself), as does the router.

Exceptions propagate. On a rate limit the orchestrator notifies and sleeps, or on the daily cap
answers at once with Groq's wait (`_rate_limit_reply`); the evaluator fails open, Monitor drops the
section, the research loop reports what it has.

### Direct

One model call, no tool schemas, on `openai/gpt-oss-120b`, through the
`call_claude` passed to it. Its prompt
ends with `memory/user.md` (Hermes' USER.md) and how full it is; `memory_write` refuses
a write past 1500 characters, judged on a batch's result, and lists the entries.

Its prompt forbids claims about IGOR's state it cannot check, saying IGOR lacks a capability,
and promising a future action. Facts it may lack (a product, company or event today) get a bare
`LOOKUP`, and React takes the turn. SelfDescribe adds `docs/HERMES_PARITY.md` when Hermes is named.

### React

System prompt rebuilt each call: `clock.time_line()` and the next seven dates, then
`prompt_react.md` if present, else the built-in default; messages are `[system] +
context + user`. `search`, `fetch_url`, `read_file`, `memory_read`, `search_memory`, `clarify` and
`shell` attach, with `load_tools` for the other nineteen (it returns their rules too; a direct
call loads the tool and retries; a request for a file, a reminder or a save opens it at once). Workers and scheduled runs name their own list through `allowed_tools`.
`clarify` (`clarify.py`) asks the user and waits 10 minutes. `write_file` replaces
only a file this turn read whole, unchanged since. A result over 4000 characters is kept for the turn
and read on as `result:N`, never on disk. Each reply ends with subtext lines of what its saving tools really did,
and a save that failed is flagged whatever the reply says (Hermes' verifier). A link in any
reply must appear in what was read that turn, the message or the conversation (`links.py`);
any other is taken out, with a line saying how many.

`search_history` finds every word of a query in `context.db` (90 days, at most 10000
messages, both sides plus proactive sends), else the messages with the most of them:
numbered, dated excerpts, newest first; `around` reads two either side of a number.

`shell` checks `_hardline_refusal` before anything runs: Hermes' floor (root or
home wipes, fork bomb, `mkfs`, `dd` onto a device, a download piped into a shell)
plus the repo, memory, sudoers, the deploy machinery and force-push. Unparseable quoting fails closed.
Hermes' middle tier (`approvals.py`): recursive
deletes, `kill`, permission or key-file changes, table drops, `sh -c`/`python -c`
git resets and invisible characters wait for the user's yes in Discord; "no, do X" hands X
to the turn; 5 minutes of silence is a no.
`scheduled_message` and `goal` close after a web read too.

**Stock watch.** Shopify's `/products/<handle>.js`: keyless, no model call. Blocked
fetches fall back to the `r.jina.ai` relay (public URLs only).

**Watch history** (`history.py`, `memory/history.db`): React's `watch_history` returns changes only.

**Checkpoints** (Hermes' rollback), once per React turn: `rollback` lists, `rollback diff N` previews,
`rollback N [all|<file>]` restores.

`task_update` adds, finishes or lists tasks in `tasks.md`.

`delegate_task` runs one isolated worker: `handle()` with an empty context, a restricted tool list,
8 iterations and `MODELS["worker"]` (20b), one at a time, depth 1, 3 a turn. Its tools are intersected
with what the parent still holds and never include `_WORKER_FORBIDDEN`; granting it `search` or
`fetch_url` counts as the parent reading the web.

`execute_code` (`script_tool.py`, Hermes' tool of that name) runs a script in the sandbox with
memory hidden. It calls 8 read-only tools over stdin and stdout, under the turn's rules, 12
lookups and 60s at most; only what it prints returns, as untrusted. No private files or user.md.

`search_files` greps IGOR's own source, skipping private files.

**Files** (`deliver.py`, Hermes' deliverable mode): `send_file` takes Markdown, a server file or the
last reply, and the name's extension picks Word, PDF, Excel, CSV, Markdown or text, rendered in code
with the standard library. Paths follow `read_file`'s rules; it stays open after a web read, as a
reply does. "Send that as a PDF" needs no model. No HTML, and a CSV cell cannot run as a formula.

**Background** (`background.py`, Hermes' terminal background and process_manage): `shell` with
`background` runs up to 30 minutes, two at a time, at low priority, and code tells the user when it
ends. `process` lists, polls, reads or kills. A restart ends them; the next start says so. `shell` and
`python_run` can write `memory/workspace`, kept a week up to 200 MB. Prerun scripts cannot.

**Second opinion** (`moa.py`, Hermes' /moa): 20b and qwen answer from the conversation's text, no tools, and
React gets that as a private trailing message.

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
first, then forces the final answer.

**Search behaviour.** Dates are stripped from queries; a recency query gets a 180-day window by
default. These models make one tool call a step, so
`queries` takes up to 8 lookups in one call. `fetch_url` returns a page's text a window at
a time, or with `question` what it says on one thing; a page it cannot read goes through
Exa. A failed search says so, rather than reading as no results; DDGS backs Exa, with one
retry, except for proof checks.

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
the entry to its next local occurrence, so 09:00 stays 09:00 across
daylight saving; an interval (3 hours at the shortest, 5 minutes with a script) steps by its
minutes. `times` limits the runs; a run ending `[DONE]` on its own line ends the series (Hermes'
LOOP_COMPLETE) and says so; one ending a line is dropped. After downtime: one late message, never a backlog; resuming
a paused entry skips what was missed. Repeating messages are never merged; each
delivery spends 120b tokens and sees the series' last three messages and a line
each of the 20 before, marked sent, never built. An IGOR
series gets its subject from code, by date, and this file's paragraphs on it (`_area_detail`).

**Scripts and chains** (`prerun.py`, Hermes' cron `script`, `no_agent`, `context_from`): a message
can name a script in `memory/private/cron_scripts/`, sealed from every tool, so only the user or
Claude Code adds one. It runs first in the sandbox, but with the network and its own folder
(`IGOR_STATE`), 60s, and can read ordinary memory. Printing nothing or `{"wakeAgent": false}`
means no model call and no message; failures alert once a streak. `script_only`, or no brief, sends
what it prints with no model. `context_from` reads up to 3 other
messages' latest output (`scheduled_outputs.json`); `keep_only` keeps a result unsent. A writer
sees all this as outside text.

Presence writes on 120b. Gates in code before any model call: silent 03:00-11:00 local, 4h
between messages, 2 a day, 6 calls a day, never within 20 minutes of a live
conversation; the daily check-in waits 2h after the user last spoke. It sees
the user's messages from 3 days, `user.md` and its last 5 check-ins; both triggers get the
standing preferences and no tasks or drafts. Not sent: one on IGOR's own posts, or a
check-in on a subject of its last 3.

**Heartbeat** (`heartbeats.py`, Hermes' /heartbeat; not `heartbeat.txt`):
`heartbeat every 1h <request with links>`, 15m to 7d, up to 3 pages, one at a time,
plus `pause`, `resume`, `clear`. Code diffs each page's visible text, ignoring times;
only a change reaches 20b, tool-less, which writes a line or `NO_REPLY`. 12 a day.

The job watch reads RemoteOK, WWR, Greenhouse and Remotive; code filters (and drops one asking more
years than job_search.md's `## Most years asked`), 20b picks by what each posting asks; picks go out with those asks, no note. On those remote-only boards a location naming
the US counts as open to the user. React's `search` (`source: jobs`) reads them
live, pay only as each posting states it; a user's job request forces that search first,
with user.md in the prompt and no read_file. The gig watch (`agents/gigs.py`)
reads the n8n forum Jobs board, r/forhire [Hiring] and HN "Who is hiring?" contract
posts; red flags drop in code or a 120b screen, blocking the poster; a post under 25 words
drops; a failed screen holds the gigs; picks go out in the post's own words. The user writes any pitch.

### ConfigEdit

Edits only `digest_config.md`, checks section names against the ones the digest reads, keeps a
rolling `.bak`. Times are `routine_schedule`'s: `routines.py` writes `schedule_config.md` and retimes the live job.

React's `memory_write` uses `memory_entries.py` (duplicates, injection patterns and
whole-file overwrite refused), against `prod_memory._ALLOWED_FILES`.

### ResearchLoop

**Not a ReAct loop.** Each iteration is a fixed pipeline of isolated calls:

1. **PLAN** - sees the question and prior findings, returns one search query
2. **SEARCH** - no model, `research._run_search`
3. **DISTILL** - sees only this search's raw results, returns 3-5 sourced
   findings and a `Next:` thread; the raw results go no further
4. **APPEND** - code, not a model, writes to `research.md`

Every model call goes through `_call`, a wrapper over `llm.complete`. **Both calls run at `reasoning_effort` low**,
4x to 7x fewer completion tokens than unset.

Findings go out as a raw file attachment with no model pass, then an offer to synthesize, which
routes to React with an instruction to read the file and not search.

### Hooks and HTTP

**Hooks** (`hooks.py`, Hermes' shell hooks): scripts in `memory/private/cron_scripts/hooks/`, sealed, run
in prerun's sandbox on `startup`, `pre_tool_call` (exit 2 or JSON blocks; anything else fails open),
`post_tool_call` and `agent_end`, with JSON on stdin. What they print goes to the user. None is placed.

**HTTP** (`http_server.py`, Hermes' API server and webhooks), on loopback, off until configured:
`API_SERVER_KEY` opens `/v1/chat/completions`, the newest user message going through
`orchestrator.process`. Each route in `memory/private/webhooks.json`, signed, rate-limited and deduplicated,
sends its rendered prompt plainly or through a read-only run, 24 a day.

**Dashboard** (`dashboard.py`, Hermes' web dashboard): tabs for chat, Agents (13 cards, a week of calls per
ledger label in `memory/agent_activity.json`), Tasks, Memory and System. Status needs no login on loopback, other
Hosts refused; chat, notes, proposals and the night's work need the key in `memory/private/dashboard.key`, which
no tool or sandbox reads; the PC launcher fetches it over SSH. Voice (`voice.py`): Whisper hears Talk, labelled
`(voice note)`; replies are read in Orpheus, or the browser's voice while Orpheus is refused.

### Plugins, the board and the browser

**Plugins** (`plugins.py`, `plugin_runner.py`): Hermes' format, `register(ctx)` with tools, commands and hooks,
in the sealed `memory/private/cron_scripts/plugins/`, off until `enable plugin x`. Each call is a fresh process in
the sandbox: network, no memory, no key but what it names, never IGOR's. Tools count as web tools. None is placed.

**Browser** (`browser.py`, Hermes' Lightpanda engine): one session over CDP, in the sandbox with
`--block-private-networks`, closed after 2 idle minutes or past 400 MB. A call is a list of steps. No password
fields, no non-GET request. Menus go last. A web tool.

**Board** (`board.py`, `memory/board.db`, Hermes' Kanban): tasks wait on parents. A 60-second dispatcher runs one
at a time on 20b with read-only tools, 8 a day. A parent's result reaches a child as untrusted text. Two failures
or `[BLOCKED: why]` stop a task and say so; the user hears when a chain ends. The `board` tool closes after a web read.

### MCP client

`memory/private/mcp_servers.json`, which no tool can reach, lists servers: read-only tools only, deferred, one
connection a call, results treated as web text. None is configured yet.

### Overnight loop

Standing goals worked while the user is away.

**Goals** (`goals.py`, `memory/goals.md`): blocks `## g<n> <statement>` with `kind`
(`research` or `self_improve`), `status` (active or paused), `created`,
`last_reviewed`, `next_action`. Retired goals move to `goals_archive.md`. React's `goal` tool
(add, list, pause, resume, retire) is the only writer besides the loop.

**Tick** (`monitor._overnight_tick`, cron at minute 20 of every hour, one instance):
`overnight.tick()` returns at once outside 00:00-06:59 local, after a stop, at the
budget, or with no active goal. Otherwise it runs one session for the active goal
reviewed longest ago, logs it, and saves `next_action`. A failed session also marks its goal reviewed.
A session is a fixed pipeline: model calls with no tools, web search by code (its only outbound call),
files written by code only under `memory/work/` plus `next_action` in `memory/goals.md`. The paper write uses `OVERNIGHT_WRITE_MODEL`
(120b), the rest `OVERNIGHT_MODEL`; the budget counts both.

**Research session**: up to 3 PLAN / SEARCH / DISTILL steps (ResearchLoop prompts)
append sourced findings to `memory/work/<goal>/findings.md`. At 12 new findings or 3
sessions the goal's one living `paper.md` is written, then revised (under 7500 chars;
code cuts the weakest-proven, then least-sourced sections, never the shortlist or unsure).
Code checks a "What I am unsure about" heading, 3 distinct links from the
findings or paper and a revision keeping a third of its links, one retry told why,
then the Evaluator (FAIL keeps a draft). 20b, not the writer, labels each shortlist
item it can (unchecked if that fails; on an income goal, only the proof check); code sorts strongest first.
A pass moves the old paper to `versions/`; the change note comes from headings, which
PLAN also sees.

**Proof session** (`proof.py`): on an income goal, shortlisted ideas are tested before
research widens: 3 fixed Exa searches (TrustMRR, earnings data, failures). 20b says what each shows; code keeps a quote only if it is on the page, numbers
intact, money one earner received, $1000 to $20M, or a failure on this idea, and sets the source for personal
posts, claims, cited hosts and TrustMRR. `proof.json` outlives revisions; code writes
`## Proof checked` and the labels (independent, mixed, similar product, self-reported,
no income shown, unchecked, against). Code drops a summary sentence crediting income to one without.

**Self-improvement session**: `corrections.md` entries and ERROR lines in
`config.LOG_FILE` newer than `seen.json` go in as flattened, capped data (12, 25;
`rate-limited` skipped), with 40 recent non-docs commit subjects so it does not
re-propose fixed mistakes. At most 5 JSON proposals, stored in
`work/self-improvement/proposals.md` (30 pending, 40 decided kept, unanswered ones expire in 7 days).

**Memory review** (`memory_review.py`, Hermes' background review): there, and by day after 15 quiet
minutes, 20b reads the user's new messages for at most 3 items. Code checks each quote is theirs
(first-person for a fact); a fact goes to `user.md`, a preference to `preferences.md`, unasked and
named under the next reply. A replacement, or a fact for a full file with the lines 120b chose to
drop, waits for one "approve".

**Proposals** (`proposals.py`, `proposal` tool: list, approve, reject): kinds
`preference`, `prompt`, `code`, `skill`, `memory`. Approving a `preference` appends one dated line to
`memory/preferences.md`; `with_preferences` adds them to Direct's, React's and the job ranking's prompts. Approving a `prompt` or `code` proposal only marks it for a
Claude Code session. `proposal list` is an untrusted read: the quarantine then closes `proposal` for the
turn, so approval needs a fresh message.
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
`Overnight`) and `OVERNIGHT_SEARCHES` 12. A 429 stops the night; a daily-cap 429 logs
`daily cap 20b: <limit>`. 20b's daily cap is unmeasured.

**Digest** (`overnight.digest_section`, no model call): an `overnight` section in
`digest_config.md` adds finished papers (sent as attachments), proof verdicts, the latest draft per goal, pending proposals,
tokens and searches spent, and any early stop.

---

## Prompt injection defence

React holds all three legs of the "lethal trifecta" in one context: private data
(memory files), untrusted content (`fetch_url`, `search`), and outward action
(`shell`, file writes, self-modification).

Four controls, none costing a model call:

1. **Framing.** Search and fetch results are wrapped in
   `[UNTRUSTED EXTERNAL CONTENT]` markers. The system prompt states that text
   inside has no authority, whoever it claims to come from.
2. **Quarantine.** The moment a web tool runs in a turn, or if the message carries a
   document or image, every state-changing tool
   (`_QUARANTINED_AFTER_WEB`: `shell`, `python_run`, the file writers, `restart_self`,
   `memory_write`, `scheduled_message`, `delegate_task`, `task_update`, `goal`,
   `proposal`) is withdrawn from the schema **and** refused at execution for the rest of that
   turn; a batch holding both is judged whole.
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
and independent buckets. Every general-purpose model Groq serves is 8000 TPM:

| Role | Model | Bucket |
|---|---|---|
| `router` | `qwen/qwen3.8-27b` | 8000, sole occupant, ~28 tokens a call |
| `chat`, `react`, `screening`, `presence` | `openai/gpt-oss-120b` | 8000, shared; a gig screen and check-ins daily |
| `research`, `evaluator`, `summary`, `worker`, `judge` | `openai/gpt-oss-20b` | 8000, shared |
| `vision` | `qwen/qwen3.8-27b` | shares router's 8000; 2048 an image, when sent |
| `speech` | `whisper-large-v3-turbo` | audio seconds; 2000 requests a day |
| `voice` | `canopylabs/orpheus-v1-english` | billed by character; unmeasured |

`chat` and `react` share a bucket deliberately: the router sends each message to
one, never both, so they alternate instead of stacking.

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
and agent (from `llm.complete`, React and the router); Monitor reports the last 24h. The
cap refills at cap/86400 a second (`usage.headroom`), as real 429s show.

Every model here reasons, so `max_tokens` covers hidden reasoning plus output;
under about 1024 returns empty content with a 200 OK.


---

## Memory and config files

All live in `memory/` on the server and are **gitignored** - they exist only on the
host, plus the daily archive `scripts/backup_memory.ps1` pulls to the user's PC (30
kept) and an hourly push of the `*.md` notes, minus `private/` and `resume.md`, to
the private repo `WoahBanz/igor-memory` by root's `memory-push.sh`, which alerts on
a failed push.

**Read at startup, retimed live by `routines.py`:** `schedule_config.md`

**Read per call, immediate effect:** `digest_config.md`, `prompt_*.md`. No watchlist
file: Monitor reports what runs.

**Overnight loop files:** `goals.md` and `goals_archive.md`, `preferences.md`
(approved standing preferences, 1500 character cap, read per call) and `work/`.

**Data:** `context.db` (`CONTEXT_WINDOW = 6`, `CONTEXT_POSTS = 3`; kept 90 days,
`HISTORY_RETENTION_DAYS`), plus `tasks.md`, `projects.md`, `user.md`, `agents.md`,
`research*.md`.

**Compression** (`compression.py`, as Hermes): once 4 messages leave the window,
20b folds them into a running summary (`conversation_summary.json`, under 1800
chars) in the background. `_window` puts it
first as a system record, at most a fifth of the budget; not for SelfDescribe.

**Memory holds preferences, history and user facts, never architecture.**
Anything checkable against source belongs in
this file and is read with `read_file`.

### Agent prompt override pattern

Every agent's `_get_system_prompt()` returns `memory/prompt_<agent>.md` when it
exists and is not empty, else `_DEFAULT_SYSTEM_PROMPT`. Overrides take effect immediately, no restart.
Prompt files are edited through Claude Code sessions only: no tool can write one.

**No `prompt_*.md` files exist on the server**, so the prompt in the source is the
prompt in play.

---

## Startup checks

`main._smoke_test()` exits 1 rather than raising, so the deploy gate rolls back. It verifies the
`config.MODELS` roles, the keys, four routing fast paths and that **every agent module imports**
(they load lazily). The sandbox probe only logs.

---

## Safety stack

**What executes today.** `igor.service` runs `main.py`. User `igor` has no sudoers
entry, so only root-owned scripts restart IGOR. What protects the system:

- systemd `Restart=always` - a bad deploy is a visible crash loop
- `heartbeat.sh`, 2-minute timer: Discord silent past 300s restarts IGOR and alerts, once per half hour, never during a deploy
- `prestart.sh`, systemd `ExecStartPre`, root: a start runs only the tree the last
  deploy approved (commit, tracked diff, untracked `.py`). Anything else is set
  aside for review, reset and alerted; it fails closed. The file tools refuse
  `venv/`, `.git/` and caches, which git cannot see

### Health-gated deploy (S.1)

`/usr/local/lib/igor-deploy/deploy.sh`, root-owned, outside `/opt/igor` so `write_file` cannot reach it,
logs every attempt to `/var/log/igor-deploy.log`, which `igor` cannot write either.

Four gates. Syntax (every `.py` outside `memory/`), imports (the NameError class `py_compile` misses) and
every offline `tests/test_*.py` (as igor, ~5 min) run before any restart and reset to the previous commit, so
IGOR never goes down for them. **Gateway**: `I.G.O.R. online` in the journal within 90s, or roll back and alert.

**After a healthy deploy:** a crash is `Restart=always`, a dropped connection the heartbeat above.
Below the host, the PC's `scripts/backup_memory.ps1` and the hourly `memory-push.sh` cover it.

---

## Known sharp edges

1. **The router can misroute at the margins** (35 of 35 live, 2026-10-22). Guards
   cover the harmful cases; Monitor, SelfDescribe and Direct hand off what is not theirs.
2. **Oracle Always Free** can terminate the instance, as in July 2026.
3. **The critic is off** (`ENABLE_CRITIC`), so `_critic_pass` never runs.

---

## Deliberately not built

Described in `IGOR_SPEC.md` or `GAMEPLAN.md`, absent from the code:

- **Dev and Comms as separate specialists.** Absorbed into React.
- **Any UI beyond Discord and the dashboard.** Spec Phase 2.
- **Multi-user anything.**
