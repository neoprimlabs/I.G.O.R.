# STATE.md - Where Things Stand Right Now

**Rewrite this file. Never append to it.** History belongs in git log and
GAMEPLAN's Progress Log. This file answers one question: what is true today?

Last updated: **2026-09-12**

---

## Running

- **Host:** `129.80.181.77` - Oracle `VM.Standard.E2.1.Micro`, x86_64,
  US-ASHBURN-AD-1, Always Free, 1 OCPU / 956 MB RAM + 2 GB swapfile
- **OS:** Ubuntu 22.04.5, Python 3.10.12, service user `igor`, root `/opt/igor`
- **Services:** `igor` and `igor-watchdog`, both enabled and active
- **Status:** online, confirmed responding on Discord

## Code

- Server `/opt/igor` runs `17d2407` (scheduled messages), deployed through the S.1
  gate on 2026-09-12, twice - the second deploy was the restart for the live test.
  `origin/master` may be ahead by documentation-only commits; those need no deploy,
  because nothing at runtime reads STATE.md or GAMEPLAN.md.
- The server tree was copied off the rescued disk rather than cloned, but its
  `.git` tracks the same remote and pulls normally.

## Next action

**Pick up here.** Two checks that need the user, then the SelfDescribe eval, then V.1:

1. **One real Discord request for a scheduled message.** "Message me in 5 minutes
   to check in." The live test on 2026-09-12 called `agents/scheduled.py` directly,
   so it proved storage, restart survival and delivery - not that the model calls
   the tool well. Expect the request to route CONFIG, get declined by ConfigEdit and
   forwarded to React; that detour is known and accepted (see Scheduled messages).
2. **Does A.2 actually work?** `memory/corrections.md` still does not exist, checked
   2026-09-12. Ask the user to send IGOR a corrective Discord message ("no, that's
   wrong - X is actually Y"), then check whether the file appears. If it does not,
   fix the detection in `orchestrator._looks_like_correction` before anything else,
   because V.1 consumes that corpus and there is currently no corpus.
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

**Waiting on the user:** whether to set `misfire_grace_time` on the scheduler (see
Known broken 5). Proposed, not done, because it changes jobs they did not ask about.

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
- **Known detour:** scheduling requests route CONFIG (the router's CONFIG line says
  "change a schedule time"), ConfigEdit declines, React handles it. One wasted call,
  ~600-700 tokens. Left alone because a router change requires the SelfDescribe eval.
- **Known limitation:** a crash between sending and saving can send a message twice.

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
5. **Every existing scheduled job can be silently skipped by a 1-second stall.**
   The digest (13:00 UTC), the model check and the advocacy draft use APScheduler's
   default `misfire_grace_time` of 1s. On 2026-09-12 the first scheduled-message
   tick after a restart was logged "missed by 0:00:01.198" - so this box does stall
   past a second. No digest has been missed (25 of 25 ran since 2026-08-18, none
   missed since 2026-08-01), but a miss would mean no digest until the next day, with
   only a WARNING line. The fix is one line -
   `AsyncIOScheduler(job_defaults={"misfire_grace_time": 300})` in `monitor.setup()`
   - and is waiting on the user.
6. **`tests/test_self_grounding.py` fails on import** and has since `13d10ab`, when
   grounding moved from React to SelfDescribe: it imports
   `_ground_if_self_referential`, which no longer exists. Delete it or rewrite it
   against SelfDescribe.

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

- **2026-09-12:** Scheduled messages shipped (`17d2407`), after the user asked on
  2026-09-07 for a 2pm check-in and IGOR had no way to send one. Design changed
  mid-build from per-message APScheduler jobs to a polled file once APScheduler's
  1-second misfire drop was measured on the server.
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
