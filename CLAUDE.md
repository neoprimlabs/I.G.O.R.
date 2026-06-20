# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview
Self-hosted personal AI assistant. Discord bot running on Oracle Cloud Ubuntu 22.04 (ARM A1).
Stack: Python, discord.py, Anthropic API, APScheduler, exa-py, python-dotenv.

## Architecture
- `orchestrator.py` - routing classifier + `call_claude()` + `Orchestrator` class
- `agents/react.py` - primary agent: ReAct loop with tool use, extended thinking, skill capture
- `agents/research_loop.py` - deep research loop (iterative react.handle() calls, writes to research.md)
- `agents/monitor.py` - scheduled digest + watchlist monitoring via APScheduler
- `agents/prod_memory.py` - memory file writes via `%%WRITE%%` blocks
- `agents/research.py` - single-shot research agent
- `interfaces/discord_bot.py` - Discord interface, DMs only
- `context_store.py` - SQLite persistence for conversation context across restarts
- `watchdog.py` - Layer 2 restart watchdog, polls for sentinel file every 5s
- `start.sh` - startup wrapper: crash recovery + syntax check before launching main.py
- `main.py` - startup, memory file templates, system config loading
- `config.py` - env vars, loaded from `.env`
- `memory/` - Markdown config and memory files, Syncthing-managed

## Safety Stack (3 layers)
- **Layer 1 (start.sh)**: Runs `python3 -m compileall -q -x venv .` before launch. On failure, reverts with `git checkout -- .` and retries. If IGOR exits non-zero, writes `.crash_detected` marker.
- **Layer 2 (watchdog.py / igor-watchdog.service)**: Independent systemd service. IGOR writes `/opt/igor/restart_requested` with a reason string; watchdog picks it up within 5s and runs `sudo systemctl restart igor`. 300s cooldown between restarts.
- **Layer 3 (start.sh crash recovery)**: On next startup, if `.crash_detected` exists, runs `git checkout -- .` to restore last known good code before launching.

## React Tools
Tools available in `react.py`'s `_TOOLS` list:
- `search`, `fetch` - Exa web search and URL fetch
- `shell` - Execute shell commands on the server
- `read_file`, `write_file` - File I/O on server (`.py` and `.md` only, path-restricted to IGOR root)
- `patch_file` - Targeted string replacement in a file (safer than write_file for small changes; fails if old_string not found or not unique)
- `python_run` - Execute Python in-process
- `memory_read`, `memory_write` - Read/write memory files
- `search_memory` - Keyword search across all memory `.md` files
- `send_message` - Send a proactive Discord DM to the user
- `restart_self` - Write sentinel file for watchdog to pick up

## Deployment Workflow
Local code lives at `c:\Dev\IGOR`. Server is the only test environment - there is no local run.

1. Make changes locally
2. Syntax check: `python -m py_compile <files>`
3. Commit and push from **local PowerShell** (not SSH terminal)
4. SSH to server and pull/restart

**PowerShell note:** `&&` is not valid. Run commands one at a time.

**SSH:**
```
ssh -i C:/Users/Nucbox/Documents/IGOR_Keys/ssh-key-2026-05-26.key ubuntu@129.213.46.96
```

**Server commands (in SSH terminal):**
```
sudo -u igor git -C /opt/igor pull
sudo systemctl restart igor
```

**Install a package on server:**
```
sudo /opt/igor/venv/bin/pip install <package>
```

**Git ownership fix (if needed):**
```
sudo chown -R igor:igor /opt/igor
```

## Always Specify the Terminal
Before every command, state whether it runs in local PowerShell or SSH terminal. Never assume which terminal the user has open.

## Agent Prompt Pattern
Every agent uses `_DEFAULT_SYSTEM_PROMPT` (or `_DEFAULT_*` for multiple prompts) with a file-based override:

```python
def _get_system_prompt() -> str:
    path = config.MEMORY_DIR / "prompt_<agent>.md"
    if path.exists():
        content = path.read_text(encoding="utf-8").strip()
        if content:
            return content
    return _DEFAULT_SYSTEM_PROMPT
```

- File override takes effect immediately (no restart)
- Reset to default: delete the file or overwrite with empty content
- Prompt files: prompt_dev.md, prompt_research.md, prompt_comms.md, prompt_prodmem.md, prompt_monitor.md, prompt_direct.md

## Style Rules (All Agents)
Every `_DEFAULT_SYSTEM_PROMPT` must include:
```
Style:
- No emojis
- No em dashes - use plain hyphens
- No exclamation points
- No casual filler phrases ("Sure!", "Of course!", "Happy to help!")
```

These rules also apply to all content written to memory files and config files.

## Config Files (memory/)
Read at startup, require restart to take effect:
- `system_config.md` - model name and context window
- `schedule_config.md` - scheduled job times

Read per-call, take effect immediately:
- `digest_config.md` - morning digest sections (valid: tasks, projects, ai_news)
- `watchlist.md` - Monitor watchlist items

## ProdMem Write Format
ProdMem writes to memory files via `%%WRITE%%` blocks in its response:
```
%%WRITE%%
file: <filename>
mode: overwrite   # omit for append
content:
<content>
%%END%%
```
- Append: default, for adding new content
- Overwrite: for editing, removing, or replacing entire file
- Memory files (tasks, projects, user, agents) default to append; use overwrite only when user explicitly edits

**Never ask IGOR to update prompt files via Discord if the new content contains `%%END%%` markers.** The write regex matches the first `%%END%%` it finds, truncating the file mid-content and corrupting the prompt. Prompt files must be edited through Claude Code sessions only.

**If ProdMem writes start failing** (%%WRITE%% block visible in Discord response): restart IGOR to clear context contamination, then test again before debugging further.

## Prompt Caching
All API calls in `call_claude()` (orchestrator.py) use `cache_control` on system prompts:
```python
system_param = [{"type": "text", "text": system, "cache_control": {"type": "ephemeral"}}]
```
Do not bypass this when adding new Claude calls - follow the same pattern.

## Code Conventions
- No comments unless the WHY is non-obvious
- No premature abstractions - three similar lines is fine
- Validate only at system boundaries
- Private functions use `_underscore` prefix
- Lazy imports inside functions for cross-agent calls (avoids circular imports)

## Do Not Assume
- Do not assume which terminal the user has open
- Do not assume code has been deployed
- Do not assume a previous command succeeded
- Ask before proceeding when state is uncertain
