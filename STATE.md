# STATE.md - Where Things Stand Right Now

**Rewrite this file. Never append to it.** History belongs in git log and
GAMEPLAN's Progress Log. This file answers one question: what is true today?

Last updated: **2026-08-10**

---

## Running

- **Host:** `129.80.181.77` - Oracle `VM.Standard.E2.1.Micro`, x86_64,
  US-ASHBURN-AD-1, Always Free, 1 OCPU / 956 MB RAM + 2 GB swapfile
- **OS:** Ubuntu 22.04.5, Python 3.10.12, service user `igor`, root `/opt/igor`
- **Services:** `igor` and `igor-watchdog`, both enabled and active
- **Status:** online, confirmed responding on Discord

## Code

- Local, `origin/master`, and server `/opt/igor` are all in sync. Server was
  deployed and restarted 2026-08-10; both services verified active afterwards.
- The server tree was copied off the rescued disk rather than cloned, but its
  `.git` tracks the same remote and pulls normally.

## Next action

Restoration finished 2026-08-03. Everything since is forward work and bug fixes
found by using the system.

**The user's direction, set 2026-08-08: a fully autonomous, self improving
assistant.** GAMEPLAN Phase A specs the path, researched then audited against its
own sources. Build order, which is not the obvious one:

1. ~~**A.2 correction logging**~~ - shipped 2026-08-08 (`d4185c4`). **Unverified:
   `memory/corrections.md` does not exist yet.** Either nothing has been corrected
   since Friday or the detection never fires, and those look identical from here.
   One deliberately corrective Discord message settles it.
2. ~~**S.1 health-gated deploy with rollback**~~ - built and tested 2026-08-10.
3. **V.1 improvement loop** - next. Batch review over a corpus that has accumulated.
4. **A.1 goals + A.3 decomposition** - together, once something consumes them

**T.1 is started, not finished.** `tests/test_llm.py` is the first test in the repo,
stdlib only, run with `python tests/test_llm.py` (use `venv/bin/python` on the
server). It covers `llm.complete` and checks that every module's imports resolve,
which `py_compile` cannot. Nothing else has tests yet.

Also open: **M.3** (settle the rule that any file stating verifiable facts needs an
owner, after five files drifted the same way).

**Open from the 2026-08-10 research run:** ResearchLoop picks each query from the
last 3000 characters of findings and bans its own `Next:` threads as
"already pursued", so it wanders instead of covering the question. A 20-iteration
run on autonomous agents spent 18 iterations on physical machines and never asked
about software agents. The fix is a persistent outline with per-subtopic coverage
counts, revised every few iterations - grounded in STORM, WebWeaver and
ScaffoldAgent, which converge on selecting the next query from a global structure
rather than from the previous finding. Not built. Do not implement ScaffoldAgent's
UCB selector: one preprint, two months old, +2.24 on its headline metric.

**Decided against, do not silently revisit: self-modification (S.2).** No proven
method exists, and three independent research efforts all produced metric-gaming.
Evidence is in GAMEPLAN Phase S.

**IGOR stays a personal tool.** An app-store version was considered and set aside,
not ruled out. Do not build toward multi-tenancy; do not deepen single-user
coupling either.

## Known broken

1. **The old safety stack still does not execute** - but deploys are now gated.
   `start.sh` is bypassed (`igor.service` runs `main.py` directly) and the watchdog
   has no sudoers entry, so Layers 1-3 remain dead. S.3 deletes all of it.

   What replaced them, built and tested 2026-08-10: **S.1 health-gated deploy with
   automatic rollback**, at `/usr/local/lib/igor-deploy/deploy.sh`, root-owned and
   outside `/opt/igor`. Syntax and import gates run before any restart; the third
   gate waits for the Discord gateway and rolls back if it does not connect.
   Verified against a deliberately hung commit - it rolled back and alerted.
   **Deploy with that script, never `git pull && systemctl restart` by hand.**

   Remaining gap: a crash *after* a healthy deploy. Covered only by
   `Restart=always` and the backup script's crash-loop alert, as before.
2. Still on Oracle Always Free, so an entitlement change can terminate the
   instance again. See ARCHITECTURE.md, the safety stack does not cover this.
3. Monitoring detects a dead process, not a dead gateway. If IGOR is running and
   systemd reports active but the Discord connection has silently dropped, the
   check reports healthy while the green dot is out. Closing that needs a
   heartbeat emitted from inside the bot, gated on `is_ready()` and a finite
   `bot.latency`. Not built.

## Backups and monitoring

`scripts/backup_memory.ps1` does both jobs in one daily pass, registered as the
Windows Scheduled Task "IGOR memory backup" at 3am. Run on demand with
`Start-ScheduledTask -TaskName 'IGOR memory backup'`.

- Pulls `memory/` and `.env` to `C:\Dev\IGOR_backup\` as dated archives, last 30
  retained. Pull-based because the server has no git push credentials.
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

## Open: React has never been measured at different reasoning_effort

ResearchLoop now sets `reasoning_effort` low after measurement: 4x to 7x fewer
tokens with output holding (ARCHITECTURE.md has the table). **React on gpt-oss-120b
does not set it**, deliberately - choosing which tool to call is the case where
reasoning plausibly earns its tokens, and it is a different model.

Worth measuring next, because React is where the TPM pressure actually is. The test
cannot be a token count. The failure mode to watch for is it getting quietly worse
at deciding, which needs a real multi-step task through Discord.

Note for whoever measures it: Groq's docs say gpt-oss defaults to high. Measured on
gpt-oss-20b, unset behaves nothing like high. Do not trust the documented default
without sampling it.

## Unverified on the server

Deployed 2026-08-10, `tests/test_llm.py` passes on the server under the venv
interpreter (18/18), both services active, no warnings since restart. Two paths
have not yet executed against the live API:

- **The morning digest**, which now goes through `llm.complete`. It fires on
  schedule; the next run is the first real exercise of it.
- **Direct's chat replies**, since `call_claude` changed. Worth one Discord message
  to confirm, per the rule about testing anything that touches the bot path.

## Recent

- **2026-07-24ish:** Oracle cut the Always Free A1 allowance from 4 OCPU/24 GB
  to 2 OCPU/12 GB and terminated the instance, which was over the new limit.
- **2026-07-31:** Data recovered. A1 capacity was exhausted in all three Ashburn
  ADs; recovery went through a region-scoped boot volume backup restored into
  AD-1, then an E2.1.Micro with the clone attached as a data disk. Nothing lost.
- **2026-08-02:** Long session. Docs restructured into rules/facts/plan/state,
  backups and alerting built, Phase R2 completed, R3.0 designed against published
  practice rather than patched, and most of Phase C cleared.

## Lessons worth keeping

**Every real bug this week was found by running the system, never by reading it.**
The watchdog's `2>&1` failure on the one path it existed for. A router reading
opinion questions as CONFIG. Reasoning budgets under the empty-content floor. A
missing stdlib import that would have crashed a deploy. A documented safety stack
that had never once executed. A news digest calling bacteriophages computer viruses.
Checking a file's contents is not checking that anything runs it.

**Fix the class, not the instance.** Patching the exact phrasing that failed
produced the same bug twice in one evening, and six stacked "do not claim what you
cannot verify" rules eventually told Direct not to read its own conversation.

**Prefer a control in code to an instruction in a prompt.** The model ignored
written guidance repeatedly - it kept writing its training-era year into search
queries with the real date in its prompt. Every durable fix moved a rule out of the
prompt and into code.

**Verify a source's date and measurement conditions before citing it.** Two specs
were written on overstated evidence: a fifteen-month-old paper quoted as current,
and a 73% result that was the best of two tasks where the other was 14.5%. Both
caught by the user, not by me. See the memory note of the same name.

**A test that confirms the fix is not a test.** Diagnosing the digest bug, the first
attempt hand-wrote a snippet containing the word "bacteriophage" and unsurprisingly
passed. Only the real API response exposed the real cause.

**Check whether a behaviour is configurable before building machinery to survive
it.** `reasoning_effort` was never set on either gpt-oss model and never
investigated, through an empty-content floor, a doubling retry and a truncation fix.
Setting it low cut research tokens 4x to 7x.

**One sample is not a measurement, and a provider's documented default is not a
measurement either.** The first pass at the above ran one call per setting, saw an
empty response at high, and produced a confident story about IGOR running at maximum
effort and that being the root cause of two days of bugs. At n=4 the empty response
did not reappear and unset looked nothing like high. Both claims were wrong, and
they had already been written into ARCHITECTURE.md, STATE.md and a commit message
before the second sample. Sample before writing it down as fact.

**A rule written as prose gets re-derived wrong at every new call site.** CLAUDE.md
documented Groq's `finish_reason` behaviour and react.py implemented it correctly,
and six of eight call sites still had no handling - including the one written two
days after reading the rule. Prose aimed at whoever writes the next call site is not
a control. `llm.py` is the same rule as code that cannot be skipped. This is the
same finding as "fix the class, not the instance" and "prefer a control in code to
an instruction in a prompt"; all three are one thing.
