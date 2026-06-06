# I.G.O.R. - Claude Code Guide

## Project Overview
Self-hosted personal AI assistant. Discord bot running on Oracle Cloud Ubuntu 22.04 (ARM A1).
Stack: Python, discord.py, Anthropic API, APScheduler, exa-py, python-dotenv.

## Architecture
- `orchestrator.py` - routing classifier + `call_claude()` + `Orchestrator` class
- `agents/` - 6 specialist agents: dev, research, comms, prod_memory, monitor + direct (in orchestrator)
- `interfaces/discord_bot.py` - Discord interface, DMs only
- `main.py` - startup, memory file templates, system config loading
- `config.py` - env vars, loaded from `.env`
- `memory/` - Markdown config and memory files, Syncthing-managed

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
