# STATE.md - Where Things Stand Right Now

**Rewrite this file. Never append to it.** History belongs in git log and
GAMEPLAN's Progress Log. This file answers one question: what is true today?

Last updated: **2026-08-02**

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

**Only three items left in the whole queue.** R0, R1, R2, Phase C except C.5, and
R3.0/R3.1/R3.4 are all done and deployed.

- **R3.2** startup smoke test on routing fast paths and model config. Pure logic,
  no API calls, testable locally. Smallest of the three.
- **R3.3** injection screening. Respecced 2026-08-02 because the original guarded
  the wrong boundary - it screened the user's own messages, but the user is the
  only authorised sender. The exposure is content React fetches and feeds back
  into its own conversation. Read the rewritten step before building.
- **C.5** in-bot heartbeat for gateway liveness. Needs one free external account
  from the user, so it cannot be done unattended.

Nothing blocks anything else. Start with whichever suits the session.

## What R2 and R3.0 actually changed, measured

- "What's new IGOR?" went from four minutes and six escalating 429 backoffs,
  answered with a five-section status report, to an instant reply in prose.
- 429s dropped from a storm inside a single turn to one in twenty minutes.
- Research went from recording nothing at all - two runs, zero findings - to three
  iterations completing with 10 of 10 findings carrying source URLs and zero
  fabricated "nobody is doing X" claims.
- Chat now works *during* a research run. Confirmed live: `Router: CHAT -> Direct`
  fired mid-run without disturbing it.

## Known broken

1. Still on Oracle Always Free, so an entitlement change can terminate the
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
