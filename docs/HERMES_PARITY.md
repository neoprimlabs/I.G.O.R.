# IGOR and Hermes Agent, feature by feature

Hermes Agent is the reference for what a personal agent should be able to do. This is where IGOR stands against it, checked against Hermes' documentation, its tool source and IGOR's source, last on 2026-11-08 through v0.23.0. The full ledger is the IGOR vs Hermes artifact. The harder limit is budget: IGOR runs on a free tier of 8000 tokens a minute and 200000 a day on its main model, on a 956 MB host, so several features exist here in a smaller form on purpose.

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
| Fallback providers | `llm.create` | Cerebras is wired in; the user declined it, since it asks for payment details. |
| Approvals | `approvals.py` | No "smart" mode: a model call per command. A no can say what to do instead, as in Hermes 0.23, and invisible characters ask first. Its pipe-to-shell, cloud-metadata and credential-upload checks have nothing to catch: the shell has no network, and a download piped into a shell is refused outright. |
| Sandboxed commands | `sandbox.py` | Bubblewrap, not Docker: the host has about 500 MB free. |
| Checkpoints, `/rollback` | `checkpoints.py` | Files only; the conversation is not rewound. |
| File-mutation verifier | Receipts under each reply | |
| Stale-overwrite guard | `write_file` | |
| Tool result storage | `result:N` | Kept in memory for the turn, not on disk, so web text cannot outlive its quarantine. |
| `clarify` | `clarify` | Typed numbers, no buttons. One question at a time, 10 minutes. |
| Skills, `skills.write_approval` on, curator | `skills.py` | Always on approval: `approve p7`, never a model's call. Hermes defaults to writing freely. Drafted from a night's review or on request, not by a nudge after each hard task. No supporting files or hub, and no consolidation pass, which Hermes also leaves off. |
| Background memory review, saving freely, "Memory updated" | `memory_review.py` | Saves without asking, as Hermes' default, and names each save under the next reply. A change to a saved line waits for approval, as Hermes stages a background patch. So does a fact for a full file, staged with the lines 120b chose to drop, as Hermes stages a consolidation: one yes applies both, and the listing shows each line in full. On 20b after 15 quiet minutes and nightly, for the budget. Stricter: a fact must quote the user's own first-person words, checked in code. Unanswered proposals expire after a week, where Hermes' wait forever. |
| Delegation | `delegate_task` | One worker at a time, for the rate limit. |
| `execute_code` | `execute_code` | Read-only tools only, 12 lookups and 60 seconds, no private files. Hermes' scripts can also write and run commands. |
| `video_analyze` | `agents/inbound.py` | No model on this key can watch a video, so ffmpeg gives Whisper the sound and the vision model 3 still frames, in the sandbox: a clip's motion is not seen. A file sent in chat only, not a link. One a message, up to 50 MB. |
| Web dashboard | `dashboard.py` | Health, tokens by the hour with headroom, schedules, the board, plugins, errors. Loopback with no login and a Host check, as Hermes'; reached through an SSH tunnel. Chat as Hermes' Chat tab, behind a key as Hermes' is behind a session token: the one conversation, Discord's included, polled rather than a terminal over a WebSocket, so no tool-call cards or live text. No config, keys, skills or logs pages: those files stay out of a page's reach. |
| Browser, Lightpanda engine | `browser.py`, `browser` | Lightpanda only: Chromium does not fit. Steps in one call instead of model-written Python, so nothing the model writes runs on the host. Reads, clicks, types and searches, and refuses password fields and anything but GET, so no logins, purchases or posts; Hermes' vault and real-profile logins are left out with them. Private networks blocked by the engine. No screenshots, one page. |
| Kanban | `board.py`, `board` | One board and one worker, on 20b, 8 runs a day, with read-only tools: no profiles, tenants, review column, parallel fan-out or workspaces. Parents, comments, restart recovery and blocking on two failures are all there. A worker ends with its reply or `[BLOCKED: why]`, not a tool call, for the budget. |
| Plugins, `register(ctx)` | `plugins.py`, `plugin_runner.py` | The same folder format, off until enabled. `register_tool`, `register_command` and `register_hook` work; `plugins` names what else one asked for. Each call is a sandboxed process rather than in the agent with its keys, so a plugin that imports Hermes' own modules, or needs its `ctx.llm`, does not load or run here. No installer or catalog: Claude Code or the user places it. None is placed yet. |
| Voice and image input, document extraction | `agents/inbound.py` | Hermes' no-speech phrases are silence, so a quiet clip is not heard as "Thank you."; a voice note keeps a spoken "okay" when its sound is shaped like speech, where Hermes drops it. A long document is summarised on arrival; paging one does not fit the budget. PowerPoint, EPUB and OpenDocument with the standard library, where Hermes installs a converter, so no old `.doc` or `.ppt`, RTF or SQLite. |
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
- **No tool-argument repair.** Hermes fixes a model's "42" or "true" against each tool's schema. Groq checks arguments before IGOR sees them, so there is nothing to repair: 2 such refusals in IGOR's whole journal.
- **Prompt caching does nothing here.** Groq's documentation offers it; measured on this account, identical calls show no cached tokens.
- **No `todo_list`.** Hermes' plan tool costs a model call per update here, since 120b makes one tool call a step, for work that is rare (5 turns out of steps in two weeks). A turn out of steps says what is still open, and a follow-through round ends by saying what is left, which serves the same purpose for nothing.
- **No @ references.** Hermes' documentation calls them primarily a CLI feature and says its gateway passes them through unexpanded, so its Discord has none either. Its source hints a gateway path may exist, unresolved. React reads a file or a link with a tool.
- **Hermes 0.23's cron stop and catch-up setting.** A run takes seconds, and a missed message already goes out late with a note. Of 17 since August, none failed.
- **No image or video generation.** Left out by the user's choice; both would also need a paid provider.

## Parked by the user

- **Buttons.** Not wanted, in Discord or in the app (the user, 2026-11-03). Spoken replies are built on the dashboard.
- **Clones.** GAMEPLAN Phase K.

## Left out, and why

- **Key rotation.** Groq's limits are by organisation, so a second key on this account adds nothing, and a second account is a question for Groq's terms.

## The host rules these out

- **Computer use, and a browser with screenshots.** Chromium does not fit in 956 MB.
- **Local models.** There is no GPU.

## Waiting on the user

- **Accounts.** X search (xAI), Google Workspace and Spotify (each needs a sign-in flow the MCP client does not have yet), memory providers, secrets managers.
- **IGOR's own app.** Started 2026-11-02: the dashboard, its chat and voice (`voice.py`). Orpheus waits for the account owner to accept its terms in the Groq console. Buttons are not wanted (2026-11-03).
- **A larger host.** Oracle's free Arm allowance is now 2 OCPU and 12 GB (halved in 2026), twelve times this host's memory; it would fit Chromium and screenshots. Capacity is often exhausted, and moving IGOR is the largest job on this list.
