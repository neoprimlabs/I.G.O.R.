# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Start Here
- `IGOR_SPEC.md` is the authoritative vision. Never deviate from it without flagging.
- `GAMEPLAN.md` is the active work queue. If the user asks "what's next" or you are
  continuing restoration work, execute the next unchecked step there - literally.
- The server is the only runtime. There is no local run (discord.py is not even
  installed locally - test pure logic with standalone python snippets instead).

## Project Overview
Self-hosted personal AI assistant. Discord bot running on Oracle Cloud Ubuntu 22.04 (ARM A1).
Stack: Python, discord.py, **Groq API via the openai SDK** (free tier), APScheduler, exa-py, python-dotenv.
Formerly Anthropic-powered; migrated 2026-06-22 for cost. Any `anthropic` references you find are stale - flag them.

## Groq Platform Rules (hard-won; violating these cost days)
- **Rate limits are per MODEL, per org: ~8000 tokens per MINUTE each.** Requests/day
  is a non-issue. Think in tokens-per-minute. Different models = separate buckets,
  which is why agents are assigned different models (see GAMEPLAN.md target architecture).
- **max_tokens counts against TPM at request time** (prompt + max_tokens = "requested").
  Never configure a call where prompt + max_tokens can exceed 8000. File mode: 3072.
  Chat: 2048. Small synthesis calls: >= 1024, never less (see next rule).
- **gpt-oss and qwen models are reasoning models: max_tokens covers hidden reasoning
  plus output.** Caps under ~1024 silently produce EMPTY or clipped content with a
  200 OK. Treat "empty content, no error" as a reasoning-budget symptom first.
- **finish_reason "length"** means the budget ran out (often all reasoning). react.py
  handles it (return partial or retry once with doubled budget). Follow that pattern
  in new call sites.
- **Llama models garble tool-call syntax** under load -> Groq returns 400
  `tool_use_failed`. react.py retries 3x and feeds the rejection reason back into
  the conversation. Do not remove that machinery.
- 429 storms with 20-50s backoffs are the free tier working as designed, not a bug.
  The openai client is constructed with max_retries=5 everywhere - keep it.

## Architecture
- `orchestrator.py` - routing (fast paths + classifier; model router lands in GAMEPLAN R2.2),
  `call_claude()` helper, `Orchestrator` class, critic (disabled: `config.ENABLE_CRITIC = False`)
- `agents/react.py` - Task agent: ReAct tool loop on gpt-oss-120b. Guards: duplicate-call
  dedupe, tool_use_failed retries, JSON-parse guard, 4000-char tool result cap,
  max 8 iterations, forced tool-free final answer on exhaustion
- `agents/direct.py` - Chat agent, no tools, warm prose (GAMEPLAN R2.1; may not exist yet)
- `agents/evaluator.py` - independent PASS/FAIL contract check on file-mode outputs;
  one retry with feedback; fails open
- `agents/research_loop.py` - deep research loop; archives research.md with a timestamp
  before each new run
- `agents/monitor.py` - scheduled digest + watchlist via APScheduler; read-only by design
- `agents/prod_memory.py` - memory-file write helper (`_write_to_memory` with allowlists);
  becomes the ConfigEdit agent in GAMEPLAN R2.3
- `agents/research.py` - Exa search helpers (`_run_search`, `_format_results`), not an agent
- `interfaces/discord_bot.py` - Discord DMs only; outbound punctuation sanitizer
  (`_PUNCT_MAP` - keep new typographic chars mapped to ASCII); chunker hard-splits
  lines > 2000 chars
- `context_store.py` - SQLite rolling context (window in config.py, currently 6)
- `watchdog.py` + `start.sh` + crash markers - 3-layer safety stack (below)
- `config.py` - env vars and settings. **Single source of truth for models and window.**
- `memory/` - markdown config and memory files on the server, Syncthing-managed

## Safety Stack (3 layers)
- **Layer 1 (start.sh)**: `python3 -m compileall` before launch; on failure `git checkout -- .` and retry.
- **Layer 2 (watchdog.py / igor-watchdog.service)**: independent systemd service; IGOR writes
  `/opt/igor/restart_requested`, watchdog restarts the service within 5s; 300s cooldown.
- **Layer 3 (start.sh crash recovery)**: `.crash_detected` marker on non-zero exit ->
  next boot restores last known good code.
- `restart_self` notifies the user on Discord with the reason and last commit before restarting.

## Deployment Workflow (Claude Code runs all of this directly via the Bash tool)
Local repo: `c:\Dev\IGOR`. Server: `/opt/igor`, service `igor`, user `igor`.

1. Edit locally, then `python -m py_compile <changed files>` - never skip.
2. Commit and push (heredoc for multi-line messages).
3. Deploy and verify in one shot:
```
ssh -i C:/Users/Nucbox/Documents/IGOR_Keys/ssh-key-2026-05-26.key -o BatchMode=yes ubuntu@129.213.46.96 "sudo -u igor git -C /opt/igor pull && sudo systemctl restart igor && sleep 6 && sudo systemctl is-active igor"
```
4. Expected: `active`. On anything else: `sudo journalctl -u igor -n 30 --no-pager`.
5. Ask the user for a Discord smoke test after changes to routing, react.py, or the bot.

Server memory files are edited with `sudo -u igor` (tee/sed), never as root, never via git.
IGOR sometimes commits on the server itself; if pull reports divergence, prefer
`sudo -u igor git -C /opt/igor reset --hard origin/master` AFTER confirming the remote
contains everything needed. When giving the user commands to run themselves, always say
which terminal (local PowerShell vs SSH); PowerShell has no `&&`.

## Debugging Playbook (check in this order)
1. **Wrong/weird behavior despite prompt fixes** -> `cat /opt/igor/memory/skills_react.md`
   on the server. Learned skills inject into every prompt and override prompt edits.
   A poisoned skill caused days of confusion once already.
2. **Slow or "frozen"** -> it is almost always 429 TPM backoff. Check journalctl for
   429 lines; check true limits with the x-ratelimit-* headers via a curl to
   /openai/v1/chat/completions.
3. **Empty sections / clipped output, no errors** -> reasoning budget. Raise max_tokens.
4. **`Something went wrong (X)` in Discord** -> journalctl has the full traceback.
5. **Model behaves like config was ignored** -> confirm the running process is on the
   commit you think (`sudo -u igor git -C /opt/igor log -1 --oneline`) and was restarted
   after the change. Config/env is read at startup only (memory prompt files: per call).

## Agent Prompt Pattern
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

- File override takes effect immediately (no restart). Reset: delete or empty the file.
- Prompt files in use: prompt_react.md, prompt_monitor.md, prompt_prodmem.md,
  prompt_direct.md, prompt_evaluator.md
- Prompt files are edited through Claude Code sessions only - never via Discord
  (ProdMem's legacy `%%WRITE%%` regex truncates at the first `%%END%%`).

## Style Rules (All Agents)
Every `_DEFAULT_SYSTEM_PROMPT` must include:
```
Style:
- No emojis
- No em dashes - use plain hyphens
- No exclamation points
- No casual filler phrases ("Sure!", "Of course!", "Happy to help!")
```
These rules also apply to all content written to memory files, config files, and
IGOR source. The Discord layer additionally sanitizes typographic punctuation to
ASCII on the way out (`_PUNCT_MAP` in discord_bot.py) - extend the map when a new
character class appears as mojibake in the user's viewer.

## Config Files (memory/ on the server)
Read at startup (restart required):
- `schedule_config.md` - scheduled job times
Read per call (immediate effect):
- `digest_config.md` - digest sections (valid: tasks, projects, daily_forecast, ai_news, unreal_news)
- `watchlist.md` - Monitor watchlist items
- `prompt_*.md` - agent prompt overrides
- `skills_react.md` - learned skills injected into React's prompt
Removed: `system_config.md` once silently overrode MODEL and CONTEXT_WINDOW at startup
and caused two multi-hour debugging sessions. GAMEPLAN R1.1 deletes the mechanism; if
it still exists when you read this, treat it as a landmine: any model/window change
must be mirrored there or it will silently revert.

## Implementation Discipline
Before building anything:
1. Read the relevant existing code - understand what's already there
2. Write out the plan: what changes, what files, what order
3. Identify failure modes: runtime, server, money, data - and for every model call:
   what happens at 429, at empty content, at garbled tool JSON
4. Only then write code

Do not ship until edge cases are handled. Moving fast and patching later has cost
the user money and lost work. If something is discussed and agreed on, it gets
built - not noted and forgotten.

## Code Conventions
- No comments unless the WHY is non-obvious
- No premature abstractions - three similar lines is fine
- Validate only at system boundaries
- Private functions use `_underscore` prefix
- Lazy imports inside functions for cross-agent calls (avoids circular imports)

## Do Not Assume
- Do not assume code has been deployed - verify the server commit
- Do not assume a previous command succeeded - check output
- Do not assume the model saw your prompt change - skills and prompt overrides win
- Ask before proceeding when state is uncertain
