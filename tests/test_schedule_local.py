"""What the user reads arrives at the same time on their clock, all year.

Run: python tests/test_schedule_local.py   (repo root, stdlib only, no API calls)

Fixed in UTC on a UTC host, the digest, job and gig lists and the Monday draft all came an hour early
when the clocks went back on 2026-11-01. The test that matters is the one either side of a change.
"""

import asyncio
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import clock
import config

_failures: list[str] = []
# The week before and after the clocks went back in 2026, and either side of the spring change.
BEFORE_FALL = datetime(2026, 10, 26, 3, 0, tzinfo=timezone.utc)
AFTER_FALL = datetime(2026, 11, 2, 3, 0, tzinfo=timezone.utc)
BEFORE_SPRING = datetime(2027, 3, 8, 3, 0, tzinfo=timezone.utc)
AFTER_SPRING = datetime(2027, 3, 15, 3, 0, tzinfo=timezone.utc)
EXPECTED = {"morning_digest": (10, 0), "job_watch": (11, 30), "gig_watch": (11, 45), "advocacy_draft": (12, 0)}


def _check(name: str, condition: bool, detail: str = "") -> None:
    print(f"  {'pass' if condition else 'FAIL'}  {name}" + ("" if condition else f"  {detail}"))
    if not condition:
        _failures.append(name)


def _jobs(schedule=None) -> dict:
    """The jobs monitor.setup really registers, with their triggers, and the scheduler stopped."""
    from agents import monitor
    real = config.MEMORY_DIR, config.GROQ_API_KEY

    async def _send(*args, **kwargs):
        return None

    async def run():
        monitor._setup_done = False
        monitor.setup(_send, _send, lambda: True)
        jobs = {j.id: j for j in monitor._scheduler.get_jobs()}
        monitor._scheduler.shutdown(wait=False)
        return jobs

    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as folder:
        config.MEMORY_DIR, config.GROQ_API_KEY = Path(folder), "test-key"
        if schedule is not None:
            (config.MEMORY_DIR / "schedule_config.md").write_text(schedule, encoding="utf-8")
        try:
            return asyncio.run(run())
        finally:
            config.MEMORY_DIR, config.GROQ_API_KEY = real


def _next(job, now: datetime) -> datetime:
    return job.trigger.get_next_fire_time(None, now)


def _on_clock(job, now: datetime) -> tuple[int, int]:
    fire = _next(job, now).astimezone(clock.USER_TZ)
    return fire.hour, fire.minute


def test_every_list_keeps_its_time() -> None:
    jobs = _jobs("# Schedule Config\n\n## morning_digest\ntime: 10:00 local\n")
    for name, expected in EXPECTED.items():
        _check(f"{name} is registered", name in jobs, str(sorted(jobs)))
        for label, now in (("before the clocks go back", BEFORE_FALL), ("after they go back", AFTER_FALL),
                           ("before they go forward", BEFORE_SPRING), ("after they go forward", AFTER_SPRING)):
            _check(f"{name} {label}", _on_clock(jobs[name], now) == expected, str(_on_clock(jobs[name], now)))
    digest = jobs["morning_digest"]
    _check("and that really moves in UTC, or nothing was tested",
           _next(digest, BEFORE_FALL).astimezone(timezone.utc).hour == 14
           and _next(digest, AFTER_FALL).astimezone(timezone.utc).hour == 15,
           str((_next(digest, BEFORE_FALL).astimezone(timezone.utc), _next(digest, AFTER_FALL).astimezone(timezone.utc))))
    _check("the draft still goes on Mondays", _next(jobs["advocacy_draft"], AFTER_FALL).astimezone(clock.USER_TZ).weekday() == 0)


def test_reading_the_file() -> None:
    from agents import monitor
    _check("local is the user's clock", _on_clock(_jobs("## morning_digest\ntime: 08:15 local\n")["morning_digest"], AFTER_FALL) == (8, 15))
    _check("no zone at all is too", _on_clock(_jobs("## morning_digest\ntime: 08:15\n")["morning_digest"], AFTER_FALL) == (8, 15))
    utc = _jobs("## morning_digest\ntime: 13:00 UTC\n")["morning_digest"]
    _check("a file that says UTC is taken at its word", _next(utc, AFTER_FALL).astimezone(timezone.utc).hour == 13)
    _check("no file means 10 on the user's clock", _on_clock(_jobs()["morning_digest"], AFTER_FALL) == (10, 0))
    _check("a file that does not parse does too", _on_clock(_jobs("## morning_digest\ntime: 8am\n")["morning_digest"], AFTER_FALL) == (10, 0))
    import routines
    _check("the one parser reads the new form",
           routines.setting("morning_digest", "## morning_digest\ntime: 09:00 local\n")["at"][:2] == (9, 0))
    _check("and writes it", "time: 09:00 local" in routines._with_line("", "morning_digest", "time: 09:00 local"))


def test_config_edit_leaves_times_alone() -> None:
    """2026-11-08: the digest's time and the watches' are changed live by routine_schedule, so ConfigEdit,
    which could only change them for the next restart, no longer writes them."""
    from agents import prod_memory
    source = Path(prod_memory.__file__).read_text(encoding="utf-8")
    _check("it is not asked to convert to UTC", 'Times are UTC' not in source)
    _check("and is told times are not set there", "When something runs" in prod_memory._CONFIG_SYSTEM_PROMPT
           and "schedule_config.md" not in prod_memory._CONFIG_EDITABLE)


if __name__ == "__main__":
    test_every_list_keeps_its_time()
    test_reading_the_file()
    test_config_edit_leaves_times_alone()
    print(f"\n{len(_failures)} failures" if _failures else "\nAll pass")
    sys.exit(1 if _failures else 0)
