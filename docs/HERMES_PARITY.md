# IGOR and Hermes Agent, feature by feature

Hermes Agent is the reference for what a personal agent should be able to do. This is where IGOR stands against it, checked against Hermes' documentation (v0.22.5) and IGOR's source on 2026-10-30. The full ledger is the IGOR vs Hermes artifact. The harder limit is budget: IGOR runs on a free tier of 8000 tokens a minute and 200000 a day on its main model, on a 956 MB host, so several features exist here in a smaller form on purpose.

Update this file when a row changes. ARCHITECTURE.md describes how each thing works; this only says whether it exists.

## Built

| Hermes | IGOR | Difference, if any |
|---|---|---|
| `/goal` | `followthrough.py` | IGOR decides when to continue instead of a command. Three rounds of 4 steps, 45000 tokens at most, against Hermes' 20 turns. A failed judge stops; Hermes' continues. |
| Tool Search | `load_tools` | One bridge tool, not three: a bridge step costs a whole model call here. |
| `tool_search(queries)` batching | `search(queries)` | The model makes one tool call a step, so eight lookups fit in one call. |
| `web_extract` | `fetch_url` | Page text in windows, or one question answered from the page. |
| Gateway `/stop` `/retry` `/undo` `/usage` `/status` `/help` | `commands.py` | Plain words, with or without the slash. |
| `busy_input_mode: queue` | One turn at a time | Queue, not Hermes' default interrupt: two turns at once share one rate limit. `stop` covers the rest. |
| `redact_secrets` | `sanitize.redact` | |
| Protected paths | `_is_secret_file` | |
| Cron `[SILENT]` | `scheduled_message` | |
| Cron | `scheduled_message`, `compose.write` | Read-only tools. No pre-run scripts or chained jobs. |
| `/heartbeat` | `heartbeats.py` | Code compares pages and only a change reaches a model; a full turn per tick would spend the day. |
| `memory` tool, add, patch, remove, batched | `memory_entries.py` | |
| USER.md, MEMORY.md | `user.md`, `preferences.md` | `user.md` reaches Direct and job searches, not every React turn: see below. |
| `session_search` | `search_history`, `around` | LIKE over FTS5, at this size. |
| Context compression | `compression.py` | Folds on eviction, since there is one endless conversation. |
| Fallback providers | `llm.create` | Cerebras is wired in and waits for a key. |
| Approvals | `approvals.py` | No "smart" mode: a model call per command. |
| Sandboxed commands | `sandbox.py` | Bubblewrap, not Docker: the host has about 500 MB free. |
| Checkpoints, `/rollback` | `checkpoints.py` | Files only; the conversation is not rewound. |
| File-mutation verifier | Receipts under each reply | |
| Skills, `skills.write_approval` on, curator | `skills.py` | Always on approval: `approve p7`, never a model's call. Hermes defaults to writing freely. Drafted from a night's review or on request, not by a nudge after each hard task. No supporting files or hub, and no consolidation pass, which Hermes also leaves off. Read with `read_file`, so no extra tool schema. |
| Delegation | `delegate_task` | One worker at a time, for the rate limit. |
| `execute_code` | `execute_code` | Read-only tools only, 12 lookups and 60 seconds, no private files. Hermes' scripts can also write and run commands. |
| Voice and image input, document extraction | `agents/inbound.py` | A long document is summarised on arrival; paging one does not fit the budget. |
| URL safety | `url_safety.py` | |
| MCP server | `mcp_server.py` | Read-only, over SSH. |

## Different on purpose

- **The user's notes are not in every React prompt.** Hermes puts USER.md in every session. React holds `fetch_url`, so a page could ask for those notes in a URL, and Direct has no tools, so it gets them. This narrows the risk rather than closing it: React can still read `user.md` when a turn needs it, in a turn that also has the web. Closing that would make "use my notes to find X" take two messages.
- **A document in the message closes the tools that change things**, so "save the key points of this" takes a second message. Hermes has no equivalent rule.
- **No mid-task budget warnings.** Hermes removed these after they made models abandon work.
- **Prompt caching does nothing here.** Groq's documentation offers it; measured on this account, identical calls show no cached tokens.
- **No image or video generation.** Left out by the user's choice; both would also need a paid provider.

## Parked by the user

- **Spoken replies, buttons, a dashboard.** These wait for IGOR's own app.
- **Clones.** GAMEPLAN Phase K.

## The host rules these out

- **Browser automation and computer use.** Chromium does not fit in 956 MB.
- **Local models.** There is no GPU.

## Still open, most useful first

1. **A second search backend, waiting on one install.** The DDGS fallback is written and tested offline but has never run against the real service, because the package is not on the server. Until it is, search stops if Exa's credits run out, and says so.
2. **Background memory review.** Hermes reviews each conversation and saves what matters. IGOR saves only when asked. A wrong fact about the user is costly, so this would need the user's approval, the way skills do.
3. **MCP client.** IGOR serves MCP but cannot use other servers.
4. **Chained scheduled jobs and pre-run scripts.**
5. **Kanban.** Several agents sharing a board. Little use with one worker at a time.

## Waiting on the user

- **Installing `ddgs` on the server.** See the list above: DDGS answers when Exa cannot.
- **A Cerebras key.** The fallback chain is built. Third-party pages put the free tier near 1M tokens a day, which would be the largest budget increase available.
