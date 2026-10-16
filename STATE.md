# STATE.md - Where Things Stand Right Now

**Rewrite this file. Never append to it.** History belongs in git log and
GAMEPLAN's Progress Log. This file answers one question: what is true today?

Last updated: **2026-10-16**

---

## Running

- **Host:** `129.80.181.77` - Oracle `VM.Standard.E2.1.Micro`, x86_64,
  US-ASHBURN-AD-1, Always Free, 1 OCPU / 956 MB RAM + 2 GB swapfile
- **OS:** Ubuntu 22.04.5, Python 3.10.12, service user `igor`, root `/opt/igor`
- **Services:** `igor` and `igor-watchdog`, both enabled and active
- **Status:** online, confirmed responding on Discord

## Code

- Server `/opt/igor` runs `09fd411` (scheduled_message update action), deployed
  through the S.1 gate on 2026-10-16. Check with `git -C /opt/igor log -1`. `origin/master` may be ahead by documentation-only
  commits; those need no deploy, because nothing at runtime reads STATE.md or
  GAMEPLAN.md.
- The server tree was copied off the rescued disk rather than cloned, but its
  `.git` tracks the same remote and pulls normally.

## Next action

**Pick up here.** Two checks that need the user, then the SelfDescribe eval, then V.1:

1. **Fix ConfigEdit overwriting the digest schedule** - Known broken 7. Asked to
   move a check-in, it silently rewrote `schedule_config.md` instead. Restored by
   hand on 2026-09-16; the cause is untouched and it will happen again.
2. **A.2 answered 2026-09-16: the machinery works, the corpus is empty because the
   user does not correct IGOR in those words.** Measured, not guessed. The detector
   scores 7/7 on explicit corrections with 0 false positives on ordinary messages,
   and the writer produces a correct entry - both exercised offline, with no staged
   message sent to IGOR. Running the detector over the real `context.db`: **73 user
   messages from 2026-08-08 to 2026-09-14, zero matches.** Nothing was broken and
   nothing was ever going to be captured.

   What the user does instead is restate the request seconds after IGOR acts - "Will
   you message me at some random times tomorrow?" then, 18 seconds later, "Do it
   later.. around 11:40". Reformulation is a known implicit dissatisfaction signal
   and a known noisy one (EMNLP 2025, arXiv 2507.23158, "A Lens to Understand Users
   But Noisy as a Learning Signal"), so it is now captured under `Signal:
   reformulation`, never merged with `Signal: explicit`. Capture-only as before.

   **Still unverified: nothing has been captured live yet.** The next reformulation
   in Discord creates `corrections.md`. Check it exists, then read what landed in it
   before V.1 consumes any of it - the noise is the known risk, and the labels exist
   so a human can weigh the two signals differently.

   Do not widen `_CORRECTION_PATTERNS` to compensate. They are accurate; the problem
   was never their precision.
3. **The SelfDescribe eval, and one real defect it found.** Four of ten cases were
   run on gpt-oss-120b on 2026-09-12, no 429:
   - Pass: "What tools can you use?", "Where is the digest schedule configured?",
     and the new "Can you schedule a message to me for later?"
   - **Fail: the UBI case declines every time.** SelfDescribe returns
     `NOT_ABOUT_IGOR` and hands "How can we utilize your autonomous scheduled system
     to promote UBI?" to React - the agent that fabricated `scheduler.yaml` on this
     exact question on 2026-08-13. A/B on 2026-09-12: declined 3/3 with the current
     ARCHITECTURE.md and 3/3 with the pre-`17d2407` one, so it is not the scheduled
     messages edit. It is the decline path `32e7dcc` added for messages "not about
     IGOR at all"; `d90926e` kept it to test whether it was enough alone, and this is
     the answer - it over-declines a question that is half about IGOR. Fix it with
     the eval as the judge, not by reading an answer.
   - Six cases still unmeasured on the current model: agents, change own code,
     content filter, social media, router failure, and the two handoffs.
   - `tests/eval_self_describe.py` has **no `__main__` guard** - importing it runs
     all ten cases. Its INVALID RUN message still quotes llama-3.3-70b's 100000/day,
     which is stale. Pass substrings of the questions to run a subset.

   Run it after any change to `agents/self_describe.py`, the router prompt, or
   ARCHITECTURE.md's summary.
4. Then **V.1**, the improvement loop with sign-off buckets.

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

**Resolved 2026-10-09: K&J and New Dimension are two jobs, not one.** They were
never a contradiction between the two source resumes. K&J Comics and Games stemmed
from New Dimension Comics, the companies were near identical, and he worked at both -
New Dimension first, K&J after. The duties read the same because it was the same work
at a sibling company. Both are now on the resume in sequence, and `profile.md`
describes them as two sibling retailers rather than one 2024-2026 block.

**Also added 2026-10-09: his current job.** Electronics Salesperson, Walmart, Delmont
PA, 09/2026 to present. It was missing entirely, so the job watch had been ranking
against a profile that showed no current employment. `profile.md` now carries it
de-identified as a big-box electronics role, and calls customer-facing technical
work - support, success, solutions, sales engineering - a direct fit rather than a
stretch, because explaining consumer technology to non-technical buyers is the day
job now.

**Settled 2026-10-12, both of them.** On his instruction the handover months go to
K&J: the role is now one continuous entry, 05/2024 to 04/2026 under K&J Comics and
Games, with New Dimension Comics named in a bullet as the sibling store it started
at. No invented month, no gap, no overlap, nothing marked unknown. **Do not re-split
this into two dated entries.** He also confirmed Delmont PA is the correct spelling.
`resume.md` now has no TBC markers and a continuous timeline: Walmart 09/2026 to
present, K&J 05/2024 to 04/2026, Pizzeria Davide 11/2022 to 05/2024, Cheesecake
Factory 2020 to 2022. A pre-edit copy is at `memory/resume.md.bak` on the server.

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

**Also corrected:** ARCHITECTURE claimed "No working self-modification -
`restart_self` writes a sentinel nothing acts on." False. `watchdog.py` watches
`/opt/igor/restart_requested`, reads it, unlinks it and runs `systemctl restart igor`
with a cooldown. `restart_self` works; what IGOR cannot do is pull, gate or roll back
its own code. That claim sat in the self-summary, so IGOR was telling people
something untrue about itself.

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
  used add-then-cancel, which worked but did not use update. Re-run the replay
  once, after the daily budget resets. "100am" still reads as 10:00.
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

1. **The old safety stack still does not execute** - but deploys are gated.
   `start.sh` is bypassed (`igor.service` runs `main.py` directly) and the watchdog
   has no sudoers entry, so Layers 1-3 remain dead. S.3 deletes all of it.

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
   while it is gone. 20b and qwen are still unmeasured. The orchestrator's 429 reply
   says "wait a minute", which is wrong for a daily cap with a 20-minute wait.
3. Still on Oracle Always Free, so an entitlement change can terminate the
   instance again. See ARCHITECTURE.md, the safety stack does not cover this.
4. Monitoring detects a dead process, not a dead gateway. If IGOR is running and
   systemd reports active but the Discord connection has silently dropped, the
   check reports healthy while the green dot is out. Closing that needs a
   heartbeat emitted from inside the bot, gated on `is_ready()` and a finite
   `bot.latency`. Not built.
5. **FIXED 2026-09-16.** Every scheduled job used APScheduler's 1s
   `misfire_grace_time`, so a stall longer than a second skipped a job silently. The
   host does stall: a live tick was missed by 1.198s on 2026-09-12. Nothing had been
   lost (25 of 25 digests since 2026-08-18), but a stall at 13:00 would have cost
   that day's digest with only a WARNING. `monitor.setup()` now builds the scheduler
   with `job_defaults={"misfire_grace_time": 300}`: five minutes late beats missing.
6. **`tests/test_self_grounding.py` fails on import** and has since `13d10ab`, when
   grounding moved from React to SelfDescribe: it imports
   `_ground_if_self_referential`, which no longer exists. Delete it or rewrite it
   against SelfDescribe.
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
- Checks that `igor` and `igor-watchdog` are active, and compares systemd's
  restart count against the previous run to catch crash loops.
- Alerts to a Discord webhook on: unreachable host, service not active, backup
  failure, or restart count jumping by 3 or more. A webhook is used rather than
  IGOR itself because the alert has to work when IGOR cannot.
- Sends one all-clear on Sundays, so prolonged silence means the task stopped
  running rather than everything being fine.

Webhook URL lives at `C:\Users\Nucbox\Documents\IGOR_Keys\discord_webhook.txt`,
outside the repo. Test the alert path with `-TestAlert`.

Caveat: it only runs when this machine is on. Task Scheduler catches up when the
machine returns, so time away means late alerts rather than none.

## Verified on the server since the last rewrite

- **The morning digest through `llm.complete`:** 25 runs since 2026-08-18, all
  "executed successfully". The weather section's provider returned 503 on six days
  in late August; the digest went out without that section each time.
- **Direct's chat replies:** 33 since 2026-08-18.
- **Scheduled messages, end to end:** two requests on 2026-09-14, scheduled by React
  itself and both delivered on time on 2026-09-15.
- **Router accuracy on `qwen/qwen3.8-27b`:** 23/24, three consecutive runs, the same
  single miss each time - "We could have you use your autonomous scheduled system to
  promote and inform." goes to Direct instead of SelfDescribe. That is one of the two
  messages behind the 2026-08-13 fabrications, though Direct has no tools and is told
  to say it cannot check how IGOR is built, so the likely failure is a non-answer
  rather than an invention. **No router score was ever recorded on qwen3.6 and Groq
  has deleted it, so this cannot be called a regression** - there is nothing left to
  compare against. Baseline to beat from here: 23/24.

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
