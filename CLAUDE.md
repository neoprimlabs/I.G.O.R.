# CLAUDE.md

Guidance for Claude Code (claude.ai/code) when working in this repository.

This file holds **how to behave**. It deliberately contains no architecture
facts, because facts drift and this file should not have to change when the code
does.

## Start Here - read these first, in this order

1. **`ARCHITECTURE.md`** - what the system actually is today. Verified against
   source. If it disagrees with the code, the code wins and the file is wrong:
   fix it in the same commit.
2. **`STATE.md`** - where things stand right now. Host, service status, repo
   positions, next action, known-broken list. **Rewrite it, never append.**
3. **`GAMEPLAN.md`** - the active work queue. If the user asks "what's next" or
   you are continuing restoration work, execute the next unchecked step there,
   literally.
4. **`IGOR_SPEC.md`** - the authoritative vision. Parts of it have never been
   built. Never deviate from it without flagging.

**Update `STATE.md` at the end of any session that changes the running system.**
A state file that goes stale is worse than no state file.

The runtime is the server. There is no local run.

## Groq Platform Rules (hard-won; violating these cost days)
- **Rate limits are PER MODEL, independent buckets, and the limit VARIES by model**
  (verified 2026-07-09). Requests/day is a non-issue. Think in tokens-per-minute.
  Measured: llama-3.3-70b-versatile=12000, gpt-oss-120b=8000, gpt-oss-20b=8000,
  llama-3.1-8b-instant=6000. Different models = separate buckets (that is why agents
  are assigned different models). Same model for two roles = SHARED bucket. The old
  "8000 everywhere" assumption was wrong - do not rely on it.
- **max_tokens counts against TPM at request time** (prompt + max_tokens = "requested").
  Never configure a call where prompt + max_tokens can exceed the model's limit.
  File mode: 3072. Chat: 2048. Small synthesis calls: >= 1024, never less.
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

## Deployment Workflow (Claude Code runs all of this directly via the Bash tool)
Local repo: `c:\Dev\IGOR`. Server: `/opt/igor`, service `igor`, user `igor`.
Current host IP is in `STATE.md`.

1. Edit locally, then `python -m py_compile <changed files>` - never skip.
2. **If the change alters what ARCHITECTURE.md describes, update it in the same
   commit.** Not afterwards. This file went 24 commits stale once while R2 and R3
   rewrote most of what it documented, and IGOR reads it to answer questions about
   itself - so it told the user it used keyword routing days after the model router
   shipped. A stale ARCHITECTURE.md is worse than none, because IGOR quotes it.
3. Commit and push (heredoc for multi-line messages).
4. Deploy and verify in one shot:
```
ssh -i C:/Users/Nucbox/Documents/IGOR_Keys/ssh-key-2026-05-26.key -o BatchMode=yes ubuntu@129.80.181.77 "sudo -u igor git -C /opt/igor pull && sudo systemctl restart igor && sleep 6 && sudo systemctl is-active igor"
```
5. Expected: `active`. On anything else: `sudo journalctl -u igor -n 30 --no-pager`.
6. Ask the user for a Discord test after changes to routing, react.py, or the bot.
7. After restarting `igor`, confirm `igor-watchdog` is also active. It was enabled
   but never started after the 2026-08 migration, leaving safety Layer 2 down.

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
6. **IGOR silent for a long stretch** -> check the host exists before debugging code.
   The whole instance was terminated once. `scripts/backup_memory.ps1` alerts on this.
7. **A scheduled job silently does nothing** -> APScheduler failures surface only in
   ERROR lines in journalctl. The httpx request logs will not show a failed job, so
   an absence of request logs is the symptom, not the evidence.
8. **`NameError` on a stdlib module** (asyncio, re, json) in an agent -> it is not
   transitively available from other imports. Import it explicitly at the top.
   `py_compile` will NOT catch this: it checks syntax, not names, so the commit
   passes and the module fails when something first routes to it. The startup check
   in main.py imports every agent module for exactly this reason.

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

**Test the failure path, not just the happy path.** Two bugs in the backup and
alerting script were invisible on read and only appeared when the failure case was
deliberately triggered - including one that killed the script before it could alert,
on the exact scenario it existed for.

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
- Do not assume a documented fact is current - ARCHITECTURE.md and STATE.md are
  maintained by hand and can lag the code
- Ask before proceeding when state is uncertain
