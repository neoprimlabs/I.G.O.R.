# STATE.md - Where Things Stand Right Now

**Rewrite this file. Never append to it.** History belongs in git log and
GAMEPLAN's Progress Log. This file answers one question: what is true today?

Last updated: **2026-08-03**

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

Restoration is finished. R0, R1, R2, R3 and Phase C except C.5 are all done. The
queue is now forward work, in GAMEPLAN phases M, T, V, I and X.

**Next: T.1 - tests into the repo.** Roughly eight test files were written on
2026-08-02 and 08-03 and every one was a throwaway in the session scratchpad.
Between them they caught the watchdog's `2>&1` bug, the router classifying opinion
questions as CONFIG, reasoning budgets below the empty-content floor, a missing
stdlib import that would have crashed a deploy, the full injection quarantine, and
the search date edge cases. None of it is reproducible and nothing prevents any of
it regressing. A routing regression was caused and caught by an ad-hoc test on
08-03, which is the argument.

Then **V.1**, the improvement loop with sign-off buckets. Highest value item on the
list: the critic is off and skills_react.md is gone, so IGOR cannot currently learn
anything, and self-improvement is the stated vision.

M.1 and M.2 are done. **M.3 matters more than it looks** - four files have now
drifted the same way (CLAUDE.md, skills_react.md, agents.md/projects.md, and
ARCHITECTURE.md itself). The rule to settle is that any file stating verifiable
facts needs an owner and a trigger, or it eventually lies to the user.

**The user has decided IGOR stays a personal tool.** An app-store version was
considered and set aside, not ruled out. Do not build toward multi-tenancy, but do
not deepen single-user coupling either: memory and context are the only layers
that would be expensive to retrofit.

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
2. Monitoring detects a dead process, not a dead gateway. If IGOR is running and
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

One thing was deployed but never smoke tested, because the session ended first:
Direct's rule against reporting on IGOR was broadened after it invented a status
report ("in the testing phase, promising results"). Ask "How is the deep research
feature working out?" - it should say it cannot see that from a conversation and
offer to check, not describe how the feature is performing.

## Recent

- **2026-07-24ish:** Oracle cut the Always Free A1 allowance from 4 OCPU/24 GB
  to 2 OCPU/12 GB and terminated the instance, which was over the new limit.
- **2026-07-31:** Data recovered. A1 capacity was exhausted in all three Ashburn
  ADs; recovery went through a region-scoped boot volume backup restored into
  AD-1, then an E2.1.Micro with the clone attached as a data disk. Nothing lost.
- **2026-08-02:** Long session. Docs restructured into rules/facts/plan/state,
  backups and alerting built, Phase R2 completed, R3.0 designed against published
  practice rather than patched, and most of Phase C cleared.

## Lesson worth keeping from 2026-08-02

Every real bug that day was found by running the thing, not by reading it. The
watchdog's `2>&1` failure, the router reading opinion questions as CONFIG, empty
reasoning budgets, the "deep research" misroute, and Direct inventing a status
report were all invisible on the page and obvious on first contact.

Related: fixing the exact phrasing that failed, rather than the class of failure,
produced the same bug twice in one evening. Direct's "nothing has changed" was
patched, and it returned as "it is in the testing phase". Ask what class a bug
belongs to before fixing the instance.
