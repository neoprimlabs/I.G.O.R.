# ARCHITECTURE.md - What IGOR Actually Is

Describes the system **as it exists today**, verified against the source on
2026-08-01. Nothing aspirational appears here.

- `IGOR_SPEC.md` is the vision. Some of it has never been built.
- `GAMEPLAN.md` is the plan. All of it is unbuilt by definition.
- **This file is reality.** If it disagrees with the code, the code wins and this
  file is wrong - fix it in the same commit.

---

## The shape, in one sentence

An orchestrator with a keyword router in front of one tool-using generalist
agent, plus a scheduler and a research loop bolted alongside.

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
3. **File-mode sniff** - a `file:` prefix sets `file_mode` and is stripped. This
   is a flag, not a route.
4. **`orchestrator._classify`** - pure keyword matching, no model call:

   ```
   substring in _MONITOR_TRIGGERS       -> Monitor
   substring in _STOP_RESEARCH_TRIGGERS -> StopResearch
   startswith  _RESEARCH_LOOP_TRIGGERS  -> ResearchLoop
   anything else                        -> React
   ```

   That last line is why React handles nearly everything.
5. **`orchestrator._route`** - builds a bound `call_claude`, sets max_tokens
   (2048 chat, 3072 file mode), dispatches.
6. **`agents/react.py` `handle`** - the ReAct loop. Detailed below.
7. **Evaluator** - file mode only. One PASS/FAIL contract check, one retry with
   feedback, then delivers with an `[Evaluator warning: ...]` prefix. Fails open.
8. **`orchestrator.process` tail** - critic (disabled), context update (both
   sides truncated to 1500 chars, written to the in-memory list and SQLite),
   `[Monitor]` suffix when applicable.
9. **`discord_bot`** - `_sanitize` maps typographic punctuation to ASCII, then
   chunks to Discord's 2000-char limit, hard-splitting over-long single lines.

### Inside the ReAct loop

System prompt is rebuilt on every call from three parts: current UTC datetime,
then `prompt_react.md` if it exists and is non-empty otherwise the built-in
default, then learned skills from `skills_react.md`. Messages become
`[system] + context window + user message`. All 12 tools are attached unless
`allowed_tools` narrows them.

Then up to 8 iterations:

| `finish_reason` | Behavior |
|---|---|
| `stop` | return content, done |
| `tool_calls` | dedupe against `seen_calls`, run in parallel, cap each result at 4000 chars, append, loop |
| `length` | return partial content, or retry once with doubled budget (max 4096) |
| `BadRequestError` + `tool_use_failed` | feed the rejection text back as a user note, retry up to 3 times |

On iteration exhaustion, one final tool-free call forces a real answer.

**The 12 tools:** `search`, `memory_read`, `search_memory`, `python_run`,
`read_file`, `patch_file`, `write_file`, `restart_self`, `shell`, `fetch_url`,
`send_message`, `memory_write`.

---

## What actually runs

| Component | Reachable via | Model role |
|---|---|---|
| Orchestrator | every message | none (keyword only) |
| React (`agents/react.py`) | default for anything unmatched | `react` |
| Monitor (`agents/monitor.py`) | trigger words + APScheduler | `summary` |
| ResearchLoop (`agents/research_loop.py`) | `deep research` prefix | `research` |
| Evaluator (`agents/evaluator.py`) | file mode only | `evaluator` |

### Not agents, despite living in `agents/`

- **`prod_memory.py`** is a write helper. React calls `_write_to_memory` through
  the `memory_write` tool. It is not routable.
- **`research.py`** is an Exa search wrapper (`_run_search`, `_format_results`).
  React and Monitor call it. It is not routable.

This naming is a significant source of confusion when reading the tree.

### File map

| File | Role |
|---|---|
| `main.py` | entry point: logging, memory-file templates, starts the bot |
| `orchestrator.py` | keyword classifier, `call_claude()` helper, `Orchestrator`, critic (disabled) |
| `agents/react.py` | the ReAct tool loop. 12 tools, 8 iterations, dedupe and retry guards |
| `agents/monitor.py` | scheduled digest and watchlist via APScheduler. Read-only by design |
| `agents/research_loop.py` | deep research loop; timestamps and archives `research.md` before each run |
| `agents/evaluator.py` | PASS/FAIL contract check on file-mode output. Fails open |
| `agents/prod_memory.py` | memory-write helper with allowlists. Not an agent. Becomes ConfigEdit in R2.3 |
| `agents/research.py` | Exa search helpers. Not an agent |
| `interfaces/discord_bot.py` | DMs only, `_PUNCT_MAP` sanitizer, 2000-char chunker |
| `context_store.py` | SQLite rolling context |
| `config.py` | env and settings. **Single source of truth for models and context window** |
| `watchdog.py`, `start.sh` | safety stack layers 2 and 1/3 |
| `scripts/backup_memory.ps1` | daily off-host backup and health alerting. Runs on the Windows machine, not the server |

`agents/direct.py` is referenced in GAMEPLAN R2.1 and **does not exist yet**.

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
`prompt_*.md`, `skills_react.md`

**Data:** `context.db` (SQLite rolling context, `CONTEXT_WINDOW = 6`, 200 rows
retained on disk), plus `tasks.md`, `projects.md`, `user.md`, `agents.md`,
`research*.md`.

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

**Status as of 2026-08-02: no `prompt_*.md` files exist on the server.** Every
agent is running its built-in default. The mechanism is supported and unused, so
if an agent behaves unexpectedly, the prompt in the source is the prompt in play.
`skills_react.md` is the exception that still modifies React's prompt at runtime.

---

## Safety stack

1. **`start.sh`** - `compileall` before launch; on failure `git checkout -- .`
   and retry.
2. **`watchdog.py` / `igor-watchdog.service`** - independent systemd service.
   IGOR writes `restart_requested`, the watchdog restarts within 5s, 300s
   cooldown.
3. **`start.sh` crash recovery** - `.crash_detected` marker on non-zero exit
   restores last known good code on next boot.

All three operate **above** the host. None can fire if the machine itself
disappears, which is exactly what happened in July 2026.

---

## Dead weight

Present in the tree, does nothing:

- **Critic is off** (`ENABLE_CRITIC = False`), so `_critic_pass`,
  `_CRITIC_PROMPT` and `_write_skill` never execute. Consequence worth knowing:
  `skills_react.md` is still injected into every React prompt, but nothing
  writes to it anymore. It is frozen at whatever it currently holds.
- `main.py` creates `skills_research.md`, `skills_dev.md`, `skills_comms.md` on
  boot. Nothing reads them. (GAMEPLAN R2.4 removes them.)
- `react.handle` accepts `call_claude` and `thinking`. **Both are unused.** The
  orchestrator carefully builds a bound caller with rate-limit handling and
  notification, passes it in, and React ignores it in favor of its own client at
  `_get_client()`. The main chat path therefore relies on the SDK's
  `max_retries=5` rather than `call_claude`'s retry-and-notify logic.
- `_THINKING_BUDGET` is defined in `react.py` and never referenced.
- `config.ANTHROPIC_API_KEY` is read and unused. Deliberate - it is the standing
  hook for GAMEPLAN R4.1.

---

## Known sharp edges

1. **`"digest"` is a bare substring** in `_MONITOR_TRIGGERS` and is checked
   first. Any message containing that word routes to the read-only Monitor, so
   "deep research on digest formats" never reaches the research branch.
2. **No in-turn token budget guard** in `react.handle`. Within one call, every
   tool round appends up to 4000 chars; by internal iteration 7 or 8 the request
   itself can exceed the model's TPM limit and 413. This is the root cause of the
   July 2026 research failures. GAMEPLAN R2.0.
3. **`requirements.txt` omits `openai`** and includes unused `anthropic`. The
   live deploy works because `openai` was installed by hand. A clean install from
   that file produces a bot that fails on first model call.
4. **`react.py` `python_run` description** advertises `anthropic` to the model as
   an available sandbox package.

---

## Deliberately not built

Described in `IGOR_SPEC.md` or `GAMEPLAN.md`, absent from the code:

- `agents/direct.py` does not exist. Chat goes through React, with all 12 tool
  schemas attached, on the saturated 8k bucket. (R2.1)
- The router is keyword matching, not a model call. (R2.2)
- No ConfigEdit agent, so natural-language config requests dead-end. (R2.3)
- Dev, Comms and Prod+Memory were never separate agents.
- **Prompt-injection screening has never existed**, despite being a Spec
  Principle 1 requirement. (R3.3)
- No Flutter UI. Discord is the only interface. (Spec Phase 2)
