# STATE.md - Where Things Stand Right Now

**Rewrite this file. Never append to it.** History belongs in git log and
GAMEPLAN's Progress Log. This file answers one question: what is true today?

Last updated: **2026-08-01**

---

## Running

- **Host:** `129.80.181.77` - Oracle `VM.Standard.E2.1.Micro`, x86_64,
  US-ASHBURN-AD-1, Always Free, 1 OCPU / 956 MB RAM + 2 GB swapfile
- **OS:** Ubuntu 22.04.5, Python 3.10.12, service user `igor`, root `/opt/igor`
- **Services:** `igor` and `igor-watchdog`, both enabled and active
- **Status:** online, confirmed responding on Discord

## Code

- Local `master` and `origin/master`: **88098d5**
- Server `/opt/igor`: **9bff009**, one commit behind. The next deploy pulls it
  forward. The server tree was copied off the rescued disk, not cloned.

## Next action

**GAMEPLAN R2.0** - add the in-turn token budget guard to `react.handle`. It is
described in GAMEPLAN's Progress Log but has never been promoted to a numbered
step. Do that first, then R2.1 (Direct agent), then R2.2 (router).

R0.1, R1.1 and R1.2 are done. Everything from R2 onward is not started.

## Known broken

1. **No external heartbeat.** The July outage went unnoticed for roughly two
   days. IGOR exposes no HTTP port, so a pull-based uptime monitor cannot check
   application health. Needs a push-based dead-man's-switch, ideally emitted
   from inside the APScheduler loop so it proves the bot is alive rather than
   just the box.
2. Still on Oracle Always Free, so an entitlement change can terminate the
   instance again. See ARCHITECTURE.md, the safety stack does not cover this.

## Backups

`scripts/backup_memory.ps1` pulls `memory/` and `.env` to
`C:\Dev\IGOR_backup\` as dated archives, keeping the last 30. Registered as the
daily Windows Scheduled Task "IGOR memory backup" at 3am. Pull-based because the
server has no git push credentials. Run on demand with
`Start-ScheduledTask -TaskName 'IGOR memory backup'`.

Caveat: it only runs when this machine is on. That is the remaining gap.

## Recent

- **2026-07-24ish:** Oracle cut the Always Free A1 allowance from 4 OCPU/24 GB
  to 2 OCPU/12 GB and terminated the instance, which was over the new limit.
- **2026-07-31:** Data recovered. A1 capacity was exhausted in all three Ashburn
  ADs; recovery went through a region-scoped boot volume backup restored into
  AD-1, then an E2.1.Micro with the clone attached as a data disk. Nothing lost.
