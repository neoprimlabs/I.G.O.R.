"""IGOR's own routines, retimed when the user asks: Hermes' cronjob update, for jobs built into IGOR.

Hermes keeps every recurring job in ~/.hermes/cron/jobs.json, and its agent's cronjob tool changes one's
schedule at once, with no restart and no approval. IGOR's digest and watches are built in, registered by
monitor.setup. Until 2026-11-08 only the digest's time could change, through ConfigEdit, and only at the
next restart, which IGOR cannot do itself. Asked to run the price watch less often, ConfigEdit could have
written a line nothing read and answered "Updated".

The user's timings live in schedule_config.md, a section a routine, which monitor.setup reads at start. A
change is written there and applied to the running scheduler at once, from the next run on. Only these
five: the heartbeat, the scheduled-message tick, the board and the overnight tick are IGOR's own machinery,
and retiming them would break what depends on them.
"""

import logging
import os
import re
from datetime import timezone
from typing import Optional

import config

logger = logging.getLogger(__name__)

# The scheduler's job id, what the user calls it, and its timing when the file says nothing: a daily local
# time, or hours between runs.
ROUTINES = {
    "morning_digest": {"name": "morning digest", "at": (10, 0)},
    "job_watch": {"name": "job watch", "at": (11, 30)},
    "gig_watch": {"name": "gig watch", "at": (11, 45)},
    "price_watch": {"name": "price watch", "every": 2},
    "stock_watch": {"name": "stock watch", "every": 1},
}
MIN_HOURS = 1
MAX_HOURS = 168
_TIME = re.compile(r"^time:\s*(\d{1,2}):(\d{2})\b(.*)$", re.IGNORECASE)
_EVERY = re.compile(r"^every:\s*(\d+)\s*(h|hrs?|hours?|d|days?)\s*$", re.IGNORECASE)
_HHMM = re.compile(r"^\s*(\d{1,2}):(\d{2})\s*$")


def _path():
    return config.MEMORY_DIR / "schedule_config.md"


def _text() -> str:
    try:
        return _path().read_text(encoding="utf-8")
    except OSError:
        return ""


def _section_lines(text: str, routine: str) -> list[str]:
    lines, inside = [], False
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith("## "):
            inside = stripped[3:].strip() == routine
        elif inside:
            lines.append(stripped)
    return lines


def setting(routine: str, text: Optional[str] = None) -> dict:
    """{"at": (hour, minute, zone)} or {"every": hours}: the file's, or the default when it has none that
    reads. A time is the user's own clock unless the line says UTC, as the digest's first line once did."""
    import clock
    spec = ROUTINES[routine]
    for line in _section_lines(_text() if text is None else text, routine):
        if "at" in spec:
            found = _TIME.match(line)
            if found and 0 <= int(found.group(1)) <= 23 and 0 <= int(found.group(2)) <= 59:
                zone = timezone.utc if "utc" in found.group(3).lower() else clock.USER_TZ
                return {"at": (int(found.group(1)), int(found.group(2)), zone)}
        else:
            found = _EVERY.match(line)
            if found:
                hours = int(found.group(1)) * (24 if found.group(2).lower().startswith("d") else 1)
                if MIN_HOURS <= hours <= MAX_HOURS:
                    return {"every": hours}
    if "at" in spec:
        return {"at": (*spec["at"], clock.USER_TZ)}
    return {"every": spec["every"]}


def trigger(routine: str, text: Optional[str] = None):
    from apscheduler.triggers.cron import CronTrigger
    from apscheduler.triggers.interval import IntervalTrigger
    timing = setting(routine, text)
    if "every" in timing:
        return IntervalTrigger(hours=timing["every"])
    hour, minute, zone = timing["at"]
    return CronTrigger(hour=hour, minute=minute, timezone=zone)


def describe(routine: str, text: Optional[str] = None) -> str:
    import clock
    timing = setting(routine, text)
    if "every" in timing:
        hours = timing["every"]
        if hours % 24 == 0:
            return "every day" if hours == 24 else f"every {hours // 24} days"
        return "every hour" if hours == 1 else f"every {hours} hours"
    hour, minute, zone = timing["at"]
    words = f"{hour % 12 or 12}:{minute:02d} {'AM' if hour < 12 else 'PM'}"
    return f"daily at {words}" + (" UTC" if zone is timezone.utc and clock.USER_TZ is not timezone.utc else "")


def _next_run(routine: str) -> str:
    from agents import monitor
    scheduler = monitor._scheduler
    job = scheduler.get_job(routine) if scheduler is not None and scheduler.running else None
    if job is None or job.next_run_time is None:
        return ""
    import dashboard
    return dashboard._when(job.next_run_time)


def listing() -> str:
    rows = []
    for routine, spec in ROUTINES.items():
        upcoming = _next_run(routine)
        rows.append(f"- {routine} ({spec['name']}): {describe(routine)}" + (f", next {upcoming}" if upcoming else ""))
    return ("IGOR's routines the user can retime:\n" + "\n".join(rows)
            + f"\nWatches take every_hours, {MIN_HOURS} to {MAX_HOURS}; the others take a daily time, at.")


def _with_line(text: str, routine: str, line: str) -> str:
    """The file with this routine's setting replaced by line, every other line as it was."""
    lines = text.splitlines()
    start = next((i for i, l in enumerate(lines) if l.strip() == f"## {routine}"), None)
    if start is None:
        body = "\n".join(lines).rstrip() if lines else "# Schedule Config"
        return f"{body}\n\n## {routine}\n{line}\n"
    end = next((i for i in range(start + 1, len(lines)) if lines[i].strip().startswith("## ")), len(lines))
    kept = [l for l in lines[start + 1:end] if not (_TIME.match(l.strip()) or l.strip().lower().startswith("every:"))]
    while kept and not kept[-1].strip():
        kept.pop()
    after = lines[end:]
    return "\n".join(lines[:start + 1] + [line] + kept + ([""] if after else []) + after).rstrip() + "\n"


def _write(text: str) -> None:
    import checkpoints
    path = _path()
    checkpoints.before_write(path, "routine timing")
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(text, encoding="utf-8")
    os.replace(tmp, path)
    checkpoints.after_write(path)


def change(routine: str, every_hours: Optional[int] = None, at: Optional[str] = None) -> str:
    """Set one routine's timing, save it and apply it to the running scheduler."""
    spec = ROUTINES.get(routine or "")
    if spec is None:
        return f"[routine_schedule refused: no routine {routine!r}. These can be retimed: {', '.join(ROUTINES)}]"
    if "every" in spec:
        if at:
            return (f"[routine_schedule refused: the {spec['name']} runs at intervals, so it takes every_hours, "
                    f"not a time of day]")
        if not isinstance(every_hours, int) or isinstance(every_hours, bool) or not MIN_HOURS <= every_hours <= MAX_HOURS:
            return f"[routine_schedule refused: every_hours must be a whole number from {MIN_HOURS} to {MAX_HOURS}]"
        line = f"every: {every_hours} hours"
    else:
        if every_hours is not None:
            return f"[routine_schedule refused: the {spec['name']} runs once a day, so it takes at, a time like 09:30]"
        found = _HHMM.match(at or "")
        if not found or not (0 <= int(found.group(1)) <= 23 and 0 <= int(found.group(2)) <= 59):
            return "[routine_schedule refused: at must be a local time in 24 hours, HH:MM, such as 09:30 or 18:00]"
        line = f"time: {int(found.group(1)):02d}:{found.group(2)} local"
    before = describe(routine)
    new = _with_line(_text(), routine, line)
    if describe(routine, new) == before and setting(routine, new) == setting(routine):
        return f"The {spec['name']} already runs {before}. Nothing changed."
    try:
        _write(new)
    except OSError as e:
        logger.error("Could not save the %s's timing - %s: %s", routine, type(e).__name__, e)
        return f"[routine_schedule failed: the timing could not be saved ({type(e).__name__}). Nothing changed]"
    logger.info("Routine %s retimed: %s -> %s", routine, before, describe(routine))
    applied = _apply(routine)
    after = describe(routine)
    if not applied:
        return (f"Saved: the {spec['name']} will run {after} (it was {before}). The scheduler is not running "
                f"here, so it takes effect when IGOR next starts.")
    upcoming = _next_run(routine)
    return f"Done: the {spec['name']} now runs {after} (it was {before})." + (f" Next run: {upcoming}." if upcoming else "")


def _apply(routine: str) -> bool:
    """Retime the live job. False when there is no running scheduler or no such job, as in a test or a
    throwaway copy: the file still holds the change for the next start."""
    from agents import monitor
    scheduler = monitor._scheduler
    if scheduler is None or not scheduler.running or scheduler.get_job(routine) is None:
        return False
    scheduler.reschedule_job(routine, trigger=trigger(routine))
    return True


def apply_all() -> int:
    """Retime every routine from the file, as after a rollback restores it. How many were applied."""
    return sum(_apply(routine) for routine in ROUTINES)


def run_tool(inputs: dict) -> str:
    action = (inputs.get("action") or "").strip().lower()
    if action == "list":
        return listing()
    if action == "change":
        return change(inputs.get("routine") or "", inputs.get("every_hours"), inputs.get("at"))
    return "[routine_schedule refused: action is list or change]"
