# IGOR and Hermes Agent, feature by feature

Hermes Agent is the reference for what a personal agent should be able to do. This is where IGOR stands against it, checked against Hermes' documentation and IGOR's source, last on 2026-11-01. The full ledger is the IGOR vs Hermes artifact. The harder limit is budget: IGOR runs on a free tier of 8000 tokens a minute and 200000 a day on its main model, on a 956 MB host, so several features exist here in a smaller form on purpose.

Update this file when a row changes. ARCHITECTURE.md describes how each thing works; this only says whether it exists.

## Built

| Hermes | IGOR | Difference, if any |
|---|---|---|
| `/goal` | `followthrough.py` | IGOR decides when to continue instead of a command. Three rounds of 4 steps, 45000 tokens at most, against Hermes' 20 turns. A failed judge stops; Hermes' continues. The judge reads every reply so far: IGOR's rounds reach it one message each. |
| Tool Search | `load_tools` | One bridge tool, not three: a bridge step costs a whole model call here. |
| `tool_search(queries)` batching | `search(queries)` | The model makes one tool call a step, so eight lookups fit in one call. |
| `web_extract` | `fetch_url` | Page text in windows, or one question answered from the page. |
| DDGS search backend | `research._keyless_search` | A backup for when Exa fails, not a choice of provider. Short undated snippets, and proof checks refuse it. It found nothing on 3 of 11 first tries, so it retries once. |
| `grounded-citations` ledger | `proof.py` | Overnight income ideas only: code checks each quote is on the page, and the ledger outlives the paper's revisions. Reads Exa's excerpts rather than whole pages, for the budget. |
| Gateway `/stop` `/retry` `/undo` `/usage` `/status` `/help` | `commands.py` | Plain words, with or without the slash. |
| `busy_input_mode: queue` | One turn at a time | Queue, not Hermes' default interrupt: two turns at once share one rate limit. `stop` covers the rest. |
| `/steer`, `/btw` | `steer`, `btw` | Plain words. A side question runs on 20b, the bucket the turn is not using, and is kept in the conversation, of which there is one. Neither carries outside text. |
| `terminal(background=true)`, `process_manage`, `notify_on_complete` | `shell(background)`, `process`, `background.py` | The end goes to the user in code, not through a model call. Two at a time, 30 minutes each, at low priority. No writing to a command's input, and a restart ends them, saying so. |
| Terminal files persist between calls | `memory/workspace` | One folder, kept a week up to 200 MB. Scheduled scripts, which have the network, cannot write there. |
| Deliverable mode | `send_file`, `deliver.py` | Word, PDF, Excel and CSV written in code with the standard library, since the host has none of Hermes' libraries. From Markdown, a server file or the last reply, which costs no output tokens. No charts or HTML. |
| `/moa`, Mixture of Agents | `moa.py`, `second opinion` | One-shot only, on 20b and qwen advising 120b: the models this account has. Not a selectable model, and no presets. |
| `redact_secrets` | `sanitize.redact` | |
| Protected paths | `_is_secret_file` | |
| Cron `[SILENT]` | `scheduled_message` | |
| Cron | `scheduled_message`, `compose.write` | Read-only tools. Any interval, but 3 hours at the shortest without a script and 5 minutes with one: a run without one spends tokens. `times` as Hermes' `repeat`. |
| `/loop` stop conditions | `[DONE]`, `times` | Hermes' `LOOP_COMPLETE` and `--times`. No `--until` judge: the brief says when to stop, and the run judges it. |
| Cron `script`, `no_agent`, wake gate | `prerun.py` | Only the user or Claude Code can add a script: the folder is sealed from every tool. In the sandbox with the network, no keys and no private files. 60 seconds, not an hour. A failure alerts once a streak, not every run. |
| Cron `context_from`, `deliver: local` | `context_from`, `keep_only` | The latest output, dated, up to 3 sources. An entry that reads others goes last in a tick, where Hermes does not wait. |
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
| Background memory review, `memory.write_approval` on | `memory_review.py` | Always on approval, never a model's call; Hermes defaults to saving freely. Once a night on 20b rather than after each turn, for the budget. A fact must quote the user's own first-person words, checked in code. |
| Delegation | `delegate_task` | One worker at a time, for the rate limit. |
| `execute_code` | `execute_code` | Read-only tools only, 12 lookups and 60 seconds, no private files. Hermes' scripts can also write and run commands. |
| Voice and image input, document extraction | `agents/inbound.py` | A long document is summarised on arrival; paging one does not fit the budget. PowerPoint, EPUB and OpenDocument with the standard library, where Hermes installs a converter, so no old `.doc` or `.ppt`, RTF or SQLite. A file that unpacks past 100 MB is refused. |
| Syntax checks on `write_file` and `patch` | `_syntax_note` | Python only: IGOR's file tools write nothing but `.py` and `.md`. Kept and reported, and only what the edit broke, as Hermes does for Python. |
| URL safety | `url_safety.py` | |
| MCP server | `mcp_server.py` | Read-only, over SSH. |
| Shell hooks | `hooks.py` | The same JSON on stdin, exit 2 or a block decision, failing open. In the sandbox, where Hermes' run with its keys, and sealed from every tool. Four events, not Hermes' dozens: `startup`, `pre_tool_call`, `post_tool_call`, `agent_end`. What one prints goes to the user. It cannot change a tool's arguments or add to a prompt, and cannot fail closed. None is placed yet. |
| API server, `/v1/chat/completions` | `http_server.py` | Off until a key is set, and on loopback. One conversation, so only the newest user message is taken; no images, tool progress, `/v1/responses` or runs API. The way in for IGOR's own app. |
| Webhooks | `http_server.py` | Off until a route is set, and on loopback: reaching it from outside takes a step on Oracle's firewall, the user's call. GitHub, GitLab and timestamped signatures only; 30 a minute, 1 MB, seen once, as Hermes. Runs are read-only and capped at 24 a day, then delivered plainly. No script filters, coalescing or delivery elsewhere. |
| MCP client, `mcp_servers` | `mcp_client.py` | Read-only tools only, where Hermes can ask before a write. Keys through headers, with no OAuth sign-in, so services that require it cannot connect. No resources, prompts or sampling. One connection a call rather than one held open, for memory. No Node on the host, so no npx servers. Results count as web text. Nothing is configured yet. |

## Different on purpose

- **The user's notes are not in every React prompt.** Hermes puts USER.md in every session. React holds `fetch_url`, so a page could ask for those notes in a URL, and Direct has no tools, so it gets them. This narrows the risk rather than closing it: React can still read `user.md` when a turn needs it, in a turn that also has the web. Closing that would make "use my notes to find X" take two messages.
- **A document in the message closes the tools that change things**, so "save the key points of this" takes a second message. Hermes has no equivalent rule.
- **No mid-task budget warnings.** Hermes removed these after they made models abandon work.
- **Prompt caching does nothing here.** Groq's documentation offers it; measured on this account, identical calls show no cached tokens.
- **No @ references.** Hermes' documentation calls them primarily a CLI feature and says its gateway passes them through unexpanded, so its Discord has none either. Its source hints a gateway path may exist, unresolved. React reads a file or a link with a tool.
- **No image or video generation.** Left out by the user's choice; both would also need a paid provider.

## Parked by the user

- **Spoken replies, buttons, a dashboard.** These wait for IGOR's own app.
- **Clones.** GAMEPLAN Phase K.

## Left out, and why

- **Key rotation.** Groq's limits are by organisation, so a second key on this account adds nothing, and a second account is a question for Groq's terms.

## The host rules these out

- **Browser automation and computer use.** Chromium does not fit in 956 MB.
- **Local models.** There is no GPU.

## Still open, most useful first

1. **Video analysis.** Needs ffmpeg on the host, not installed. Whisper and qwen could then read a clip's sound and frames, though Discord's upload limit leaves little room for a phone's video.
2. **Kanban.** Several agents sharing a board. Little use with one worker at a time.

## Waiting on the user

- **A Cerebras key.** The fallback chain is built. Third-party pages put the free tier near 1M tokens a day, which would be the largest budget increase available.
