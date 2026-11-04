"""Messages to the user at a later time.

React's scheduled_message tool writes them to memory/scheduled.json, and a monitor
job checks that file every minute and sends whatever is due.

An entry carries either content, the exact words, fixed when scheduled and free to
deliver; or a brief, a few words about what the message is for, with the words
written at delivery by compose.write: an agent run with read-only tools, as Hermes
runs a job when it fires. React can only give a brief (run_tool); content remains
for callers in code. If writing fails the brief is sent as plain text, because a
message that arrives plainly beats one that vanishes.

As Hermes' cron jobs, an entry can run a script first (prerun.py), which can end the
run before any model call; send what the script prints with no model at all
(script_only, Hermes' no_agent); read the latest output of other entries
(context_from); and keep its result without sending it (keep_only, Hermes' deliver
local), so one entry can gather for another. Hermes does not wait for an upstream job
in the same tick; here an entry that reads others goes last.

A polled file rather than one APScheduler job per message, because APScheduler
3.11.3 silently drops a date job more than one second late (measured on the server,
2026-09-12). A message due during a deploy would vanish. Here an overdue message is
simply due at the next check, so restarts need no catch-up code.

The model supplies local wall-clock time or a number of minutes. Every conversion
and every addition happens here, in code.
"""

import json
import logging
import os
import random
import re
import secrets
from datetime import datetime, time, timedelta, timezone
from typing import Awaitable, Callable, Optional

import clock
import config
import prerun

logger = logging.getLogger(__name__)

_MAX_PENDING = 20
_MAX_AHEAD = timedelta(days=30)
_MAX_ATTEMPTS = 10
_LATE_AFTER = timedelta(minutes=5)
_AT_FORMAT = "%Y-%m-%d %H:%M"
_BARE_TIME = re.compile(r"^\d{1,2}:\d{2}$")

# Local hour ranges the user can name. The code picks the minute, never the model:
# asked for "some random times", it picked one number once and called it random.
# Interruptions at random moments also measure worse than ones at a natural break,
# so the window stays coarse and the variation lives inside it.
_WINDOWS = {
    "morning": (10, 12),
    "afternoon": (12, 17),
    "evening": (17, 23),
    "tomorrow": (10, 23),
}
_EARLIEST_AHEAD = timedelta(minutes=5)
_SAME_SLOT = timedelta(minutes=5)
_REPEATS = ("hourly", "daily", "weekdays", "weekly")
_REPEAT_WORDS = {"hourly": "every hour", "daily": "every day", "weekdays": "every weekday",
                 "weekly": "every week"}
# As Hermes' "every 2h". A run with a script costs nothing until the script finds something, so 5
# minutes; one without spends a writer call, about 1800 tokens, against a refill of 8300 an hour.
_EVERY = re.compile(r"^(?:every\s+)?(?:(\d+)\s*)?(m|mins?|minutes?|h|hrs?|hours?|d|days?|half an? hour)$")
_SAID = {"every hour": "hourly", "every day": "daily", "every weekday": "weekdays", "every week": "weekly"}
_MIN_WITH_SCRIPT = 5
_MIN_WITHOUT_SCRIPT = 180
# Continuity, as Hermes' cron: each run of a repeating message is shown what the
# series already said. Live 2026-10-16, a weekday "practical idea" series got one
# specific idea as its brief, so every weekday would have repeated it. Hermes passes
# the latest output; three, because Thursday repeating Tuesday is the same failure.
_KEEP_RECENT = 3
_RECENT_CHARS = 600
_JOB_FIELDS = ("script", "script_only", "context_from", "keep_only")
_MAX_SOURCES = 3
_OUTPUT_CHARS = 1500
_KEEP_OUTPUT = timedelta(days=30)


class _Unreadable(Exception):
    pass


def _path():
    return config.MEMORY_DIR / "scheduled.json"


def _load() -> list[dict]:
    path = _path()
    if not path.exists():
        return []
    try:
        entries = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as e:
        raise _Unreadable(f"{type(e).__name__}: {e}") from e
    if not isinstance(entries, list) or not all(
        isinstance(e, dict) and all(isinstance(e.get(k), str) for k in ("id", "due", "content"))
        for e in entries
    ):
        raise _Unreadable("not a list of entries with id, due and content")
    return entries


def _save(entries: list[dict]) -> None:
    # Write then rename, so a crash mid-write leaves the previous file, not half of one.
    path = _path()
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(entries, indent=2), encoding="utf-8")
    os.replace(tmp, path)


def _local(due_iso: str) -> str:
    return f"{datetime.fromisoformat(due_iso).astimezone(clock.USER_TZ):%a %Y-%m-%d %H:%M %Z}"


def _pick_in_window(window: str, now: datetime) -> Optional[datetime]:
    """A random minute inside a named window, rolled to tomorrow if it has passed."""
    if window not in _WINDOWS:
        return None
    start_hour, end_hour = _WINDOWS[window]
    local = now.astimezone(clock.USER_TZ)
    day = local.date() + timedelta(days=1) if window == "tomorrow" else local.date()
    for _ in range(2):
        start = datetime.combine(day, time(start_hour), tzinfo=clock.USER_TZ)
        end = datetime.combine(day, time(end_hour), tzinfo=clock.USER_TZ)
        earliest = max(start, local + _EARLIEST_AHEAD)
        minutes = int((end - earliest).total_seconds() // 60)
        if minutes > 0:
            return earliest + timedelta(minutes=random.randint(0, minutes - 1))
        day = day + timedelta(days=1)
    return None


def _human(due: datetime, now: datetime) -> str:
    """How a person says a time out loud. React reads this back to the user."""
    local = due.astimezone(clock.USER_TZ)
    today = now.astimezone(clock.USER_TZ).date()
    clock_time = local.strftime("%I:%M %p").lstrip("0")
    days = (local.date() - today).days
    if days == 0:
        day = "today"
    elif days == 1:
        day = "tomorrow"
    else:
        # The date, always. Given only "Monday at 10:00 AM", React told the user
        # "Monday, October 23" for Monday the 19th (2026-10-16): a model fills a
        # missing date in by itself, so the tool must not leave one missing.
        day = f"{local:%A}, {local:%B} {local.day}"
    return day + " at " + clock_time


def _due(at: Optional[str], in_minutes: Optional[int], window: Optional[str],
         now: datetime) -> tuple[Optional[datetime], str]:
    """The UTC due time for whichever one of at, in_minutes, window was given,
    or None and what is wrong with it."""
    if at is not None:
        try:
            # 2026-11-01, live: "every day at 8:50am" came as at "08:50", and the refusal cost a
            # whole model call to learn a date nobody had asked for.
            if _BARE_TIME.match(at.strip()):
                local = now.astimezone(clock.USER_TZ)
                due = datetime.combine(local.date(), datetime.strptime(at.strip(), "%H:%M").time(),
                                       tzinfo=clock.USER_TZ)
                if due <= now:
                    due = datetime.combine(local.date() + timedelta(days=1), due.time(), tzinfo=clock.USER_TZ)
            else:
                due = datetime.strptime(at.strip(), _AT_FORMAT).replace(tzinfo=clock.USER_TZ)
        except ValueError:
            return None, f"could not read {at!r}. Use local time as YYYY-MM-DD HH:MM."
    elif window is not None:
        due = _pick_in_window(window, now)
        if due is None:
            return None, f"{window!r} is not a window. Use morning, afternoon, evening or tomorrow."
    elif isinstance(in_minutes, bool) or not isinstance(in_minutes, int) or in_minutes < 1:
        return None, "in_minutes must be a whole number of at least 1."
    else:
        due = now + timedelta(minutes=in_minutes)
    due = due.astimezone(timezone.utc).replace(microsecond=0)

    if due <= now:
        return None, f"{_local(due.isoformat())} has already passed. It is now {clock.time_line(now)}."
    if due - now > _MAX_AHEAD:
        return None, "messages can be scheduled at most 30 days ahead."
    return due, ""


def _repeat(value: Optional[str]) -> tuple[Optional[str], str]:
    """(repeat, problem). "" means one-off; None means not given."""
    if value is None:
        return None, ""
    value = value.strip().lower()
    if value in ("", "none", "once", "off"):
        return "", ""
    value = _SAID.get(value, value)
    if value in _REPEATS:
        return value, ""
    every = _EVERY.match(value)
    if every and (every.group(1) or value.endswith("half an hour") or value.endswith("half a hour")):
        unit = every.group(2)[0]
        minutes = 30 if unit == "h" and "half" in every.group(2) else int(every.group(1)) * {"m": 1, "h": 60, "d": 1440}[unit]
        if 0 < minutes <= 30 * 1440:
            return ("hourly" if minutes == 60 else f"every {minutes}m"), ""
    return None, ("repeat must be daily, weekdays, weekly, an interval such as every 6 hours, or none to stop "
                  "repeating.")


def _interval(repeat: Optional[str]) -> Optional[int]:
    """Minutes between runs, for a repeat that goes by the clock's minutes and not its hours."""
    if repeat == "hourly":
        return 60
    return int(repeat[6:-1]) if repeat and repeat.startswith("every ") else None


def _repeat_words(repeat: str) -> str:
    minutes = _interval(repeat)
    if repeat in _REPEAT_WORDS or minutes is None:
        return _REPEAT_WORDS.get(repeat, repeat)
    if minutes % 1440 == 0:
        return f"every {minutes // 1440} days" if minutes > 1440 else "every day"
    return f"every {minutes // 60} hours" if minutes % 60 == 0 else f"every {minutes} minutes"


def _with_until(brief: str, until: Optional[str]) -> str:
    """Hermes' --until, said where the run reads it. Live 2026-11-01, "and stop once it has" was
    left out of a brief by a model told it could say so there; a field of its own is harder to skip."""
    until = (until or "").strip().rstrip(".")
    return f"{brief} Stop repeating once this has happened: {until}." if brief and until else brief


def _next_occurrence(due: datetime, repeat: str, now: datetime) -> datetime:
    """The next local-clock occurrence after now. Stepping the local calendar, not
    adding 24h in UTC, keeps 9:00 at 9:00 across a daylight-saving change, and
    skipping past now means a restart sends one late message, not a backlog."""
    minutes = _interval(repeat)
    if minutes:
        step = timedelta(minutes=minutes)
        return (due + ((now - due) // step + 1) * step).replace(microsecond=0)
    local = due.astimezone(clock.USER_TZ)
    wall, day = local.time(), local.date()
    step = timedelta(days=7 if repeat == "weekly" else 1)
    while True:
        day += step
        if repeat == "weekdays" and day.weekday() >= 5:
            continue
        nxt = datetime.combine(day, wall, tzinfo=clock.USER_TZ).astimezone(timezone.utc)
        if nxt > now:
            return nxt.replace(microsecond=0)


def _onto_weekday(due: datetime) -> datetime:
    local = due.astimezone(clock.USER_TZ)
    while local.weekday() >= 5:
        local = datetime.combine(local.date() + timedelta(days=1), local.time(), tzinfo=clock.USER_TZ)
    return local.astimezone(timezone.utc)


def _outputs_path():
    return config.MEMORY_DIR / "scheduled_outputs.json"


def _outputs() -> dict:
    """What each entry last produced, for context_from. An entry that sent once is gone from
    scheduled.json by then, so this is its own file. A missing or bad file is no outputs."""
    try:
        data = json.loads(_outputs_path().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    if not isinstance(data, dict):
        return {}
    return {k: v for k, v in data.items()
            if isinstance(v, dict) and isinstance(v.get("at"), str) and isinstance(v.get("text"), str)}


def _remember(entry_id: str, text: str, now: datetime) -> None:
    """Never raises: the message has already gone."""
    try:
        kept = {k: v for k, v in _outputs().items()
                if now - datetime.fromisoformat(v["at"]) < _KEEP_OUTPUT}
        kept[entry_id] = {"at": now.isoformat(), "text": text[:_OUTPUT_CHARS]}
        path = _outputs_path()
        tmp = path.with_name(path.name + ".tmp")
        tmp.write_text(json.dumps(kept, indent=1), encoding="utf-8")
        os.replace(tmp, path)
    except (OSError, ValueError) as e:
        logger.error("Could not keep the output of %s - %s: %s", entry_id, type(e).__name__, e)


def _material(script: Optional[str], printed: str, sources: Optional[list]) -> str:
    """What a run has to work with, dated so a stale upstream reads as stale. An upstream with
    nothing yet is said outright, or the model fills the gap by itself."""
    parts = [f"What {script} printed just now:\n{printed}"] if printed else []
    outputs = _outputs() if sources else {}
    for source in sources or []:
        got = outputs.get(source)
        parts.append(f"The latest from scheduled message {source}, {_local(got['at'])}:\n{got['text']}"
                     if got else f"Scheduled message {source} has no output yet.")
    return "\n\n".join(parts)


def _job_problem(entry: dict, known: set) -> str:
    """What is wrong with these job settings together, or ""."""
    script = entry.get("script")
    if script:
        why = prerun.problem(script)
        if why:
            return why
    if entry.get("script_only") and not script:
        return "script_only needs a script."
    if not entry.get("script_only") and not entry.get("brief") and not entry.get("content"):
        return "give a brief, or script_only to send what the script prints."
    minutes = _interval(entry.get("repeat"))
    if minutes and not script and minutes < _MIN_WITHOUT_SCRIPT:
        return ("only a message with a script can repeat more often than every 3 hours: any other run spends the "
                "daily budget each time.")
    if minutes and minutes < _MIN_WITH_SCRIPT:
        return "a script can repeat every 5 minutes at the shortest."
    sources = entry.get("context_from") or []
    if sources and entry.get("script_only"):
        return "a script_only message has no model to read context_from."
    if len(sources) > _MAX_SOURCES:
        return f"context_from takes at most {_MAX_SOURCES} ids."
    unknown = [s for s in sources if s not in known or s == entry.get("id")]
    if unknown:
        return f"context_from names {', '.join(unknown)}, which is not another scheduled message. List them to find it."
    return ""


def _known_ids(entries: list[dict]) -> set:
    return {e["id"] for e in entries} | set(_outputs())


def _job_words(entry: dict, ids: bool = True) -> str:
    """What else a message does. ids is for the model's list: the user reads the rest, often
    aloud, and live an id went out under a reply."""
    words = []
    if entry.get("script_only"):
        words.append(f"sends what {entry['script']} prints, with no model")
    elif entry.get("script"):
        words.append(f"runs {entry['script']} first")
    if entry.get("context_from"):
        words.append("reads the latest from " + (", ".join(entry["context_from"]) if ids else "another scheduled message"))
    if entry.get("keep_only"):
        words.append("kept, not sent")
    return "; ".join(words)


def add(content: str = "", at: Optional[str] = None, in_minutes: Optional[int] = None,
        window: Optional[str] = None, brief: Optional[str] = None,
        now: Optional[datetime] = None, repeat: Optional[str] = None,
        script: Optional[str] = None, script_only: Optional[bool] = False,
        context_from: Optional[list] = None, keep_only: Optional[bool] = False,
        times: Optional[int] = None, until: Optional[str] = None) -> str:
    now = now or datetime.now(timezone.utc)
    content = (content or "").strip()
    brief = _with_until((brief or "").strip(), until)
    # With no brief there is nothing for a model to write. Refusing this, live, drew a made-up
    # brief, and with it a model call on a job meant to cost nothing.
    script_only = script_only or bool(script and not brief and not content)
    job = {k: v for k, v in (("script", script), ("script_only", bool(script_only)),
                             ("context_from", context_from), ("keep_only", bool(keep_only))) if v}
    if content and brief:
        # 2026-10-14: asked for "thought provoking material" at 1am, the model sent
        # both, was refused, and retried keeping the words it had pre-written. The
        # message arrived as text the user had already read in chat. A model that
        # writes a brief has said what the message is for; the words it drafted
        # alongside are its own, not dictated, so the brief wins.
        logger.info("Scheduled message given both content and brief, keeping the brief")
        content = ""
    if not content and not brief and not job.get("script_only"):
        return ("Not scheduled: give either content (the exact words to send) or brief "
                "(what the message is about, written when it sends).")
    if sum(x is not None for x in (at, in_minutes, window)) != 1:
        return "Not scheduled: give exactly one of at, in_minutes or window."
    due, problem = _due(at, in_minutes, window, now)
    if problem:
        return f"Not scheduled: {problem}"
    repeat, problem = _repeat(repeat)
    if problem:
        return f"Not scheduled: {problem}"
    repeat = repeat or ""
    if repeat == "weekdays":
        due = _onto_weekday(due)

    try:
        entries = _load()
    except _Unreadable as e:
        logger.error("scheduled.json is unreadable, refusing to add - %s", e)
        return "Not scheduled: the scheduled message store is unreadable. Nothing was changed."
    problem = _job_problem({**job, "brief": brief, "content": content, "repeat": repeat}, _known_ids(entries))
    if problem:
        return f"Not scheduled: {problem}"
    # 2026-10-16, live: told to "draft something new at the scheduled time", React
    # added a second 1am entry beside the first, though the add result listed it and
    # said to cancel. Two messages minutes apart are never what was meant. Merging
    # rather than replacing, because "pills at 9" and "call mom at 9" are two real
    # requests and replacing would drop one.
    if brief and not repeat and not job:
        for e in entries:
            if (e.get("brief") and not e.get("content") and not e.get("failed")
                    and not e.get("repeat") and not any(e.get(k) for k in _JOB_FIELDS)
                    and abs(datetime.fromisoformat(e["due"]) - due) <= _SAME_SLOT):
                e["brief"] = (f"{brief} (This updates or adds to an earlier request for the "
                              f"same time: {e['brief']})")
                _save(entries)
                logger.info("Merged a new brief into scheduled message %s", e["id"])
                when = _human(datetime.fromisoformat(e["due"]), now)
                return (f"Okay, {when}. Merged with the message already set for then, "
                        "so one message will send, covering both.")

    if len(entries) >= _MAX_PENDING:
        return f"Not scheduled: {_MAX_PENDING} messages are already pending. Cancel one first."

    entry = {"id": secrets.token_hex(3), "due": due.isoformat(), "content": content,
             "brief": brief, "created": now.isoformat(), "attempts": 0, "repeat": repeat, **job}
    if times and repeat:
        entry["times_left"] = times
    others = list(entries)
    entries.append(entry)
    _save(entries)
    logger.info("Scheduled message %s for %s", entry["id"], entry["due"])
    reply = f"Okay, {_human(due, now)}" + (f", then {_repeat_words(repeat)}" if repeat else "")
    reply += f", {times} times in all." if times and repeat else "."
    if job:
        reply += f" It {_job_words(entry, ids=False)}."
    # On its own line: the first line is shown to the user under the reply. The next call has to
    # name this entry, and nothing else tells the model its id.
    if job.get("keep_only"):
        reply += f"\nFor you, so do not repeat it to the user: give context_from this id on the message that reads it: {entry['id']}"
    # 2026-10-14: "I said 1am" added a second entry and left the misread 10am one in
    # place, and both sent. The model never saw it - nothing showed what was pending.
    if others:
        reply += ("\nAlso pending, ids are internal and not for the user:\n" + _lines(others)
                  + "\nIf the new message replaces one of these, cancel the old one now"
                  " or both will send. To change a pending message, use update on its id"
                  " instead of adding.")
    return reply


def _lines(entries: list[dict]) -> str:
    lines = []
    for e in sorted(entries, key=lambda e: e["due"]):
        state = "  FAILED to send - cancel to clear" if e.get("failed") else ""
        state += f"  repeats {_repeat_words(e['repeat'])}" if e.get("repeat") else ""
        state += f", {e['times_left']} runs left" if e.get("times_left") else ""
        state += "  PAUSED" if e.get("paused") else ""
        state += f"  {_job_words(e)}" if _job_words(e) else ""
        summary = (e.get("content") or e.get("brief") or e.get("script") or "")[:80]
        lines.append(f"{e['id']}  {_local(e['due'])}{state}  {summary}")
    return "\n".join(lines)


def list_pending() -> str:
    try:
        entries = _load()
    except _Unreadable:
        return "The scheduled message store is unreadable."
    scripts = prerun.available()
    extra = f"\nScripts a message can run first: {', '.join(scripts)}" if scripts else ""
    if not entries:
        return "No scheduled messages." + extra
    return _lines(entries) + extra


def cancel(entry_id: str) -> str:
    entry_id = (entry_id or "").strip()
    try:
        entries = _load()
    except _Unreadable:
        return "Not cancelled: the scheduled message store is unreadable."
    kept = [e for e in entries if e["id"] != entry_id]
    if len(kept) == len(entries):
        return f"No scheduled message with id {entry_id!r}."
    _save(kept)
    logger.info("Cancelled scheduled message %s", entry_id)
    return f"Cancelled {entry_id}."


def update(entry_id: str, at: Optional[str] = None, in_minutes: Optional[int] = None,
           window: Optional[str] = None, brief: Optional[str] = None,
           now: Optional[datetime] = None, repeat: Optional[str] = None,
           script: Optional[str] = None, script_only: Optional[bool] = None,
           context_from: Optional[list] = None, keep_only: Optional[bool] = None,
           times: Optional[int] = None, until: Optional[str] = None) -> str:
    """Change a pending message in place: its time, its brief, its job settings, or a mix.

    A correction used to be cancel-then-add, two calls, and React did the add and
    skipped the cancel often enough that both messages sent (2026-10-14, and again
    in a live replay on 2026-10-16). Hermes edits a job in place for the same
    reason, so a correction here is one call.
    """
    now = now or datetime.now(timezone.utc)
    entry_id = (entry_id or "").strip()
    brief = _with_until((brief or "").strip(), until)
    moved = sum(x is not None for x in (at, in_minutes, window))
    if moved > 1:
        return "Not updated: give at most one of at, in_minutes or window."
    repeat, problem = _repeat(repeat)
    if problem:
        return f"Not updated: {problem}"
    job = {k: v for k, v in (("script", script), ("script_only", script_only),
                             ("context_from", context_from), ("keep_only", keep_only)) if v is not None}
    if not moved and not brief and repeat is None and not job and not times and not until:
        return "Not updated: give a new time, a new brief, a repeat, or a mix."
    due = None
    if moved:
        due, problem = _due(at, in_minutes, window, now)
        if problem:
            return f"Not updated: {problem}"

    try:
        entries = _load()
    except _Unreadable:
        return "Not updated: the scheduled message store is unreadable. Nothing was changed."
    entry = next((e for e in entries if e["id"] == entry_id), None)
    if entry is None:
        return f"Not updated: no scheduled message with id {entry_id!r}. List them to find it."
    if until and not brief:
        brief = _with_until(entry.get("brief") or "", until)
    after = {**entry, **job, **({"brief": brief, "content": ""} if brief else {}),
             **({"repeat": repeat} if repeat is not None else {})}
    problem = _job_problem(after, _known_ids(entries))
    if problem:
        return f"Not updated: {problem}"
    for field, value in job.items():
        if value:
            entry[field] = value
        else:
            entry.pop(field, None)
    if due is not None:
        entry["due"] = due.isoformat()
        entry["attempts"] = 0
        entry.pop("failed", None)
    if brief:
        entry["brief"] = brief
        entry["content"] = ""
    if repeat is not None:
        entry["repeat"] = repeat
        if repeat == "weekdays":
            entry["due"] = _onto_weekday(datetime.fromisoformat(entry["due"])).isoformat()
    if times:
        entry["times_left"] = times
    _save(entries)
    logger.info("Updated scheduled message %s, due %s", entry_id, entry["due"])
    every = f", then {_repeat_words(entry['repeat'])}" if entry.get("repeat") else ""
    return f"Okay, now {_human(datetime.fromisoformat(entry['due']), now)}{every}."


def set_paused(entry_id: str, paused: bool, now: Optional[datetime] = None) -> str:
    """Pause or resume one message without losing it, as Hermes' pause/resume."""
    now = now or datetime.now(timezone.utc)
    entry_id = (entry_id or "").strip()
    try:
        entries = _load()
    except _Unreadable:
        return "Not changed: the scheduled message store is unreadable."
    entry = next((e for e in entries if e["id"] == entry_id), None)
    if entry is None:
        return f"No scheduled message with id {entry_id!r}. List them to find it."
    entry["paused"] = paused
    if not paused and entry.get("repeat") and datetime.fromisoformat(entry["due"]) <= now:
        # Resuming must not fire every occurrence missed while paused.
        entry["due"] = _next_occurrence(datetime.fromisoformat(entry["due"]), entry["repeat"],
                                        now).isoformat()
    _save(entries)
    logger.info("%s scheduled message %s", "Paused" if paused else "Resumed", entry_id)
    if paused:
        return f"Paused {entry_id}. It stays listed and will not send until resumed."
    return f"Resumed {entry_id}. Next: {_local(entry['due'])}."


_SILENT = "[SILENT]"
# Hermes' LOOP_COMPLETE, on its own line: this was the last one, whatever is left of the series.
_DONE = re.compile(r"^\s*\[DONE\]\s*$", re.MULTILINE)
# 2026-11-04: a weekday idea with no stop condition ended "...the token budget unchanged. [DONE]".
# Not on its own line, so the series went on, but the mark reached the user. One at the very end is
# taken off; one inside a sentence, as a page might quote it, is left.
_TRAILING_DONE = re.compile(r"\s*\[DONE\]\s*\Z")
# Said in code, so a series a model ended by mistake does not just stop arriving.
_LAST_ONE = "That was the last of these: the series has ended."


async def deliver_due(send_fn: Callable[[str], Awaitable[bool]],
                      now: Optional[datetime] = None,
                      compose: Optional[Callable] = None) -> int:
    """Send everything due. Returns how many were delivered. Never raises."""
    now = now or datetime.now(timezone.utc)
    try:
        due = [e for e in _load()
               if not e.get("failed") and not e.get("paused")
               and datetime.fromisoformat(e["due"]) <= now]
    except (_Unreadable, ValueError) as e:
        logger.error("scheduled.json is unreadable, nothing delivered - %s", e)
        return 0

    delivered = 0
    for entry in sorted(due, key=lambda e: bool(e.get("context_from"))):
        text = entry.get("content") or ""
        brief = entry.get("brief") or ""
        script, printed, quiet, broke = entry.get("script"), "", False, False
        if script:
            result, why = await prerun.run(script)
            if result is None:
                # One alert for a run of failures: an hourly job would otherwise say so every hour.
                broke, quiet = True, bool(entry.get("script_failures"))
                text = f"The scheduled script {script} failed: {why}"
            elif not prerun.wakes(result):
                quiet = True
            elif entry.get("script_only"):
                text = result
            else:
                printed = result
        if brief and not text and not quiet:
            # Composition is a model call and can fail. A check-in that arrives in
            # plain words beats one that vanishes, so the brief is the fallback.
            writer = compose
            if writer is None:
                from agents.compose import write as writer
            extra = {"recent": entry.get("recent") or []} if entry.get("repeat") else {}
            material = _material(script, printed, entry.get("context_from"))
            if material:
                extra["material"] = material
            try:
                written = await writer(brief, now, **extra)
            except Exception as e:
                logger.error("Composing %s failed - %s: %s", entry["id"], type(e).__name__, e)
                written = None
            text = (written or "").strip() or "\n\n".join(x for x in (brief, printed) if x)
        finished = not broke and not entry.get("content") and bool(_DONE.search(text))
        if finished:
            text = _DONE.sub("", text).strip()
        elif not broke and not entry.get("content") and _TRAILING_DONE.search(text):
            logger.warning("Scheduled message %s ended with [DONE] inside a line: removed, the series goes on",
                           entry["id"])
            text = _TRAILING_DONE.sub("", text).strip()
        if finished and text and entry.get("repeat"):
            text = f"{text}\n\n{_LAST_ONE}"
        body = text
        # Hermes' cron: a job with nothing to report says so and nothing is sent. A failed
        # run never gets here with the mark, so it still reaches the user.
        silent = quiet or (not broke and bool(brief) and not entry.get("content") and _SILENT in text) or (finished and not text)
        keep = bool(entry.get("keep_only")) and not broke
        if now - datetime.fromisoformat(entry["due"]) > _LATE_AFTER:
            text = f"(Scheduled for {_local(entry['due'])}, delivered late.)\n{text}"
        try:
            ok = silent or keep or bool(await send_fn(text))
        except Exception as e:
            logger.error("Scheduled message %s send raised - %s: %s", entry["id"], type(e).__name__, e)
            ok = False

        # Re-read after the await. React can add or cancel while the send is in
        # flight, and writing back the list read before it would undo that.
        try:
            entries = _load()
        except _Unreadable as e:
            logger.error("scheduled.json became unreadable mid-delivery - %s", e)
            return delivered

        if ok:
            delivered += not silent and not keep
            produced = not silent and not broke
            if produced:
                _remember(entry["id"], body, now)
            kept = next((x for x in entries if x["id"] == entry["id"]), None)
            if kept is not None and broke:
                kept["script_failures"] = kept.get("script_failures", 0) + 1
            elif kept is not None:
                kept.pop("script_failures", None)
            left = kept.get("times_left") if kept is not None else None
            last_run = finished or (isinstance(left, int) and left <= 1)
            if kept is not None and kept.get("repeat") and not last_run:
                if left:
                    kept["times_left"] = left - 1
                kept["due"] = _next_occurrence(datetime.fromisoformat(kept["due"]),
                                               kept["repeat"], now).isoformat()
                kept["attempts"] = 0
                if produced:
                    kept["recent"] = ([body[:_RECENT_CHARS]] + (kept.get("recent") or []))[:_KEEP_RECENT]
                logger.info("Scheduled message %s %s, next %s", entry["id"],
                            "stayed silent" if silent else "kept" if keep else "delivered", kept["due"])
            else:
                entries = [x for x in entries if x["id"] != entry["id"]]
                logger.info("Scheduled message %s %s", entry["id"],
                            "stayed silent" if silent else "kept" if keep else "delivered")
        else:
            for x in entries:
                if x["id"] != entry["id"]:
                    continue
                x["attempts"] = x.get("attempts", 0) + 1
                if x["attempts"] >= _MAX_ATTEMPTS:
                    x["failed"] = True
                    logger.error("Scheduled message %s failed %d times, giving up", x["id"], x["attempts"])
                else:
                    logger.warning("Scheduled message %s not delivered (attempt %d), will retry",
                                   x["id"], x["attempts"])
        _save(entries)
    return delivered


def status() -> str:
    """For Monitor. Asked "what messages are scheduled?" with nothing to read, it
    answered "No other messages are currently scheduled" while one was pending."""
    try:
        entries = _load()
    except _Unreadable:
        return "Messages scheduled for the user: the store is unreadable, so this is unknown."
    if not entries:
        return "Messages scheduled for the user: none."
    lines = []
    for e in sorted(entries, key=lambda e: e["due"]):
        state = " (failed to send)" if e.get("failed") else ""
        state += f" (repeats {_repeat_words(e['repeat'])})" if e.get("repeat") else ""
        state += " (paused)" if e.get("paused") else ""
        state += f" ({_job_words(e, ids=False)})" if _job_words(e) else ""
        lines.append(f"  - {_local(e['due'])}{state}: {(e.get('brief') or e.get('content') or e.get('script') or '')[:80]}")
    return "Messages scheduled for the user:\n" + "\n".join(lines)


def _flag(value) -> Optional[bool]:
    return None if value in ("", None) else value is True or str(value).strip().lower() == "true"


def _ids(value) -> Optional[list]:
    """A list, or one id, or several in a string; none clears them; absent stays None."""
    if value in ("", None, []):
        return None
    parts = value if isinstance(value, list) else re.split(r"[,\s]+", str(value))
    ids = [str(p).strip() for p in parts if str(p).strip()]
    return [] if ids == ["none"] else ids


def run_tool(inputs: dict) -> str:
    """React's entry point. Models send "" for unused fields and numbers as strings.

    React can only give a brief. With a content field on offer it pre-wrote the
    message three times out of three in a live test (2026-10-16), once replacing a
    correct brief, so the user got words they had already read. Hermes stores only a
    prompt for the same reason. A stray content still arrives as a brief.
    """
    action = inputs.get("action")
    if action in ("add", "update"):
        at = inputs.get("at") or None
        window = inputs.get("window") or None
        in_minutes = inputs.get("in_minutes")
        if in_minutes in ("", None):
            in_minutes = None
        elif isinstance(in_minutes, str) and in_minutes.strip().isdigit():
            in_minutes = int(in_minutes.strip())
        # "Start now", as a repeating script is usually asked for: live it cost a refusal and a call.
        if in_minutes == 0:
            in_minutes = 1
        brief = inputs.get("brief") or inputs.get("content") or None
        repeat = inputs.get("repeat") or None
        script = inputs.get("script") or None
        if isinstance(script, str) and script.strip().lower() == "none":
            script = ""
        job = {"script": script, "script_only": _flag(inputs.get("script_only")),
               "context_from": _ids(inputs.get("context_from")), "keep_only": _flag(inputs.get("keep_only"))}
        times = inputs.get("times")
        job["times"] = int(times) if isinstance(times, (int, str)) and str(times).strip().isdigit() and int(times) > 0 else None
        job["until"] = inputs.get("until") or None
        if action == "update":
            return update(inputs.get("id", ""), at=at, in_minutes=in_minutes,
                          window=window, brief=brief, repeat=repeat, **job)
        return add(at=at, in_minutes=in_minutes, window=window, brief=brief, repeat=repeat, **job)
    if action == "list":
        return list_pending()
    if action == "cancel":
        return cancel(inputs.get("id", ""))
    if action in ("pause", "resume"):
        return set_paused(inputs.get("id", ""), action == "pause")
    return "Unknown action. Use add, update, pause, resume, list or cancel."
