# STATE.md - Where Things Stand Right Now

**Rewrite this file. Never append to it.** History belongs in git log and
GAMEPLAN's Progress Log. This file answers one question: what is true today?

Last updated: **2026-08-08**

---

## Running

- **Host:** `129.80.181.77` - Oracle `VM.Standard.E2.1.Micro`, x86_64,
  US-ASHBURN-AD-1, Always Free, 1 OCPU / 956 MB RAM + 2 GB swapfile
- **OS:** Ubuntu 22.04.5, Python 3.10.12, service user `igor`, root `/opt/igor`
- **Services:** `igor` and `igor-watchdog`, both enabled and active
- **Status:** online, confirmed responding on Discord

## Code

- Local, `origin/master`, and server `/opt/igor` are all in sync. Server was
  deployed and restarted 2026-08-02; both services verified active afterwards.
- The server tree was copied off the rescued disk rather than cloned, but its
  `.git` tracks the same remote and pulls normally.

## Next action

Restoration finished 2026-08-03. Everything since is forward work and bug fixes
found by using the system.

**The user's direction, set 2026-08-08: a fully autonomous, self improving
assistant.** GAMEPLAN Phase A specs the path, researched then audited against its
own sources. Build order, which is not the obvious one:

1. **A.2 correction logging** - twenty lines, zero model calls, and it starts
   accruing value immediately. Correction data has a lead time; every day it is not
   collected is signal lost.
2. **S.1 health-gated deploy with rollback** - before anything runs unattended
3. **V.1 improvement loop** - batch review over a corpus that has accumulated
4. **A.1 goals + A.3 decomposition** - together, once something consumes them

Also open and worth doing: **T.1** (tests into the repo - every test written this
week was a scratchpad throwaway) and **M.3** (settle the rule that any file stating
verifiable facts needs an owner, after five files drifted the same way).

**Decided against, do not silently revisit: self-modification (S.2).** No proven
method exists, and three independent research efforts all produced metric-gaming.
Evidence is in GAMEPLAN Phase S.

**IGOR stays a personal tool.** An app-store version was considered and set aside,
not ruled out. Do not build toward multi-tenancy; do not deepen single-user
coupling either.

## Known broken

1. **The safety stack does not execute, and self-modification cannot deploy.**
   Found 2026-08-03. `igor.service` runs `main.py` directly, so `start.sh` is never
   invoked and Layers 1 and 3 (compile check, crash revert) do not run. The
   watchdog is active but has no sudoers entry, so its `sudo systemctl restart
   igor` fails - zero restarts in its entire journal. React can therefore write
   code and write the restart sentinel, but nothing loads the change. What does
   protect the system: main._smoke_test, systemd Restart=always, and the backup
   script's crash-loop alert. Nothing reverts a bad change. See ARCHITECTURE.md.
   **Needs a decision before fixing** - see below.
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

## Unverified on the server

Nothing outstanding. Everything deployed on 2026-08-08 was tested in Discord by the
user, and the digest fix was verified against the source article.

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
