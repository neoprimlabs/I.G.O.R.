# STATE.md - Where Things Stand Right Now

**Rewrite this file. Never append to it.** History belongs in git log and
GAMEPLAN's Progress Log. This file answers one question: what is true today?

Last updated: **2026-09-16**

---

## Running

- **Host:** `129.80.181.77` - Oracle `VM.Standard.E2.1.Micro`, x86_64,
  US-ASHBURN-AD-1, Always Free, 1 OCPU / 956 MB RAM + 2 GB swapfile
- **OS:** Ubuntu 22.04.5, Python 3.10.12, service user `igor`, root `/opt/igor`
- **Services:** `igor` and `igor-watchdog`, both enabled and active
- **Status:** online, confirmed responding on Discord

## Code

- Server `/opt/igor` runs `a5c2ad3` (router on qwen3.8), deployed through the S.1
  gate on 2026-09-16. `17d2407` before it, deployed 2026-09-12.
  `origin/master` may be ahead by documentation-only commits; those need no deploy,
  because nothing at runtime reads STATE.md or GAMEPLAN.md.
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

**Cadence is roughly weekly, not the couple-a-day the spec imagined.** `lull` needs
the user to message first, and drafts and stale_task are on a 7-day cooldown. That
is a material limit, not a tuning one: presence only knows `tasks.md`, `drafts.md`
and the conversation. More frequency means giving it more real material, not more
triggers.

**Run `tests/eval_presence.py` after any change to either prompt.** It reports false
alarm rate, miss rate and echo count - this feature has now failed in both
directions, and one sample cannot tell them apart.

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
due; the text is fixed at scheduling time, so delivery costs no tokens. Limits: 30
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
- **Known detour, and it has bitten once:** scheduling requests route CONFIG (the
  router's CONFIG line names "change a schedule time"). Usually ConfigEdit declines
  and React handles it, costing one wasted call, ~600-700 tokens. Once it did not
  decline - see Known broken 7.
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
2. **The daily token budget exists, bites, and is completely unmeasured.** A
   tokens-per-DAY cap appears ONLY in the 429 body - the `x-ratelimit-*` headers
   report healthy per-minute state while the daily budget is gone. The one number
   ever measured, 100000/day, was on `llama-3.3-70b-versatile`, which Groq deleted on
   2026-08-17. No per-day limit is known for any model IGOR now runs on. Re-measure
   from a 429 body before planning anything around a daily budget.
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
