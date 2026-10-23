# STATE.md - Where Things Stand Right Now

**Rewrite this file. Never append to it.** History belongs in git log and
GAMEPLAN's Progress Log. This file answers one question: what is true today?

Last updated: **2026-10-22**

---

## Running

- **Host:** address in `CLAUDE.local.md` (gitignored) - Oracle `VM.Standard.E2.1.Micro`, x86_64,
  US-ASHBURN-AD-1, Always Free, 1 OCPU / 956 MB RAM + 2 GB swapfile
- **OS:** Ubuntu 22.04.5, Python 3.10.12, service user `igor`, root `/opt/igor`
- **Services:** `igor`, enabled and active. `igor-watchdog` was removed 2026-10-21:
  it ran as `igor`, which has no sudo rights, so it could never restart anything.
- **Status:** online, confirmed responding on Discord

## Code

- Server `/opt/igor` runs master as deployed through the S.1 gate. Check with
  `git -C /opt/igor log -1`. Doc-only commits need no deploy, except ARCHITECTURE.md,
  which SelfDescribe reads at runtime.
- The server tree was copied off the rescued disk rather than cloned, but its
  `.git` tracks the same remote and pulls normally.

## Next action

**Showcase for AI agent engineer applications (started 2026-10-21).** The user is
applying to AI agent engineer roles and wants IGOR shown off. Agreed story: "an
always-on personal agent, run in production on free tiers, with the safety
engineering most agents skip." Done: `README.md` (first public README; numbers from
the record only); the public repo cleanup (2026-10-21: host details to gitignored
local files, personal history out of STATE/GAMEPLAN, watchdog and start.sh deleted);
the I.G.O.R. entry in `memory/resume.md` brought up to date (backup
`resume.md.bak-20261021`). Still in git history: the old host address and personal
notes; rewriting public history is the user's decision. Done 2026-10-21 evening:
README sections on memory and shared context and on cost and logging;
`scripts/usage_report.py` (per-agent calls, tokens and, from 10-22, call time, read
from the journal; run with sudo on the server); call timing in `llm.create`; router
eval rerun, 27/28; the overnight paper write moved to 120b. Later that night: the
deploy gate runs the whole offline suite as Gate 3 (49 files, ~150s; failure path
checked with an untracked failing test, which blocked the dry run without a
restart); chat's window shows the last 6 conversation messages plus 3 posts, so posts
cannot push out the user's words; the user's own schedule routes CHAT and Monitor
hands non-status questions to Direct; Direct no longer reports user.md notes as what
the user said. Router 35/35 (after a rule for remember requests), SelfDescribe 11/11 after a scorer fix. Memory is
edited one entry at a time (add, patch, remove), as Hermes does. Fixed 2026-10-22
from that day's Discord output: the check-in assumed a shift on the user's stated
day off (presence moved from 20b to 120b, which named the day off when the same facts
were replayed); the weekday idea treated the previous idea as built (each earlier
item is marked sent, never built, the self-summary is read last, a 200-word cap;
the cause was the 10-22 message itself, stored in the series saying "the model you
already use for the auto-tagging step", so that clause was cut from
`scheduled.json`, backup `scheduled.json.bak-20261022`; replayed clean, 145 words);
the job watch sent three
postings under "no postings worth applying to" (a ranking that picks none now sends
nothing). Built 2026-10-22 evening: `mcp_server.py`, six read-only tools over SSH on
stdio for the user's other agents (Hermes is only an MCP client). `mcp==2.3.0` was
installed by hand as igor, since the deploy script never runs pip; a fresh venv needs
it before the gate's import check. Verified end to end from the PC: ready in 3.2s,
resume.md and ../.env refused. The connect command is in `CLAUDE.local.md`. The
Helfen application went in on 2026-10-22. Fixed 10-23 from that day's output: the job
watch sends only the ranking's picks, counting the rest; an IGOR idea series gets its
subject from code by date, because telling the model to change subject failed on a
replay (it proposed a notes archiver). Also 10-23: "remember that..." and a
correction right after it go to React with memory_write required, the saved entry's
exact text handed to the correction; an end-to-end run on a copy of memory saved
one line and patched it in two calls. Prompts list the next seven dates, after Direct
and React both put Oct 28 on a Monday. Closed the same evening: any start that was
not a deploy (crash, heartbeat restart, reboot) ran whatever IGOR had written to disk
with no gate. `prestart.sh` now runs before every start (systemd drop-in, root) and
lets through only the tree deploy.sh approved; IGOR's file tools refuse `venv/` and
`.git/`. Canary runs passed, including a `sitecustomize.py` set aside before it could
run and a locked git that made it refuse to start. Next: the walkthrough with the user, a demo
script run until it holds, and no new features before an interview. Later: a recorded
demo, an MCP server, and the README cost table refreshed once times accumulate.
First target: an AI Agent Systems Engineer role (e-commerce operations) that asks
for production multi-agent work, memory and shared context, and safeguards. Research behind
the list: reviewers favour one deep project with evals, safety work, measured cost.

**Hermes parity run, 2026-10-19 (the user away, Claude Code working through the
docket; order: attachments, always-on memory, a backup provider, asking before risky
commands; clones parked by the user, design in `docs/superpowers/specs/`):**
- **Attachments - built.** `agents/inbound.py`, Hermes' pattern for text-only
  models: voice notes via Whisper (`whisper-large-v3-turbo`), images described by
  `qwen/qwen3.8-27b` (3 per message, by Discord URL, inline fallback), PDFs (pypdf
  6.1.3, installed by hand in the venv), `.docx` and text saved to `memory/inbox/`.
  Live on the server: vision described a real image; Whisper transcribed a public
  `.ogg` ("Hello."); Groq lists `opus` as accepted. **Discord tests for the user:**
  a voice note, a screenshot, a PDF. Spoken replies (Orpheus) and Discord buttons are
  not wanted in Discord: the user wants them in IGOR's own app (I.1), 2026-10-20.
- **Always-on memory - built.** Hermes puts a capped USER.md (1375 chars) into every
  session and refuses a memory write past the cap. IGOR's `preferences.md` already
  plays MEMORY.md; the gap was Direct (50 of 69 routed messages since 10-01), which
  saw none of `user.md`. Its prompt now ends with `user.md`; React's `memory_write`
  refuses a `user.md` write past `config.USER_PROFILE_CAP` (1500) and says how much
  to free. Live `user.md` corrected (the server is an x86 micro, not ARM A1) and
  tidied to 1488 chars (backup `user.md.bak-20261019`).
- **Backup provider - built, dormant until the user adds a key.** Hermes'
  `fallback_providers`: a chain tried in order, failing over on 429s and 5xx after
  retries, a missing model or refused key at once, a dropped connection; a 400 on
  the primary is still raised. `config.FALLBACK_MODELS` 120b -> Cerebras
  `gpt-oss-120b` (same model, free tier ~1M tokens/day at 5 requests/min per
  third-party pages; signup may want a card) -> Groq 20b. Cerebras is skipped until
  `CEREBRAS_API_KEY` is in `/opt/igor/.env`. **For the user:** sign up at Cerebras,
  create an API key, then Claude Code adds it and runs one live call to confirm the
  model id and that `reasoning_effort` is accepted (a rejection already hands on to
  20b).
- **Asking before risky commands - built.** Hermes' middle tier between "runs" and
  the hardline blocklist: `approvals.py` matches recursive deletes, `find -delete`,
  `xargs rm`, open permissions, `kill`/`pkill`, `systemctl stop/restart`, writes to
  `.env`/`.ssh`/`/etc`, table drops, `sh -c`/`python -c`, git resets; React's shell
  tool DMs the command and the reason and waits; the user's yes/no reaches
  `approvals.answer` in the orchestrator before anything else; 5 minutes of silence
  is a no and React is told not to retry. One request waits at a time. Hermes'
  "smart" mode (a model waving low-risk commands through) is not built: a model call
  per command for a tool used a few times a week. **Discord test for the user:** ask
  IGOR to run `pkill -f no_such_process_xyz`, expect the approval DM, reply no.
- The ARCHITECTURE summary said "no voice, no image reading"; corrected.
- **Shell sandbox - built.** Hermes runs commands in Docker; this host has ~500 MB
  free and the docker group is root by another name, so `sandbox.py` uses bubblewrap
  0.6.1 (apt, installed 10-19) - a flagged deviation. Found while designing it: the
  unit's `EnvironmentFile=/opt/igor/.env` put every key in IGOR's environment and
  every shell or python_run subprocess inherited it, so `env` printed them. Now both
  run with a clean environment, `.env` and the sealed and private memory files and
  the deploy folder hidden, no network, own PID namespace, read-only except `/tmp`
  and `.git`, 1 GiB address space; a sandbox that cannot start refuses, it never
  runs bare. Probed live as igor before wiring. Usage since 08-01: 8 shell calls,
  all read-only (`git log/status/show`, `ps`), so the cost to real use is nil.
- **Checkpoints and rollback - built.** Hermes snapshots a directory into a shadow
  git repo before file tools and destructive shell commands. With the shell now
  read-only, only `write_file`, `patch_file` and `memory_write` change files, so
  `checkpoints.py` keeps per-file before-images in `memory/.checkpoints/` instead,
  on Hermes' rules otherwise (once per turn, first image wins, newest 20, hand edits
  kept unless `all`, a rollback is itself checkpointed). The user types `rollback`,
  `rollback diff N`, `rollback N`, `rollback N all` or `rollback N <file>`; handled in
  code, no model. Not rewound: the conversation (Hermes does). Fails open. Three bugs
  caught by the tests before deploy: a deleted file read as "nothing recorded",
  folder names that sorted 10 before 2, and a pruned name reused and pruned at once.
  **Discord test for the user:** ask IGOR to add a task, then send `rollback`.
- **Context compression - built.** IGOR showed models the last 6 messages and
  nothing older, and proactive posts (digest, job and gig lists) took those slots
  too. Hermes compresses a session near its context limit: head and tail verbatim,
  an auxiliary model summarises the middle into fixed sections, later passes update
  rather than rewrite, failures back off 60/300/900s. IGOR has one endless
  conversation, so `compression.py` runs on eviction: every 4 messages that leave
  the window, 20b (not chat's 120b) folds them into `conversation_summary.json`,
  under 1800 chars, in the background so no reply waits. `_window` puts it first as
  a system message marked as a record, not instructions, capped at a fifth of the
  budget (1400 chars for React, out of its 7000); SelfDescribe gets none. First live
  run filed IGOR's own 10:00 idea as the user's open work; the prompt now gives a
  proactive post one line in Done. Rerun: 986 tokens, correct.
- **Page heartbeat - built.** Hermes' `/heartbeat every <interval> <prompt>` runs a
  full agent turn per tick and stays silent on `NO_REPLY`; on this free tier that
  would spend a day's chat budget in a day. `heartbeats.py` works like IGOR's
  watchers instead: code fetches the pages named in the request, compares visible
  text (times and "5 minutes ago" ignored, as Hermes' `/loop` digest ignores
  timestamps), and only a changed diff reaches 20b, tool-less, which writes one to
  three sentences or `NO_REPLY`. Hermes' rules: one heartbeat, replaced by a new
  one; status/pause/resume/clear; missed ticks coalesce; a tick waits while the user
  is talking. Different on purpose: a link is required (a no-page recurring message
  is a repeating scheduled message), 15m minimum (it fetches someone else's site),
  12 model calls a day, a page unreachable 3 times in a row is mentioned once.
  Live checks 10-20: the judge reported a $49.99 to $39.99 drop and stayed silent on a
  footer change and on a price rise; python.org and Wikipedia read the same twice
  running. Fetches use urllib because Wikipedia 403s httpx whatever its headers.
  **Discord test for the user:** `heartbeat every 1h tell me when <a page> changes`.
- main.py's startup import check now covers checkpoints, compression, heartbeats and
  url_safety.
- **Sense of time, 2026-10-21.** The 11:39 check-in said "I trust your shift at the
  store went smoothly" on a day the shift had not begun. Its 8-row view held four
  morning posts, so "tomorrow before I work" (Tue 12:10) had fallen out; it saw only
  Tue's "I'm at my retail job today". The summary had the same flaw: "tomorrow or
  Thursday", undated, read wrong a day later. Hermes only puts the current time in its
  prompt (rebuilt per session); IGOR already did that on every message, and one
  conversation that runs for days needs more. Now: the check-in reads 30 rows (posts
  no longer count toward its six turns), plus the summary, with weekday-and-date
  labels and "never assume something mentioned has happened"; the summary writes days
  as dates; Direct, React and SelfDescribe see each user message and post stamped
  "[Tue 10-20 14:30]", with one system note on reading them (stamps are written into
  the text; the stored `ts` never reaches the API). My 10-20 rule "tomorrow is not
  today" was backwards for older messages and is rewritten.
  **Discord test for the user:** mention a plan for tomorrow, then ask about it the
  next day.
- **Memory edited one entry at a time, 2026-10-22.** Hermes' memory tool, on IGOR's
  Markdown notes (`memory_entries.py`): `memory_write` takes add (dated, optionally
  under a section), patch (old_text appearing once) or remove (the entry holding
  old_text: a bullet with its continuation lines, or a paragraph). Exact duplicates,
  injection-shaped text and invisible characters are refused; whole-file overwrite is
  gone (the old schema's append still adds). user.md's cap refusal lists the
  entries; Direct's header shows how full it is. Live user.md: 34 entries, 1488 of
  1500 characters, so the next add will ask for a remove first. Writes are
  checkpointed. **Discord test for the user:** ask IGOR to remember something.
- **Review of the user's 10-20 Discord day, fixed:** the 12:40 check-in asked how
  "today's" application prep was going, 30 minutes after the user said they would do
  it tomorrow, and opened "Hey, just checking in". The check-in now waits until 2h
  after the user last spoke (dropped for the day past 23:00), each conversation line
  in its facts says when it was said, and its prompt asks for I.G.O.R.'s formal voice.
  The digest ran Muse Glimmer 30B twice from one article because the prompt demanded
  exactly 3 bullets; now "up to 3", and code drops a second bullet citing the same
  result. Direct's reminder offer invented the syntax "schedule X for tomorrow at
  07:00 EST"; it now gives a plain sentence that routes to scheduling ("remind me
  tomorrow at 9am to ..."). Two test runners listed tests by hand and had never
  called four of them; one, in test_presence, was failing: "I have been looking
  into" passed the claimed-work gate. Fixed, and all four now run. Working as
  designed: the gig watch (HN live, 3 equity-only posts blocked, nothing worth
  sending), the 07:21 paper revision, the memory push, compression (2 runs, ~1500
  tokens each).
- **Egress check - built (2026-10-20).** `fetch_url` takes its URL from the model,
  which reads untrusted pages, and nothing stopped it fetching the cloud metadata
  service, localhost or the private network. `url_safety.py` follows Hermes'
  module of the same name: loopback, private, link-local (all of 169.254/16 and its
  IPv4-mapped form), 100.64/10 and metadata hosts refused, on every redirect hop
  (an httpx request hook in `fetch_url`, a redirect handler in the heartbeat). DNS
  rebinding is the known gap, as in Hermes. Whether this host's metadata service
  answers unauthenticated requests was not probed: the permission system refused
  it, and the block does not depend on the answer.
- **Connection heartbeat - built.** Hermes exports a gateway-up signal and a
  scheduler heartbeat age and alerts past 180s. `monitor._heartbeat` touches
  `memory/heartbeat.txt` every minute only while Discord reports a live connection
  (a dead scheduler stops it too); `/usr/local/lib/igor-deploy/heartbeat.sh`, root-
  owned beside `deploy.sh` and run by `igor-heartbeat.timer` every 2 minutes,
  restarts IGOR and alerts the webhook when it is over 300s old - never during a
  deploy, never within 5 minutes of a start, once per half hour, then alerts only.
  Authoritative copies in `C:\Dev\IGOR_backup\deploy-machinery\` (README has the
  install). **Failure path tested live 16:03 UTC:** a heartbeat set 10 minutes stale
  restarted IGOR (back online in 7s) and the test-labelled alert reached the webhook.
  At 16:10, stale again within half an hour of that restart: "would alert only", no
  second restart. Dry runs no longer write to the deploy log.
- **Gig watch: Hacker News added**, the contract and freelance posts in the monthly
  "Who is hiring?" thread via Algolia search (4 requests; 40 such posts in October,
  23 AI or automation). Its "Freelancer? Seeking freelancer?" thread ended Oct 2025.
  `gig_search.md` Context now says "not ready for senior roles". **First real run
  (14:45):** 10 new, 3 dropped, 2 picks sent (AI-agent QA, MirrorFly), 8 skipped,
  0 red flags. One flaw, seen twice: the QA pick's note mentioned MirrorFly - the
  screen mixed two posts; the prompt now says each line is about its own posting.
- **First live paper revision (08:20 UTC 10-19) was a replacement, not a revision**:
  it kept none of the current paper's 9 sources and swapped its five ideas for three
  new ones, mostly unpriced. The Evaluator failed it for the missing prices. Code now
  rejects a revision that keeps under a third of the current paper's links and
  retries once told why (`overnight.revision_keeps`). That night spent 58986 of 60000.
- **Review of the user's 10-19 Discord day, fixed:** the weekday "Practical idea for
  IGOR" (scheduled `a055d1`) proposed a #brain-dump channel and !commands for a DM-only
  bot, knowing IGOR by name only. Hermes grounds a cron job by loading the project's
  context file (`workdir`); `compose.write` now attaches the ARCHITECTURE self summary
  when a brief names IGOR (~950 tokens, only those briefs). The Monday UBI draft's
  savings and debt figures came from citizenportal.ai, AI summaries of council
  meetings; the official first-year report has different numbers and the two-year
  PDF is blocked from both the server and the PC. That site is now excluded from the
  advocacy search. The user has no time to check figures, so code does: every
  percentage and dollar figure in a draft must appear on its source page (Exa
  `get_contents`; the search summary if the page cannot be read), else one retry
  told which, else the week is skipped (`monitor._check_figures`). Claude Code
  rewrote the 10-19 post from confirmed figures only and sent it to the user.
- **Attachments: Excel and notebooks added**, read with the standard library as
  Hermes does (`.xlsx` sheets as tab-separated rows, 2000 rows a sheet; `.ipynb`
  cells with outputs cut at 2000 chars).
- `/opt/igor/.env` was readable by every account on the box; now 600, owner igor
  (the backup reads it with sudo tar, IGOR as its owner - both checked).
- ARCHITECTURE.md is at the paging test's limit (~36,950 chars, 10 reads of 3700):
  every addition needs an equal cut.

**Pick up here (2026-10-18 evening):**
0. **Built 2026-10-18: the gig watch** (`agents/gigs.py`, daily 14:45 UTC): n8n forum Jobs board
   plus r/forhire [Hiring] search feed. Red flags are dropped by code patterns and by
   one screening call that must give every post RED, SKIP or a pick; RED blocks the
   posting account (`gigs_blocked.json`), only picks are sent, a post with no verdict
   or a failed call is held. Screening is 120b at low (`MODELS["screening"]`): live
   dry runs on 10-18 showed 20b low flag nothing, 20b medium return nothing at 2048
   and misread the profile at 4096. `gigs_found.md` keeps picks, skips with reasons,
   the last check and tool counts. Dry runs blocked 5 accounts by code pattern (free
   pilot, no project, fee, partnership-only, a seller marketplace). The user is "not
   interested in revenue shares" at all (10-18): revenue/profit share phrases are in
   `memory/gig_search.md` Exclude, so such posts drop in code, unblocked, even with
   stated pay (backup `gig_search.md.bak-20261018`). The user's rule: nothing red-flagged ever
   reaches them, and IGOR never interacts with those accounts. **Not built, by
   agreement:** pitch drafting (call them "pitches", never "proposals" - that word
   is the self-improvement queue) and the pitch log, until the user is ready to send.
   **Check next:** the first 14:45 run's message and `gigs_found.md`.
1. **Done 2026-10-18 00:48 UTC:** g1 (the generic "income feature" goal) retired to
   the archive and **g3** added with the statement the user agreed: ways an
   autonomous agent could earn money for the person who runs it, wide net, no
   delivery step required, not limited to what IGOR can do today (say what each idea
   needs beyond web search, writing and code on a no-GPU server), who pays and how
   much with sources, startup cost, how the money arrives, any time the person spends
   worth at least $20 an hour, legal and honest, ranked shortlist. The user rejected
   an earlier, narrower draft as too constrained. Backup `memory/goals.md.bak-20261018`.
   Earning on its own still needs an outward channel IGOR's limits forbid, so the
   paper is a shortlist for the user to act on. **First night (10-18):** three drafts,
   no paper. Two were false rejections - a 797-word paper over the 6000-char cap
   because links count, and the unsure section written as `#` - and one really was
   1563 words. Fixed the same day: cap 7500 (revise reads 4500 of new findings to pay
   for it), any heading level, one retry told why, digest lists only the latest draft.
   **Check next morning:** g3's first `paper.md`; later, the first live revision.
   The 10-18 drafts ranked AdSense blogs first on hype sources - the content-quality
   risk predicted for this goal.
2. **Done 2026-10-17:** typed proposal decisions ("reject p1 and p2", any case) are
   carried out in code via `_proposal_decisions`, with the tool's own words as the
   reply - no model, so no wrong-case id and no false "done". Ids are case-insensitive
   in proposals and goals. And at the user's request each goal now keeps **one living
   `paper.md`**, revised as findings arrive (old versions in `versions/`), instead of
   a new paper per trigger - the first night's two papers overlapped.

A.4, the overnight work loop, is **live** since 2026-10-17. Read its
first morning: the Overnight section of the 13:00 UTC digest, `memory/work/log.json`,
and whether a night stopped on `daily cap 20b` (see `## Overnight work loop`). Then
the SelfDescribe eval, then V.1. Phase K (clones) and I.1 (desktop dashboard) follow.

**Decided by the user 2026-10-17, following Hermes:** conversation history is kept 90
days (`config.HISTORY_RETENTION_DAYS`; Hermes prunes sessions inactive 90 days), with
10000 messages as a backstop - this resolves the "no hoarding" spec deviation. And
reading `research*.md` or `goals.md` now closes the turn's state-changing tools like a
web read (Hermes: everything read is untrusted); `search_memory` skips those files.
`jobs_found.md` is web text too but is deliberately left open: locking it would refuse
the resume read in "which of these jobs suit me".


1. **The corrections corpus was reviewed 2026-10-16** (the old item 1, ConfigEdit
   rewriting the digest schedule, was fixed 2026-09-16 - Known broken 7). 43 entries
   from 2026-09-24 to 10-16: 42 `Signal: reformulation`, 1 `explicit`. Read one by
   one, roughly 15 to 18 are real corrections - the resume-access failures, the 1am
   scheduling sequence, "That doesn't exist?", a dubious retailer - and **six are
   standing preferences**: no language-specific jobs, nothing senior ("most of these
   seem above my head"), uses Claude for all code, solo computer work, keep those in
   mind when suggesting roles. The rest are ordinary follow-ups (health chat, next
   questions) that the reformulation detector cannot tell apart.
   **Consequence for V.1: reformulation is about one-third signal, so nothing derived
   from it may be auto-approved.** Explicit corrections are rare (1 in a month).
   **One preference applied the same day:** "no language jobs" was in `user.md` since
   10-01 but the job watch rules out from `job_search.md`, so five language roles
   still reached the user. Added `speaking`, `bilingual`, `fluent` to its Exclude
   list (server copy; backup `job_search.md.bak-20261016`). **Not applied, needs the
   user:** "nothing senior" - the 10-15 feed still recommended a "Lead Enterprise
   Revenue Architect". `lead` is not excluded.
2. **Discord test, 2026-10-16 21:14 UTC: failed, fixed in `2f0d9a7`, retest pending.**
   "every weekday at 10am send me one practical idea for IGOR" took the new code
   route, but React answered "Scheduled..." from one call without using the tool -
   the store stayed empty - and Monitor then repeated the false claim from the
   conversation. Now the scheduling route requires the tool (`tool_choice`, measured
   on 120b and 20b) and Monitor gets no history. **Retest: the user resends the same
   message, then "what's scheduled?" - and check `scheduled.json` on the server, not
   the reply.** **Retest passed 21:22 UTC**, checked on the server: the log shows the
   tool required and called (list, then add), and `scheduled.json` holds a055d1,
   weekdays, Mon 2026-10-19 10:00 EDT. Monitor reported it from its status block. One
   new defect from that reply, fixed after: React said "Monday, October 23" for the
   19th because the tool's confirmation gave no date - it now always does.
3. **Done 2026-10-19: the SelfDescribe eval, all 11 cases, on 120b.** The UBI
   case ("How can we utilize your autonomous scheduled system to promote UBI?") no
   longer declines; the decline rule now says a question about what IGOR can do, or
   how a part could be used for something, is SelfDescribe's (the rule said
   "construction" while the grounding paragraph said to answer it). First full run
   7/11. The rest, fixed: the 10-18 gig-watch edit had crammed gigs, scheduled
   messages and presence into one summary paragraph, so "what agents" listed watchers
   and "schedule a message" claimed it needs `presence_config.md` - the summary is
   short again, the gig detail moved below the marker, and a FALSE CLAIM check guards
   it. Two scorer faults: "handed off to React" and "America/New_York" are now
   accepted, and a forbidden name inside a denial ("there is no scheduler.yaml") no
   longer counts as fabrication. The own-code case accepts "cannot restart itself"
   without naming `restart_self`. The eval has a `__main__` guard now; about 3300
   tokens a case, ~36000 a full run. Run it after any change to
   `agents/self_describe.py`, the router prompt, or ARCHITECTURE.md's summary.
   Same session: AI news keeps every number with its unit (a digest said "816-
   parameter" for 816B); the job watch matches exclusions as whole words, so region
   terms work, and `account manager`, `apac`, `emea`, `latam`, `philippines`,
   `mexico`, `saudi`, `riyadh`, `dutch`, `india`, `brazil`, `svp` were added to
   `job_search.md` (backup `job_search.md.bak-20261019`). Side effect: `sales` no
   longer drops "Salesforce" titles. `lead` and `leader` added too: the user is "not ready to tackle senior roles".
4. Then **V.1**, the improvement loop with sign-off buckets.

## Overnight work loop (A.4, live 2026-10-17)

Merged to master and deployed 2026-10-17 01:37 UTC. Design:
`docs/superpowers/specs/2026-10-16-overnight-work-loop-design.md`; ARCHITECTURE.md
describes the code. Built by subagent-driven development: six tasks each reviewed,
a whole-branch review (opus), one fix wave of 12 items, all re-verified.

Live state on the server: `_overnight_tick` registered (hourly, minute 20);
`overnight` added to `memory/digest_config.md` Sections (backup
`digest_config.md.bak-20261017`); goals seeded - **g1** research towards an income
feature, **g2** self-improvement.

**One live session, 2026-10-17 01:38 UTC (forced into the window), g1:** 7 calls on
20b, **9164 tokens** (about 15% of the 60000 nightly budget), 3 searches, **13 sourced
findings**, which triggered a paper. It stayed a draft - "cites 10 links not in the
findings" - and all 10 were real: gpt-oss glues a full-width citation bracket to
every URL (`...price-optimization】`). Fixed the same night in `_links` (URL ends at
brackets, quotes, markdown marks; trailing slash trimmed both sides), with a test
built from those exact citations. The session counts against the night of 10-17.

**First full night (night of 2026-10-17):** 8 sessions alternating g1/g2, none failed,
52733 tokens and all 12 searches (the search cap is what binds), no 429 - so 20b's
daily cap is still unmeasured. Two papers finished with 21-22 sources each, all
verified present in the findings. Three weaknesses, fixed the same day:
- The second paper repeated 4 of the first's 6 topics (1 shared link of ~22): after a
  paper, findings.md is emptied and the planner lost what was covered. PLAN now gets
  earlier papers' titles and headings (`_covered_by_papers`, 1500 chars).
- Both proposals were stale: p2 was the "100am" bug fixed on 10-16 (and misstated),
  p1 an early check-in failure. The first night worked through the 43-entry backlog
  from September with no idea what had been fixed. The session now gets 40 recent
  non-docs commit subjects (`_recent_fixes`); docs-only commits are skipped, because
  with them the "100am" fix ranked 35th of 108 and fell outside the list.
- The digest listed the test session's superseded draft as in progress; a draft now
  drops off once a later paper in its folder finished.
Both settled 2026-10-17: g1's generic statement was replaced by g3 (Next action,
item 1), and the user rejected p1/p2.

**Still to do:** when a night first stops with `daily cap 20b: <limit>`, put that
limit in `config.DAILY_TOKEN_CAPS` and Known broken 2 - the first measurement of
20b's cap. The 60000-token budget is a guess until then.

## Presence, first week (2026-09-17 to 09-21)

Working, after three fixes. The eval scores **7/7: 0 false alarms in 5 silence
cases, 0 misses, 0 echoes.** First genuine message sent 2026-09-21 04:38.

Three bugs, in the order they were found, all by running it rather than reading it:

1. **`lull` measured IGOR, not the user.** It keyed on the last *stored* message,
   and `record_outbound` stores the digest, so the 13:00 digest read as a
   conversation ending: lull fired on every tick from 13:25 to 14:30 and spent three
   model calls on nobody. Now keys on `context_store.last_user_timestamp()` and
   fires once per lull. The daily call cap is why this stayed cheap while wrong.
2. **It read its own messages back to the user.** On 09-18 and 09-20 it sent a
   `qwen3.6 is no longer available` alert that had been resolved on 09-17. It was
   not reporting anything: its own proactive sends were in the bundle as "recent
   conversation" and it raised what was in front of it - the 2026-08-13 mechanism
   rebuilt inside a new feature. The bundle now separates what the user said from
   what IGOR already sent, labels the latter "do not repeat", and truncates it.
   **The `digest` trigger is gone**: it woke presence when the only fresh material
   was IGOR's own digest, so the only subject was the one it must not repeat.
3. **Then it stopped speaking at all.** The eval caught it: 0 false alarms but 2
   misses of 2, silent even about drafts 38 days old, where the same set had scored
   5/6 before. One more prohibition on a prompt built around restraint pushed it
   silent - the SelfDescribe abstention over-shoot again. Fixed structurally:
   `drafts` and `stale_task` only fire once the code has established a fact, so
   those get `_SYSTEM_WRITE` with no SILENT option. `lull` keeps `_SYSTEM_JUDGE`,
   because only there can the code not know. Silence in write mode logs a WARNING.

4. **It was the wrong feature.** The first message it sent was "The draft titled
   Universal Basic Income was sent 6 days ago". The user: *"I asked for a message.
   Not a report I don't even know where is being saved."* They had asked twice for a
   check-in - "Just check in on me", "message me at some random times" - and the
   prompt I wrote explicitly forbade exactly that ("no greeting with nothing behind
   it", "nothing checking whether they are there"). Rebuilt 2026-09-21 as a **daily
   check-in**: the code picks a time between 11:00 and 23:00 local, fresh each day,
   stored so it does not move on every tick. `drafts` and `stale_task` are gone -
   each existed only to report IGOR's own bookkeeping. Pending work still reaches
   the model in the facts block as something it may touch in passing.
5. **It claimed work it had never done, and the eval called that a pass.** From the
   same run: *"I've been working on the X/Twitter fetch module - just finished a
   solid prototype, ready when you're ready to look."* None of it happened. Presence
   has no tools. The eval only scored speak-versus-silent, echoes and report
   language, so it could not see a lie - and `record_outbound` would have made that
   prototype an input to every later turn. `_tidy` now drops any first-person claim
   of having worked on, built, finished or looked into something (false by
   construction here) and logs it at ERROR. **The prompt already forbade it and the
   model did it anyway**, which is the whole argument for controls in code.

**Cadence: one check-in a day at a varied time, plus `lull` when the user has been
talking.** Presence still only knows `tasks.md`, `drafts.md` and the conversation,
so the check-in is warm rather than informed. Making it *interesting* means giving
it more real material about the user's day, not more triggers.

**Run `tests/eval_presence.py` after any change to either prompt.** It reports false
alarm rate, miss rate and echo count - this feature has now failed in both
directions, and one sample cannot tell them apart.

## Job watch (2026-09-23 to 09-28)

The user asked for IGOR to apply to remote jobs on Indeed as they appear. Neither
half of that survived contact with the facts, and both were flagged rather than
silently substituted.

**Indeed cannot be a source.** Publisher API retired 2023, Job Search API closed to
new developers, current access NDA-gated with six-figure minimums. Remotive,
RemoteOK, We Work Remotely RSS and Greenhouse company boards publish openly and were
each confirmed reachable from the server. Fetching costs no tokens.

**IGOR does not apply.** A Robert Half survey of 2,000+ hiring managers (fielded
2025-11) found 67% said AI-generated applications had slowed their hiring, and mass
identical submissions get ATS-flagged; volume-apply response runs 2-3%. Vendor
figures, so directional - but it is the user's name on every application. Same rule
as advocacy posts: IGOR drafts, a human sends.

**Ranking, added 09-28.** Title keywords alone returned an entry-level QA tester and
an Anthropic research engineer in one list. One model call a day now orders the
shortlist against `memory/private/profile.md` and says what is a stretch. Verified
live: it ranked support and QA roles above specialised annotation work and said why.

**The resume never leaves the box.** `memory/resume.md` holds contact details and
both versions of his history; `_is_private` makes it unreachable. Ranking sends only
the profile - skills and work history, no name, phone, email or address.

**Resume timeline settled with the user, 2026-10-09 to 10-12.** Two source resumes
were reconciled, a missing current job added and the profile used for ranking
updated to match, de-identified. The user's decisions on how each role is dated are
recorded in `memory/resume.md` itself, not here: this file is public. Do not
re-split or re-date any entry without the user. A pre-edit copy is at
`memory/resume.md.bak` on the server.

## Job watch went silent for five days, and why (2026-09-30)

It worked, then stopped, and nothing said so. Deliveries: 10 postings on 09-23, 2 on
09-24, 1 on 09-25, then **zero every day through 09-30**.

Measured cause: 1043 postings fetched, **9 matched the keywords**, and all 9 had
already been sent. These feeds are static - the same roles stay listed for weeks -
so "new matches only" means permanent silence once the backlog is cleared.

Widening `job_search.md` to generic words (assistant, operations, junior) took it to
31 matches and pulled in payroll assistants and crypto traders. Trimmed back to 11,
which are the right kind. **The keyword list is a coarse net, not the selector** -
`jobs.rank` does the selecting against the profile, and now sees up to 30 matches
rather than an arbitrary first 8.

**Still unsolved: silence when nothing is new.** The watch returns early and sends
nothing, which is indistinguishable from being broken - it is how this went unnoticed
for five days. The fix is a weekly "nothing new, these are still open" message, or a
shorter dedupe window so open roles resurface. Not built; decide which before the
next quiet week.

**Also fixed 09-30:** the watch kept no record of what it found, so asked "which jobs
that you've found recently fit me best", IGOR had nothing to read and asked the user
to describe his own background back to it. `jobs.record_found` now writes
`memory/jobs_found.md` - postings, links and fit notes, 30 days, in `memory_read`'s
list. Backfilled with today's 11 so it is answerable now.

## Restock alerts (2026-10-13)

He asked for restock alerts after talking to IGOR: "Thats where it will benefit me
the most." Right call - the price watch reads Slickdeals, which only knows about
discounts somebody posted, and cannot see a vendor refilling a shelf.

**`agents/stock.py`, hourly, zero tokens.** Shopify exposes
`/products/<handle>.js`: variants with an `available` boolean and a price in cents.
Structured, keyless, no model call. Config is `memory/stock_watch.md` (`url:`,
optional `variant:` and `under:`); state is `memory/stock_state.json`. Monitor
answers from `stock.status()`.

**Measured on the first live run: the AOOSTAR AG01 was in stock, US variant, $179** -
under his old $200 target - and the price watch could not see it, because the only
Slickdeals posting was 18 months old and correctly age-filtered. That is the gap this
closes, demonstrated rather than argued.

**The relay is the weak point.** `aoostar.com` refuses this server: 429 inside 150ms,
including the bare storefront on the fourth request ever, still refusing after a 60s
wait - an IP-level block on the Oracle range. The fetch tries direct, then falls back
to `r.jina.ai`. **A third party is on the critical path.** If it goes, restock checks
stop and the log says so. Only public product URLs are sent to it.

Three non-obvious things, each a test:

- **A failed fetch must not record "unavailable"**, or the next good check looks like
  a restock and messages about nothing. Both failure paths carry state forward.
- **State is tracked twice**: raw availability for status, and available-AND-under-
  ceiling for transitions. Recording an over-target restock as merely available would
  suppress the alert when the price later dropped while still in stock.
- **The variant filter is anchored to the region option**, not a substring of the
  title. `variant: us` matched `EU（Shipped from GE warehouse）` on the first live
  run, because "warehouse" contains "us". Caught by reading the log, not by a test -
  the second time in two days a substring match looked fine offline and matched
  something absurd against real data.

**Corrected 2026-10-21:** a note here once said `restart_self` worked through
`watchdog.py`. On this host it never could: the watchdog ran as `igor`, and
`sudo -l -U igor` says igor may not run sudo, so its `sudo systemctl restart igor`
always failed. The watchdog is deleted; `restart_self` now tells the user the change
needs deploying, which is what actually happens.

**The self-summary budget is the recurring tax here.** Documenting the stock watch
pushed it over 5000 twice. The three watchers are now one combined block above the
marker with depth below, which bought real headroom instead of shaving adjectives,
but it sits at **4994 of 5000**. Adding a subsystem means restructuring, not editing.

## The test suite only ran on one laptop (2026-10-12, fixed)

Asked whether everything shipped that day was verified end to end. It was not, and
running every suite on the server with its own python found three things, none of
them in the new features:

1. **Five suites carried a hardcoded `sys.path.insert(0, r"c:\Dev\IGOR")`** -
   `test_read_file_paging`, `test_digest_sources`, `test_research_threads`,
   `test_self_describe`, `test_self_grounding`. They passed locally and died with
   `ModuleNotFoundError` anywhere else, including the server, which is where this
   file and CLAUDE.md both say to run them. Now using the portable idiom.
2. **The ARCHITECTURE self-summary had grown to 6791 chars against
   `self_describe._MAX_DOC_CHARS = 6000`**, so SelfDescribe was loading a summary
   silently cut mid-sentence at 4393 tokens a question. That day's own
   documentation pushed it over. Trimmed to **4997** by moving implementation depth
   below the marker. Two stale tool counts were found doing it: the routing table
   still said React had "all 13" tools and another line said 15.
   **The budget is now effectively full - adding a subsystem above the marker means
   removing something else.** `tests/test_self_describe.py` enforces both bounds.
3. **`test_self_grounding` imported `_ground_if_self_referential`**, deliberately
   removed in `13d10ab`. `test_self_describe` already asserts that function is gone,
   so one suite required the absence of what another imported. Deleted; the
   mechanism was replaced by SelfDescribe, which has 27 checks of its own.

A fourth came from the live end-to-end pass rather than any offline test:
**`task_update` was missing from `_WORKER_FORBIDDEN`**, so a delegated worker could
add or close tasks in `tasks.md`. Fixed, and the test now asserts the whole set.

**21 of 21 suites pass locally and on the server.** The live pass (26 checks: tool
registration, real-tree grep, `task_update` against a copy of the real `tasks.md`,
delegation guards, watcher status, presence bundle) is in the session scratch and
worth rebuilding if this needs re-proving.

## Hermes tool parity (2026-10-12)

The user asked how close IGOR was to Hermes Agent, the coding-agent harness in his
superpowers plugin, then asked for the gap closed. Mapped IGOR's React tools against
the Hermes table: parity already on files, shell and web; the real gaps were
`search_files`, `delegate_task` and `todo`. All three are built, tested and deployed.
`skill_view` is NOT a gap to recover - skills were removed deliberately after a stale
entry steered answers toward tables for weeks, and GAMEPLAN R4.4 replaces that with
review files and sign-off.

**React now has 16 tools.** The three new ones:

- **`search_files`** - regex grep over IGOR's own source, `path:line: text`, with up
  to 6 lines of context. Python, not a shell call to grep: the pattern comes from a
  model. Skips private files through `_is_private`, which is a test, not a comment.
  The `context` option exists because the first live delegation found the right line
  and then ran out of iterations trying to read it - `read_file` pages by CHARACTER
  offset while this returns a LINE number, so the worker burned its budget guessing.
- **`delegate_task`** - one isolated worker, `handle()` again with empty context, 8
  iterations, `MODELS["worker"]` (20b, deliberately not react's 120b). **Worker tools
  are intersected with what the parent still holds**, so a parent that has read the
  web cannot launder a write through a worker; granting a worker `search` or
  `fetch_url` counts as the parent reading the web. One at a time
  (`_delegation_gate`), depth capped at 1, 3 per turn.
- **`task_update`** - add/done/list on `tasks.md`, in place. `memory_write` rewrites
  whole files, which is how ConfigEdit destroyed `schedule_config.md`. Shares
  `monitor._parse_tasks` with the digest so the two cannot disagree about what is
  open, which they did once before.

**Verified live on the server, not just offline:** a worker traced `_watcher_status`
to `agents/monitor.py:890` and quoted its docstring; delegating `write_file` from a
web-quarantined parent was refused; a worker's own delegation was refused at the
depth cap.

**Not done, and not a Hermes gap:** a general loop-until-done. `research_loop` has a
100-iteration autonomous loop but it is research-specific. Generalising it is the
larger ambition noted in auto-memory, and it is beyond parity rather than part of it.
A `kanban`-style multi-agent board is also unbuilt; delegation is its prerequisite
and now exists.

## A public repo held the user's phone number (2026-10-12, fixed forward)

`search_files` went live, was pointed at IGOR's own source as a smoke test, and
returned the user's real name and phone number from four test fixtures. The tool was
correct - the real `memory/resume.md` was skipped as designed - and `.gitignore`
(`.env`, `memory/*`) had kept every actual secret out: **zero commits ever contained
a Groq key, a Discord token or private key material.** The fixtures were hand-written
and were the whole leak.

Fixtures now use reserved fictional values. `tests/test_no_pii_committed.py` walks
`git ls-files` and fails on anything shaped like a real phone, email or street
address, and on `resume.md` or `memory/private/` ever becoming tracked. It checks
PATTERNS, never values, because a guard holding the real number in order to search
for it would be the thing it exists to prevent. It exempts only its own file, which
has to carry the shapes it looks for.

**History scrubbed on the user's instruction, 2026-10-12.** `git filter-repo`
replaced the name, the phone number in all three formats, the former employer and the
bare `"912"` fragment with reserved fictional values across all 276 commits, then a
force-push. Current file content is byte-identical to before (verified by comparing
tree hashes). Backups of the original history are two bundles in
`C:\Dev\IGOR_backup\history-rewrite\`.

**Verified from a fresh clone of GitHub, scanning all 1416 objects** rather than
commit diffs - which matters, because `git log -S` reported clean when it was not.
Three passes were needed:

1. The name, phone and employer.
2. **`tests/test_jobs.py` looped over a tuple of literal fragments** - a surname, a
   piece of the phone number, an email domain and a home town - asserting none
   appeared in what reaches the ranking model. The fixture never contained them, so
   it proved nothing while publishing all four. Replaced with "the profile sent must
   equal the fixture's own text exactly", which catches anything extra being
   attached and needs no real values. **Do not quote the old line to explain this.
   Writing this entry the first time put those fragments straight back into the
   public repo, and the final check caught it.**
3. The bare `"912"` left in assertion markers.

**The PII guard only knew shapes** - phone, email, street - and a surname is not
phone-shaped, which is why it missed case 2. It now also derives identifiers from the
real private files at runtime and fails if any appear in a tracked file. On the
server it derives exactly five, and it was confirmed to trip on the line that leaked.
Words come only from a `## Contact` block: falling back to the whole of `profile.md`
pulled in 189 ordinary words and flagged `.gitignore`. The digit pattern has
boundaries, or it slices `022-2024` out of `2022-2024`. It skips with a notice where
the private files are absent, so **it only really counts when run on the server.**

**Two decisions he made 2026-10-12.** The leftover unreachable objects are being
left to GitHub's own garbage collection rather than chased. And **the repo should go
private** - he will reopen it if he finds someone to show IGOR to.

**The repo is still PUBLIC and I could not change it.** The `gh` CLI here is
authenticated as `WoahBanz` with `viewerPermission: READ` and
`viewerCanAdminister: false`; pushes work only because git uses the SSH key, which
is a separate credential. Changing visibility needs the `neoprimlabs` account:
Settings, then Danger Zone, then Change visibility. Until that is done, treat the
repo as public when committing anything.

## Presence answered a question that was already answered (2026-10-09, fixed)

Asked at 05:49 whether the AG01 price watch was running, IGOR answered. At 06:16
presence fired on lull and said the same thing again, unprompted. The journal names
it outright: `Presence (lull): speaking - Yes, the Aoostar AG01 price scouting is
running.`

**The cause was the 09-18 echo fix.** `_facts` split the turns into "What the user
said" and "Already sent to the user unprompted". That stopped presence re-sending
proactive alerts, and it also severed every question from its answer: presence saw
three user lines ending in a question and a separate block it was told not to
comment on, so the question read as open and answering it read as helpful. The label
was false too - four of the five assistant turns it covered were ordinary replies.

**The marker needed to tell them apart was already in the data and was being thrown
away.** `orchestrator.record_outbound` writes `[sent proactively]` and nothing else
carries it; `_facts` stripped it on sight, then called every assistant turn
unprompted. The split is now on what a turn *was*: ordinary replies stay in the
conversation in order, only marked sends get the do-not-repeat label. The 09-18 test
passes unchanged, so that fix is intact.

`presence.CONVERSATION_HEADER` is now public and `tests/eval_presence.py` reads it
rather than holding a copy - this change altered the bundle shape and staled every
fixture at once, and that file's premise is that fixtures match the real bundle.

**Scored eval after the fix, live on the server: 8/8, false alarm 0/6, miss 0/2,
0 echoes, 0 fabrications, 0 reports** - including the 10-09 case as a new SILENT
case, with re-answering it counted as an echo.

## Monitor could not see what the watchers are doing (2026-10-09, fixed)

The first answer in that same exchange was `price_watch - next run scheduled for
2026-10-09 05:56:38.282462+00:00`. True, and useless. "As it should" asks whether
the watch is doing its job, and the status block Monitor answers from held only the
scheduler's job list and the news watchlist. **A registered job and a working watch
look identical from there** - which is how the job watch once sat silent for five
days.

`monitor._watcher_status()` now puts the watch's own state in the block: item count,
when the check last actually ran, each target, and the cheapest match seen with the
same "not verified as the right product" label the status file carries. A check older
than 6 hours is reported as STALE with its age, because that is the condition the
question is really asking about and nothing surfaced it. Verified against live state
after deploy. Microseconds and raw tzinfo are gone from the job list.

**Not done: the job watch has no equivalent.** Ask whether the job watch is working
and Monitor still has only its scheduler entry.

## Price watch, and the wrong product it sent (2026-10-06 to 10-07)

**Built and running.** `agents/prices.py` reads `memory/price_watch.md` and checks
Slickdeals' RSS search every two hours, plus 90s after every start. No key, no
scraping: measured from this server, Best Buy refuses the connection, Micro Center,
camelcamelcamel and PCPartPicker return 403, Newegg redirects to a bot check. Best
Buy's API no longer issues keys to free email addresses and Keepa is about $50 a
month, against the standing no-paid-services decision. Slickdeals is deal-driven
rather than a price feed, which is the shape of "tell me when it dips below X".

**The first live alert was the wrong product.** Watching for an AOOSTAR AG01 OCuLink
eGPU dock, it sent a Minisforum DEG1 at $99. Both are eGPU docks, both matched every
query word, and $99 was genuinely under the $130 target - but the AG01's entire point
is the 800W Huntkey supply built into it, and the DEG1 is a bare enclosure powered
from your own ATX unit. **Nothing in either title separates them**, so no keyword
list was ever going to.

Fixed the way the job watch was fixed: the coarse net stays in code, the judgement
goes to a model. Price, query words and post age are still decided in code. An
optional `want:` line per watch says what the item actually is, in prose, including
what would make a listing the wrong one, and `prices.judge` makes **one `summary`
call, only when something is already under target**, to keep or drop each listing.
Rejected listings are marked seen, or the same wrong product is re-judged every two
hours for as long as it stays posted.

**Every failure path fails open and sends unfiltered.** An empty or garbled reply
arrives as a 200 OK; treating that as DROP would swallow a real price drop with no
error anywhere, which is worse than the wrong suggestion this exists to prevent.
Partial coverage is held back without being remembered, so the next check retries it.

**Measured live on the server, 2026-10-07, 5 of 5 as expected:** the DEG1 dropped,
the AG01 at $179 kept, an SFF-8611 cable and a PCIe adapter card dropped, an AG02
with a 500W supply kept. The offline tests feed canned verdicts, so they prove the
plumbing and nothing about the judgement - rerun the live check after any change to
the prompt or `config.MODELS`.

Two other defects fixed in passing:

- **Overlapping watches sent the same listing twice** in one message. The seen store
  stops repeats between runs, not inside one, and the watches overlap on purpose.
- **The `want:` line was read only as far as its first line.** Indented
  continuations fell through every branch of the parser, so the judge saw a
  description cut off mid-sentence with nothing to show it had happened.

**Current watchlist** (two items, narrowed from three on 2026-10-07): the AG01 by
name under $200, and any-brand OCuLink dock *that includes its own power supply*
under $150. The third watch, a generic `egpu dock` under $130, is the one that
produced the DEG1 and was removed rather than judged - judging it would have burned
a call per check to reject Thunderbolt enclosures.

**It sends nothing when nothing is under target.** A weekly "Still watching" message
was tried the same day and the user's verdict on the first one was "not a good use of
a message". He was right on all three counts: it answered my question rather than
his, it reported a $44 "closest" for an OCuLink dock that was a refurbished DEG1, and
it listed a watch removed an hour earlier because the closest map accumulated instead
of being rebuilt. Liveness moved from push to pull - `memory/price_watch_status.md`
is rewritten every check with what is watched, each target, when it last ran and the
cheapest keyword match, labelled in the file as **not** judged for being the right
product so it cannot be quoted back as the item's price. React reads it, and
`price_watch.md`, when asked. Verified on the server at 05:58 UTC: two items, nothing
under target, nothing sent, file written.

**The job watch still has the weekly message this one just lost.** `jobs.heartbeat`
sends "Job watch is running. Nothing new this week." Same objection applies and it
has not been raised yet - decide whether that becomes a status file too before the
next quiet week.

**Known gap: a stale deal is filtered, a sold-out one is not.** `_MAX_AGE_DAYS = 14`
rejects anything posted longer ago than that - the first live run surfaced an AG01 at
$179 posted 11 April 2025 and would have sent it as news - but a deal posted three
days ago and already dead still reads as live. The message links the listing so this
is visible, not hidden.

## Resolved: the resume split (2026-09-28, fixed 2026-09-30)

**Works:** the file is stored at `memory/resume.md`, unreachable by any tool, and
Direct discusses it fine when the router sends a message to CHAT. It loads on turns
whose message or last four turns mention a resume, CV, work history or cover letter.
The job ranking uses `memory/private/profile.md` daily, which is where IGOR knowing
his history actually pays off.

**Does not work:** "Can you read resume.md?" routes TASK to React, which is sealed,
so it correctly but uselessly says it cannot open the file. Whether the user gets
Direct or React depends on phrasing, so the behaviour looks random from outside.

**Three attempts, three different paths, because I patched symptoms.** First the
refusal message (React), then the trigger words (Direct), then conversational
follow-ups (Direct). Each fix was correct and none addressed the design: the rule is
split across two agents and the router picks which one answers.

**FIXED 2026-09-30.** The quarantine is symmetric instead of React being sealed. One rule - private data and the open web never share a turn. React may
read `resume.md` when no web tool has run in the turn, and reading it switches off
`search` and `fetch_url` for the rest of that turn, exactly as reading the web already
switches off the state-changing tools. That removes the split, so both agents behave
the same way and phrasing stops mattering. It is a change to `react.py`'s tool loop,
which is the most security-critical file in the repo: do it with tests first, and
verify both directions (private-then-web refused, web-then-private refused).

## Private memory files are now actually private (2026-09-27)

ARCHITECTURE claimed since August that `corrections.md` and `drafts.md` were
"readable by no agent". That was an intention, not a control: `memory_read` had an
allowlist, but `search_memory` globbed every `*.md` and `read_file` accepted any path
inside the IGOR root. Both were readable, two ways.
`react._is_private`, called from `_safe_path`, now covers `corrections.md`,
`drafts.md`, `resume.md` and everything under `memory/private/`, so `read_file`,
`patch_file` and `write_file` refuse them and `search_memory` skips them.
`tests/test_private_files.py` reproduced the hole before the fix and guards it now.

The outward call could not be what gets blocked: `fetch_url` is deliberately outside
the post-web quarantine, because reading a search result is the ordinary research
flow. With job postings now arriving daily from the open web, the data had to be the
thing made unreachable.

## Digest news: stop resending the same cycle (2026-09-18)

The user, 2026-09-17: "I'm sick of seeing 3 headlines on ai safety every morning.
They are not productive." Measured across the six digests in `context.db`: five of
six were dominated by safety discourse. Three causes, all fixed:

- The query was `"artificial intelligence news"`. It now asks for model releases,
  developer tools and technical research.
- Nothing compared results against what had already been sent. Used URLs are
  remembered in `memory/news_seen.json` for 14 days.
- `digest_config.md`'s Exclusions section had been annotated "pending code
  implementation" since July and was ignored. Now implemented: keywords filter on
  title and summary (not full text, which would drop a release that mentions safety
  in passing), and generic words cannot become filters. Active terms: apple, ios,
  safety, debate, slowdown, regulation.

Verified by dry run against live search on 2026-09-18, with `MEMORY_DIR` pointed at
a copy so it did not consume the day's stories: 8 fetched, a duplicate Gemini story
dropped, and the three bullets were Gemini 3.8 Live, Figure's Helix 2.5 and Unity's
Codex plugin. **If the section is still not useful, remove `ai_news` from
`digest_config.md` - it takes effect on the next digest, no restart.**

**Presence is ON as of 2026-09-16** (`state: on` in `memory/presence_config.md`).
It had never spoken when it was switched on. Read its decisions before trusting it:
`journalctl -u igor | grep Presence` logs every trigger and whether it spoke or
stayed silent. Turn it off by writing `state: off` in that file, or by asking IGOR
to. `tests/eval_presence.py` scores the judgement and reports a false alarm rate -
run it after any change to the presence prompt.

**The user's direction, set 2026-08-08: a fully autonomous, self improving
assistant.** GAMEPLAN Phase A specs the path, researched then audited against its
own sources. Build order, which is not the obvious one:

1. ~~**A.2 correction logging**~~ - shipped 2026-08-08 (`d4185c4`). **Unverified:
   `memory/corrections.md` does not exist yet.** One deliberately corrective Discord
   message settles it.
2. ~~**S.1 health-gated deploy with rollback**~~ - built and tested 2026-08-10.
3. **V.1 improvement loop** - next. Batch review over a corpus that has accumulated.
4. **A.1 goals + A.3 decomposition** - together, once something consumes them

**Decided against, do not silently revisit: self-modification (S.2).** No proven
method exists, and every serious attempt produced metric-gaming. Evidence is in
GAMEPLAN Phase S. The user asked again on 2026-09-07 ("what can we do to actually
make this happen?"). IGOR's answer did not mention the decision and told them to
build rollback, which S.1 already is. It was raised with the user on 2026-09-12 as a
separate conversation; nothing was reopened.

**IGOR stays a personal tool.** An app-store version was considered and set aside,
not ruled out. Do not build toward multi-tenancy; do not deepen single-user
coupling either.

## Scheduled messages (new, 2026-09-12)

React's `scheduled_message` tool adds, lists or cancels one-off messages in
`memory/scheduled.json`. A monitor job checks the file every 60s and sends what is
due. An entry is fixed `content` (no tokens at delivery) or a `brief` that
`agents/compose.py` writes into words at delivery; given both, the brief wins. Limits: 30
days ahead, 20 pending. Failed sends retry each tick up to 10 times, then stay
marked failed for a human.

- **Polled, not one APScheduler job per message**, because APScheduler 3.11.3's
  default `misfire_grace_time` is 1 second - measured on this server, a date job 5s,
  2min or 1h late is dropped with only a WARNING. An overdue message in a polled
  file is simply due at the next check.
- **Timezone:** `config.USER_TZ = "America/New_York"`, confirmed by the user. Direct
  and React now see `Fri 2026-09-11 22:09 EDT (02:09 UTC)`. The model supplies local
  time or `in_minutes`; code does every conversion.
- **Cost, measured:** 225 tokens on every React request (schema plus one prompt
  line); ~135 tokens on every SelfDescribe call from the ARCHITECTURE summary.
- **Live test passed:** scheduled at 02:12:44 UTC, survived a gated restart at
  02:13:08, delivered 02:17:15 on the first check after its 02:16:44 due time.
- **Verified end to end 2026-09-15:** React called the tool itself for two requests
  made the night before, and both were delivered from the queue - 92704e at 13:15
  UTC and 88532e at 15:40 UTC. The queue is empty and cleans up after itself.
- **2026-10-14, fixed 10-16 (`d045db3`):** one request for "thought provoking
  material at 1am" produced three entries - a misread 10am, then two 1am ones added
  on correction without cancelling anything - and all three sent, two with words the
  user had already read. The follow-up asking for fresh words at send time went to
  Direct, which promised it with no tools. Now: every add lists the other pending
  ids so a correction can cancel; the router has a rule sending "message me later"
  and corrections to TASK (live test: all three replayed messages route React, 26/27
  overall - the one miss, "We could have you use your autonomous scheduled system",
  misroutes identically under the old prompt); Direct may not promise actions or
  deny capabilities.
- **Same day, `37bbf3c` and `7738f9e`:** React can only give a brief (it pre-wrote
  3 of 3 times when offered fixed text); an agent with read-only tools writes the
  message at send time, falling back to compose and then the brief; Monitor lists
  pending messages; same-time briefs merge. Isolated live test passed on the
  writer, Monitor, dictated words and the 11pm -> 11:30 correction.
- **`09fd411`: `update` action**, so a correction is one call, as Hermes does it -
  added because the 10-14 replay left the misread 10am entry beside the new 1am
  one. Unit-tested; **the live replay is untested**: it ran into the daily cap
  (item 2 under Known broken). In the one live correction that ran, React still
  used add-then-cancel, which worked but did not use update.
- **Same day, offline:** "100am"-style times are spelled out in code before any
  model sees them, and every Groq response's token count goes to a ledger Monitor
  reports against the cap. **Next: run `tests/live_scheduling.py` once, tomorrow,**
  when the 2026-10-16 spend has aged out of Groq's window. It checks all three.
- **Same day, four Hermes gaps closed, unit-tested only:** a 120b rate limit now
  falls back to 20b until Groq's stated reset, with a notice on the reply; the shell
  refuses a hardline blocklist in code; `search_history` searches 10000 stored
  messages; scheduled messages repeat (daily, weekdays, weekly) with pause and
  resume. **None of the four has run live.** Fallback only fires on a real 429, so
  it will prove itself the next time 120b runs dry - check journalctl for "falling
  back". The rest belong in tomorrow's live pass.
- **Live pass, 2026-10-16 19:21 UTC (`tests/live_scheduling.py`), 7 of 8 checks:**
  "around 100am" scheduled for 01:00; "draft something new" merged into one entry;
  "make it 11:30" left one entry at 23:30; `search_history` found the 10-14 request
  with its date; "every weekday at 9am" made one weekday entry, rolled from Saturday
  to Monday, and "pause that" paused it. **Fallback fired for real**: 120b hit its
  daily cap mid-run, was benched, React answered on 20b, and the reply carried the
  notice. **The one failure:** the correction still went add-then-cancel, not
  `update` - the outcome was right, the mechanism was not.
- **From that run, fixed offline the same day:** a repeating message's agent now sees
  the series' last three messages (Hermes' `continuity`), because on 20b React wrote
  one specific idea into a weekday brief; and scheduling requests route to React in
  code (`_SCHEDULING`), because the router rule sent them via ConfigEdit. Both
  unit-tested, both fail on the old code. Still open: the ledger's headroom does not
  know spend from before it existed (read 82650 left when Groq said 0) - it corrects
  itself once the ledger is 24h old.
- A run costs ~48k tokens on 120b. Do not repeat it without a code change to test.
- **Known detour, now narrower:** scheduling requests used to route CONFIG and
  usually reached React after ConfigEdit declined (~600-700 wasted tokens); once it
  did not decline - see Known broken 7. The new router rule should stop the detour.
- **Known limitation:** a crash between sending and saving can send a message twice.

## Presence (new, 2026-09-16, off by default)

IGOR deciding unprompted whether to speak. Spec:
`docs/superpowers/specs/2026-09-16-proactive-presence-design.md`.

The research moved two decisions before any code existed. ProactiveBench (22 models)
and ProVoice-Bench both find over-triggering is the measured failure of proactive
agents - they help when asked but will not stay silent when nothing is needed - so
every gate that can be decided without a model is, and the model is asked one narrow
question. Both also find proactiveness does not track model capacity, so it runs on
gpt-oss-20b. The user asked for "random times"; the notification literature finds
random interruptions are judged worse than ones at a natural break, and deferring to
an interruptible moment cut response time 49.7% across 680000 users, so timing is
trigger-driven with randomness inside the trigger. That substitution was put to the
user, not made quietly.

- **Gates, all in code:** quiet 03:00-10:00 local, 4h between messages, 2 messages a
  day, 6 decision calls a day, nothing within 20 minutes of a live conversation.
- **Cost:** worst case 6 calls a day at roughly 700-1000 tokens. Every call logs its
  trigger and verdict, so this becomes a measurement rather than staying an estimate.
- **Grounding:** the model sees only facts read from files and the database, has no
  tools, and is told not to invent activity. Anything it sends enters `context.db`
  through `record_outbound` and becomes an input to every later turn, which is the
  2026-08-13 fabrication mechanism.
- **Scored once, 2026-09-16, before it was ever switched on:** 5/6, and a false
  alarm rate of 0/4 - it stayed silent in every case where silence was right,
  including the one where the only pending item had already been raised and waved
  off. The miss was the opposite direction: tasks outstanding and the user having
  said "remind me what's outstanding sometime", and it said nothing. That is the
  safe way to be wrong, and tuning it toward speaking is how the SelfDescribe
  abstention fix overcorrected into stonewalling. **One run of six cases is a weak
  measurement**, not a verdict.
- **Unverified:** it has never actually spoken to the user. It has not run with
  `state: on`, so no message has been composed from a real bundle.

## Known broken

1. **FIXED 2026-10-21: the old safety stack is gone.** `start.sh` (bypassed:
   `igor.service` runs `main.py` directly) and `watchdog.py` (no sudo rights) were
   deleted, and the service removed from the host.

   What replaced them: **S.1 health-gated deploy with automatic rollback**, at
   `/usr/local/lib/igor-deploy/deploy.sh`, root-owned and outside `/opt/igor`. Syntax
   and import gates run before any restart; the third gate waits for the Discord
   gateway and rolls back if it does not connect. It restarts even when the commit
   has not changed, which is how the 2026-09-12 restart test was done.
   **Deploy with that script, never `git pull && systemctl restart` by hand.**

   Remaining gap: a crash *after* a healthy deploy. Covered only by
   `Restart=always` and the backup script's crash-loop alert.
2. **The daily token budget bites, and is now measured for one model.**
   `gpt-oss-120b` (Direct and React) is **200000 TPD**, read from a 429 body on
   2026-10-16 when a day of live testing exhausted it - roughly 55 React calls a day.
   The cap appears ONLY in the 429 body; the `x-ratelimit-*` headers look healthy
   while it is gone. 20b and qwen are still unmeasured (the overnight loop's first
   daily-cap stop will measure 20b). Since `_rate_limit_reply`
   the user is told it is the daily allowance and Groq's actual wait, and chat no
   longer retries a daily cap for 90s first.
3. Still on Oracle Always Free, so an entitlement change can terminate the
   instance again. See ARCHITECTURE.md, the safety stack does not cover this.
4. **FIXED 2026-10-19.** A dropped Discord connection with a live process now
   restarts IGOR: `monitor._heartbeat` plus the root-owned `heartbeat.sh` timer.
5. **FIXED 2026-09-16.** Every scheduled job used APScheduler's 1s
   `misfire_grace_time`, so a stall longer than a second skipped a job silently. The
   host does stall: a live tick was missed by 1.198s on 2026-09-12. Nothing had been
   lost (25 of 25 digests since 2026-08-18), but a stall at 13:00 would have cost
   that day's digest with only a WARNING. `monitor.setup()` now builds the scheduler
   with `job_defaults={"misfire_grace_time": 300}`: five minutes late beats missing.
6. **Gone.** `tests/test_self_grounding.py`, which failed on import, has been removed.
7. **FIXED 2026-09-16.** A request to move a check-in could rewrite the morning
   digest schedule. On 2026-09-14 "Do it later.. around 11:40" routed CONFIG and
   ConfigEdit wrote `time: 11:40 UTC` into `schedule_config.md`, undetected for six
   days because the digest reads that file at startup. Two controls, both in code
   (`agents/prod_memory.py`), since ConfigEdit cannot see the previous message:
   a settings file is only written when the message names its subject
   (`_mentions_subject`), and the reply now prints the lines that changed
   (`_describe_change`), so a wrong write is visible on arrival rather than six days
   later. A declined message goes to React, which is what should have happened.
   `tests/test_config_guard.py` replays the exact Discord message.
   Still true and not fixed here: the router sends these to CONFIG in the first
   place. That needs a router prompt change and therefore the SelfDescribe eval.

## Tests

T.1 is started, not finished. Run all of them with `venv/bin/python` on the server:

- `tests/test_llm.py` - stdlib, no API. Covers `llm.complete` and checks that every
  module's imports resolve, which `py_compile` cannot.
- `tests/test_scheduled.py` - stdlib, no API, 36 checks. Timezone conversion on
  both sides of DST, rejections, delivery, failure paths, the mid-send race, and an
  unreadable store. Three key behaviours were mutation-checked.
- `tests/test_models_live.py` - one real call per role, about 5000 tokens, not in the
  deploy gate. **Run after any change to `config.MODELS` or `llm.model_params`.**
- `tests/test_delegation.py` - stdlib, no API. Worker isolation, the tool
  intersection that stops quarantine laundering, the depth cap, serialization, and
  web-delegation closing the parent's turn. Injects a fake runner, so it proves the
  wiring and nothing about the worker - rerun the live check in `## Hermes tool
  parity` after changing the worker prompt or model.
- `tests/test_fallback.py` - stdlib, no API. 120b -> 20b on a 429, benching until
  Groq's reset, the turn notice, both models limited, and React's own loop.
- `tests/test_shell_guard.py` - stdlib, no API. The shell blocklist (23 blocked, 14
  everyday commands that must still run, nothing runs if any part is blocked) and
  `search_history` (all words, newest first, literal % and _, speed at 3000 rows).
- `tests/test_usage.py` - stdlib, no API. The token ledger (summing, the 24h
  window, pruning, never raising) and run-together times (`100am` -> `1:00am`, and
  what must stay untouched).
- `tests/test_rate_limit.py` - stdlib, no API. What the user is told on a 429,
  from the real 2026-10-16 daily-cap text; chat does not retry a daily cap.
- **ARCHITECTURE.md is at 36970 of the ~37000 characters ten `read_file` pages
  hold** (`test_read_file_paging`). Trim before adding.
- `tests/live_scheduling.py` - **spends real tokens**, ~4 React turns. The three
  scheduling failures from 2026-10-14/16. Refuses to run when the ledger shows less
  than 40000 left on 120b, and prints what it spent.
- `tests/test_search_files.py` and `tests/test_task_tool.py` - stdlib, no API.
- `tests/test_no_pii_committed.py` - stdlib, no API. **Run before any commit that
  adds a test fixture.** The repo is public.
- `tests/test_monitor_status.py` - stdlib, no API. The status block Monitor answers
  "is X running" from: the watched items, a stale check, an unreadable state file,
  and the job-line formatting.
- `tests/test_prices.py` - stdlib, no API. Parsing, price extraction, stale deals,
  overlapping watches, the status file, and the product judge against canned
  verdicts. The judge's *judgement* is not covered here and cannot be: it needs the
  live check in `## Price watch` above, rerun after any prompt or model change.
- `tests/test_watchers.py`, `test_jobs.py`, `test_quarantine.py`,
  `test_private_files.py`, `test_resume_access.py` - stdlib, no API. All passing
  2026-10-07.
- `tests/test_router_verdicts.py` and `tests/eval_self_describe.py` - live API,
  scored. See Next action 3 for the eval's quirks.

## Backups and monitoring

`scripts/backup_memory.ps1` does both jobs in one daily pass, registered as the
Windows Scheduled Task "IGOR memory backup" at 3am. Run on demand with
`Start-ScheduledTask -TaskName 'IGOR memory backup'`.

- Pulls `memory/` and `.env` to `C:\Dev\IGOR_backup\` as dated archives, last 30
  retained. `memory/scheduled.json` is covered by this. Pull-based because the
  server has no git push credentials.
- Checks that `igor` is active (and `igor-watchdog`, until it was removed
  2026-10-21), and compares systemd's
  restart count against the previous run to catch crash loops.
- Alerts to a Discord webhook on: unreachable host, service not active, backup
  failure, or restart count jumping by 3 or more. A webhook is used rather than
  IGOR itself because the alert has to work when IGOR cannot.
- Also extracts `memory/work/` from the pulled archive into
  `%USERPROFILE%\Documents\IGOR Work` (the overnight loop's papers and proposals).
  A missing folder is not an error, and a failed extraction only warns, so the
  health checks and alerts still run. `-WorkOnly <archive>` runs just that step.
- Sends one all-clear on Sundays, so prolonged silence means the task stopped
  running rather than everything being fine.

The webhook URL file and the host details live outside the repo; their paths are in
`scripts/backup_memory.local.ps1` and `CLAUDE.local.md`, both gitignored. Test the alert path with `-TestAlert`.

**Hourly server push (T.4, 2026-10-20):** root's `memory-push.sh`, on
`igor-memory-push.timer`, pushes `memory/**/*.md` except `private/` and `resume.md`
to the private repo `WoahBanz/igor-memory` with its own deploy key. It does not
depend on this machine. `.env` and `context.db` stay PC-only by design. Dry run:
`sudo /usr/local/lib/igor-deploy/memory-push.sh --dry-run`. Copies of the script
and units are in `C:\Dev\IGOR_backup\deploy-machinery\`.

The three root scripts share one webhook and now name themselves: alerts arrive
as "IGOR deploy", "IGOR heartbeat" or "IGOR backup" (all came as the webhook's own
name, "Heartbeat", until 2026-10-20). heartbeat.sh and memory-push.sh now build the
payload with json.dumps, as deploy.sh did; their hand escaping covered quotes only,
so a backslash or newline in an error would have made the alert invalid JSON.

Caveat: it only runs when this machine is on. Task Scheduler catches up when the
machine returns, so time away means late alerts rather than none.

## Verified on the server since the last rewrite

- **The morning digest through `llm.complete`:** 25 runs since 2026-08-18, all
  "executed successfully". The weather section's provider returned 503 on six days
  in late August; the digest went out without that section each time.
- **Direct's chat replies:** 33 since 2026-08-18.
- **Scheduled messages, end to end:** two requests on 2026-09-14, scheduled by React
  itself and both delivered on time on 2026-09-15.
- **Router accuracy on `qwen/qwen3.8-27b`:** 29/30 on 2026-10-22, after two cases were
  added for the user's own schedule (CHAT) and scheduled messages (MONITOR). Before: 27/28 on 2026-10-21 (one case corrected:
  "message me around 100am" now expects Schedule, the fast path added 10-16). Before
  that 23/24, three consecutive runs, the same
  single miss each time - "We could have you use your autonomous scheduled system to
  promote and inform." goes to Direct instead of SelfDescribe. That is one of the two
  messages behind the 2026-08-13 fabrications, though Direct has no tools and is told
  to say it cannot check how IGOR is built, so the likely failure is a non-answer
  rather than an invention. **No router score was ever recorded on qwen3.6 and Groq
  has deleted it, so this cannot be called a regression** - there is nothing left to
  compare against. Baseline to beat from here: 35/35 (2026-10-22). The
  long-standing miss was fixed by naming suggestions in the SELF rule; that rule
  quotes the case as its example, so a reworded case not in the prompt was added
  and passed too.
- **SelfDescribe eval, 2026-10-22:** 9/11 on a full run after the day's router and
  summary changes; both misses were correct answers the scorer rejected ("is handled
  by" React; "scheduled_message" and "scheduling" instead of "scheduler"). Scorer
  widened, both rerun alone and passed: 11/11.

## Open: React has never been measured at different reasoning_effort

ResearchLoop sets `reasoning_effort` low after measurement: 4x to 7x fewer tokens
with output holding (ARCHITECTURE.md has the table). **React on gpt-oss-120b does
not set it**, deliberately - choosing which tool to call is the case where
reasoning plausibly earns its tokens.

Worth measuring, because React is where the TPM pressure is. The test cannot be a
token count. The failure mode to watch for is it getting quietly worse at deciding,
which needs a real multi-step task through Discord.

Note for whoever measures it: Groq's docs say gpt-oss defaults to high. Measured on
gpt-oss-20b, unset behaves nothing like high. Do not trust the documented default
without sampling it.

**Also open from 2026-08-10:** ResearchLoop picks each query from the last 3000
characters of findings and wanders instead of covering the question. The fix is a
persistent outline with per-subtopic coverage counts (STORM, WebWeaver,
ScaffoldAgent converge on it). Not built. Do not implement ScaffoldAgent's UCB
selector: one preprint, +2.24 on its headline metric.

Also open: **M.3** (settle the rule that any file stating verifiable facts needs an
owner, after five files drifted the same way).

## Recent

- **2026-09-21:** Presence stopped echoing its own messages, the digest trigger was
  removed, and the judge/write split fixed a 2-of-2 miss rate (`4257e4e`, `f43587b`,
  `349eed1`). Eval 7/7. First real proactive message sent at 04:38.
- **2026-09-18:** Digest news query, cross-day dedup and the Exclusions
  implementation (`60d78c3`, `8606692`); presence's lull trigger corrected. One
  NameError slipped every gate - `_read_config` does not exist in `monitor` - and
  was caught only by dry-running the pipeline, not by py_compile, the import gate,
  or unit tests that called the parser directly.
- **2026-09-17:** Presence switched on. ConfigEdit subject gate and change
  reporting (`c8e9c1d`), A.2 reformulation capture (`1399a1c`).
- **2026-09-12:** Scheduled messages shipped (`17d2407`), after the user asked on
  2026-09-07 for a 2pm check-in and IGOR had no way to send one. Design changed
  mid-build from per-message APScheduler jobs to a polled file once APScheduler's
  1-second misfire drop was measured on the server.
- **2026-09-16:** Groq replaced `qwen/qwen3.6-27b` with `qwen/qwen3.8-27b`, and the
  router was the only role on it (`a5c2ad3`). Second model removal in a month. The
  daily check alerted on the first morning and nothing broke in between only because
  no message was sent in that window - a router 404 falls through to React silently.
  Measured before switching: 3.8 returns a clean verdict at `max_tokens=10` with or
  without `reasoning_format` (n=5 each), where 3.6 leaked an unterminated think tag.
- **2026-08-17:** Groq removed the entire Llama family, taking out `router`, `chat`,
  `evaluator` and `summary`. Roles reassigned across the three general-purpose models
  Groq still serves, all 8000 TPM. See ARCHITECTURE.md.
- **2026-07-24ish:** Oracle cut the Always Free A1 allowance and terminated the
  instance. **2026-07-31:** data recovered through a boot volume backup into AD-1,
  then an E2.1.Micro. Nothing lost.

## Lessons worth keeping

**Measure the library's defaults before designing on them.** The first scheduled
messages design used one APScheduler date job per message and "sent overdue ones at
startup". A two-minute probe on the server showed APScheduler 3.11.3 drops any date
job more than one second late, silently. Neither the user guide nor the API page
states that default. The design would have lost every message due during a deploy,
and every test would have passed, because the tests never restart the process.

**Every real bug was found by running the system, never by reading it.** The
watchdog's `2>&1` failure on the one path it existed for. A router reading opinion
questions as CONFIG. Reasoning budgets under the empty-content floor. A missing
stdlib import that would have crashed a deploy. A documented safety stack that had
never once executed. Checking a file's contents is not checking that anything
runs it.

**Fix the class, not the instance.** Patching the exact phrasing that failed
produced the same bug twice in one evening, and six stacked "do not claim what you
cannot verify" rules eventually told Direct not to read its own conversation.

**Prefer a control in code to an instruction in a prompt.** The model ignored
written guidance repeatedly - it kept writing its training-era year into search
queries with the real date in its prompt. Every durable fix moved a rule out of the
prompt and into code. Scheduled messages follow it: the model never does timezone
or duration arithmetic, the code does.

**Verify a source's date and measurement conditions before citing it.** Two specs
were written on overstated evidence: a fifteen-month-old paper quoted as current,
and a 73% result that was the best of two tasks where the other was 14.5%. Both
caught by the user, not by me. See the memory note of the same name.

**A test that confirms the fix is not a test.** Diagnosing the digest bug, the first
attempt hand-wrote a snippet containing the word "bacteriophage" and unsurprisingly
passed. Only the real API response exposed the real cause. The same applies to
tests written after the code: the scheduled messages tests were mutation-checked -
the code broken on purpose three ways - before being trusted.

**One sample is not a measurement, and a provider's documented default is not a
measurement either.** An early pass ran one call per setting and wrote a confident,
wrong story into three files before the second sample. Sample before writing it down
as fact. The UBI decline on 2026-09-12 was not called a regression until an
interleaved 3-vs-3 A/B showed it was not one.

**A fabrication that reaches persisted context becomes a permanent input.** On
2026-08-13 IGOR invented a `scheduler.yaml`, a `run_agent()` helper and a content
filter, and the next day repeated it with zero tool calls because its own invention
was in the conversation window. Only deleting the rows from `context.db` fixed it.
**Wrong output that gets stored is an undetected input to every later turn.**
Anything that learns from IGOR's own history needs a way to retract, not just to
append.

**Fix the model of the problem before fixing the problem.** Six attempts went into
the `scheduler.yaml` fabrication, all assuming a knowledge gap. The cause was in
published work the whole time: hallucination is a scoring artifact (arXiv
2509.04664). Four searches would have found that before the first line of code.

**Then the correction over-shot, and only a scored set caught it.** With abstention
rewarded, SelfDescribe started refusing. `tests/eval_self_describe.py` scores
fabrication, stonewalling and missed handoff separately. **An agent whose failure is
plausible-sounding text cannot be checked by reading one of its answers.**

**A model can break a working feature with no commit on our side.** The digest lost
every link on 2026-08-10 with prompt, code and input unchanged. Anything a model is
asked to reproduce *verbatim* - a URL, an ID, a path, a number - should be carried in
code and keyed by the smallest token the model can supply. Ask it to choose, never to
transcribe.

**A gate that blocks good work gets switched off.** S.1's syntax gate once reported
a syntax error that did not exist. A check must only fail for the reason it claims.

**The alert is part of the safety system, not decoration.** S.1's first rollback
alert was silently dropped by a BOM and trailing CR in the webhook file. Any alert
path needs a way to be fired on demand - `--test-alert` here, `-TestAlert` there.

**A rule written as prose gets re-derived wrong at every new call site.** `llm.py`
is Groq's `finish_reason` rule as code that cannot be skipped. Same finding as "fix
the class" and "prefer a control in code"; all three are one thing.
