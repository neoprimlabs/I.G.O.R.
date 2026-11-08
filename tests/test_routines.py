"""IGOR's routines retimed when the user asks (routines.py, Hermes' cronjob update).

Run: python tests/test_routines.py   (repo root, no API calls)

2026-11-08 the user wanted the price and stock watches run less often and asked whether IGOR could do it.
It could not: both were fixed in code, and the request would have gone to ConfigEdit, which could write a
line nothing read and answer "Updated".
"""

import asyncio
import json
import sys
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import config

config.MEMORY_DIR = Path(tempfile.mkdtemp())

import clock
import routines

_failures: list[str] = []


def _check(name: str, condition: bool, detail: str = "") -> None:
    print(f"  {'pass' if condition else 'FAIL'}  {name}" + ("" if condition else f"  {detail}"))
    if not condition:
        _failures.append(name)


FILE = "# Schedule Config\n\n<!-- kept by hand -->\n\n## morning_digest\ntime: 10:00 local\n"


def _fresh(text=FILE):
    config.MEMORY_DIR = Path(tempfile.mkdtemp())
    if text is not None:
        (config.MEMORY_DIR / "schedule_config.md").write_text(text, encoding="utf-8")
    return config.MEMORY_DIR / "schedule_config.md"


class _Running:
    """The five jobs as monitor.setup registers them, on a paused scheduler, as monitor._scheduler."""

    def __enter__(self):
        from apscheduler.schedulers.background import BackgroundScheduler
        from agents import monitor
        self.scheduler = BackgroundScheduler(timezone="UTC")
        for routine in routines.ROUTINES:
            self.scheduler.add_job(lambda: None, routines.trigger(routine), id=routine)
        self.held = monitor._scheduler
        self.scheduler.start(paused=True)
        monitor._scheduler = self.scheduler
        return self.scheduler

    def __exit__(self, *exc):
        from agents import monitor
        monitor._scheduler = self.held
        self.scheduler.shutdown(wait=False)


def _on_clock(job) -> tuple:
    fire = job.trigger.get_next_fire_time(None, datetime(2026, 11, 9, 3, 0, tzinfo=timezone.utc)).astimezone(clock.USER_TZ)
    return fire.hour, fire.minute


def test_what_the_file_says() -> None:
    _fresh(None)
    _check("no file: the times they always had", [routines.describe(r) for r in routines.ROUTINES]
           == ["daily at 10:00 AM", "daily at 11:30 AM", "daily at 11:45 AM", "every 2 hours", "every hour"],
           str([routines.describe(r) for r in routines.ROUTINES]))
    text = "## morning_digest\ntime: 13:00 UTC\n## price_watch\nevery: 2 days\n## stock_watch\nevery: 0 hours\n## job_watch\ntime: 8am\n"
    _check("a digest line that says UTC is taken at its word", routines.setting("morning_digest", text)["at"][2] is timezone.utc)
    _check("days are hours", routines.setting("price_watch", text) == {"every": 48})
    _check("an interval out of range is the default, not a fault", routines.setting("stock_watch", text) == {"every": 1})
    _check("so is a time that does not read", routines.setting("job_watch", text)["at"][:2] == (11, 30))
    _check("a daily interval reads as a day", routines.describe("price_watch", "## price_watch\nevery: 24 hours\n") == "every day")


def test_a_change_with_nothing_running() -> None:
    path = _fresh()
    out = routines.change("price_watch", every_hours=6)
    text = path.read_text(encoding="utf-8")
    _check("saved, and said to take effect at the next start", out.startswith("Saved") and "every 6 hours" in out
           and "every 2 hours" in out and "next starts" in out, out)
    _check("as its own section", "## price_watch\nevery: 6 hours" in text, text)
    _check("with every other line as it was", text.startswith(FILE.rstrip()), text)
    routines.change("price_watch", every_hours=12)
    text = path.read_text(encoding="utf-8")
    _check("a second change replaces the line, not adds one", text.count("every:") == 1 and "every: 12 hours" in text, text)
    routines.change("morning_digest", at="08:15")
    text = path.read_text(encoding="utf-8")
    _check("the digest's time is replaced in place", "time: 08:15 local" in text and "10:00" not in text
           and text.count("## morning_digest") == 1, text)
    _check("and the comment kept", "<!-- kept by hand -->" in text, text)


def test_a_change_applies_at_once() -> None:
    path = _fresh()
    with _Running() as scheduler:
        out = routines.change("stock_watch", every_hours=8)
        job = scheduler.get_job("stock_watch")
        _check("the live job runs every 8 hours now", job.trigger.interval == timedelta(hours=8), str(job.trigger))
        _check("from now, not from its last run", abs((job.next_run_time - datetime.now(timezone.utc)) - timedelta(hours=8))
               < timedelta(minutes=1), str(job.next_run_time))
        _check("and the reply says so, with the next run", out.startswith("Done: the stock watch now runs every 8 hours (it was every hour).")
               and "Next run:" in out, out)
        out = routines.change("job_watch", at="9:05")
        _check("a daily one moves to its new time on the user's clock", _on_clock(scheduler.get_job("job_watch")) == (9, 5),
               str(_on_clock(scheduler.get_job("job_watch"))))
        _check("written in full", "time: 09:05 local" in path.read_text(encoding="utf-8"))
        listing = routines.listing()
        _check("the list shows each with its next run", all(name in listing for name in routines.ROUTINES)
               and "every 8 hours, next" in listing and "daily at 9:05 AM, next" in listing, listing)

        import checkpoints
        out = checkpoints.restore(1)
        _check("a rollback puts the file back", "time: 09:05" not in path.read_text(encoding="utf-8"), out)
        _check("and the live job with it", _on_clock(scheduler.get_job("job_watch")) == (11, 30)
               and "restored times" in out, f"{_on_clock(scheduler.get_job('job_watch'))} {out}")


def test_what_it_refuses() -> None:
    path = _fresh()
    for args, why in (({"routine": "heartbeat", "every_hours": 2}, "no routine"),
                      ({"routine": "overnight", "at": "01:00"}, "no routine"),
                      ({"routine": "price_watch", "at": "09:00"}, "every_hours"),
                      ({"routine": "price_watch", "every_hours": 0}, "1 to 168"),
                      ({"routine": "stock_watch", "every_hours": 200}, "1 to 168"),
                      ({"routine": "stock_watch", "every_hours": True}, "1 to 168"),
                      ({"routine": "stock_watch"}, "1 to 168"),
                      ({"routine": "morning_digest", "every_hours": 3}, "takes at"),
                      ({"routine": "job_watch", "at": "9am"}, "HH:MM"),
                      ({"routine": "gig_watch", "at": "24:00"}, "HH:MM")):
        out = routines.change(args.pop("routine"), **args)
        _check(f"refused: {why}", out.startswith("[routine_schedule refused") and why in out, out)
    _check("and nothing was written", path.read_text(encoding="utf-8") == FILE)
    _check("the same timing again changes nothing", "already runs" in routines.change("morning_digest", at="10:00")
           and path.read_text(encoding="utf-8") == FILE)
    path = _fresh("## morning_digest\ntime: 10:00 UTC\n")
    out = routines.change("morning_digest", at="10:00")
    _check("a UTC digest set to the same hour on the user's clock is a change",
           "already" not in out and "time: 10:00 local" in path.read_text(encoding="utf-8"), out)
    _check("an unknown action", routines.run_tool({"action": "pause"}).startswith("[routine_schedule refused"))


def test_monitor_starts_with_the_file() -> None:
    from agents import monitor

    async def _send(*args, **kwargs):
        return None

    async def run():
        monitor._setup_done = False
        monitor.setup(_send, _send, lambda: True)
        jobs = {j.id: j for j in monitor._scheduler.get_jobs()}
        monitor._scheduler.shutdown(wait=False)
        return jobs

    _fresh(FILE + "\n## price_watch\nevery: 6 hours\n\n## gig_watch\ntime: 07:40 local\n")
    real = config.GROQ_API_KEY, monitor._scheduler
    config.GROQ_API_KEY = "test-key"
    try:
        jobs = asyncio.run(run())
    finally:
        config.GROQ_API_KEY, monitor._scheduler = real
        monitor._setup_done = False
    _check("a restart keeps a changed interval", jobs["price_watch"].trigger.interval == timedelta(hours=6), str(jobs["price_watch"].trigger))
    _check("and a changed time", _on_clock(jobs["gig_watch"]) == (7, 40), str(_on_clock(jobs["gig_watch"])))
    _check("and the rest keep their own", jobs["stock_watch"].trigger.interval == timedelta(hours=1)
           and _on_clock(jobs["job_watch"]) == (11, 30) and _on_clock(jobs["morning_digest"]) == (10, 0))


def test_react_has_the_tool_and_keeps_it_safe() -> None:
    from agents import react
    _check("a deferred tool, with a rule", "routine_schedule" in react._DEFERRED_TOOLS and react._DEFERRED_TOOLS["routine_schedule"][1])
    _check("its schema is there", any(t["function"]["name"] == "routine_schedule" for t in react._openai_tools()))
    _check("closed after a web read", "routine_schedule" in react._QUARANTINED_AFTER_WEB)
    _check("and never a worker's", "routine_schedule" in react._WORKER_FORBIDDEN)
    for text in ("run the price watch every 6 hours", "how often does the stock watch run", "move the digest to 8"):
        _check(f"opened by: {text!r}", bool(react._OPENS["routine_schedule"].search(text)))


TO_ROUTINE = [
    "The stock watch and the price watch don't have to be run that often",
    "Run the price watch every 6 hours",
    "Can the stock watch run twice a day instead?",
    "move the job watch to 9am",
    "change the digest time to 14:00",
    "Make the gig watch run at 8:30",
    "how often does the price watch run?",
    "when does the morning digest go out?",
    "push the digest to 11",
]
NOT_ROUTINE = [
    "add a price watch for the AG01 every day",
    "Has the stock watch seen a restock yet?",
    "drop weather from the digest",
    "is the stock watch running?",
    "remove every news item from the digest",
    "What did the job watch find at 11:30?",
]


async def test_the_route() -> None:
    import context_store
    import orchestrator
    from agents import react

    async def notify(_):
        pass

    o = orchestrator.Orchestrator(notify)

    async def no_router(*a, **k):
        raise RuntimeError("router was called")

    o._client.chat.completions.create = no_router
    for message in TO_ROUTINE:
        got = await o._classify(orchestrator._spell_out_times(message))
        _check(f"to the routine route in code: {message!r}", got == "Routine", got)
    for message in NOT_ROUTINE:
        try:
            got = await o._classify(message)
        except RuntimeError:
            got = "router"
        _check(f"not the routine route: {message!r}", got != "Routine", got)

    seen = {}

    async def fake_handle(message, context, **kw):
        seen.update(kw)
        return "Done: the price watch now runs every 6 hours."

    tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
    real = (config.MEMORY_DIR, context_store._DB_PATH, react.handle, config.AUTHORIZED_USER_ID)
    try:
        config.MEMORY_DIR = Path(tmp.name)
        context_store._DB_PATH = config.MEMORY_DIR / "context.db"
        config.AUTHORIZED_USER_ID = 42
        react.handle = fake_handle
        o = orchestrator.Orchestrator(notify)
        await o.process(42, "The stock watch and the price watch don't have to be run that often")
        _check("it reaches React with routine_schedule required", seen.get("force_tool") == "routine_schedule", str(seen))
    finally:
        config.MEMORY_DIR, context_store._DB_PATH, react.handle, config.AUTHORIZED_USER_ID = real
        tmp.cleanup()


def test_a_turn_that_changes_one() -> None:
    """The way 10-16's scheduling failure would repeat here: a model that claims the change without the
    tool. Required on the first call, the tool runs and the file holds the change."""
    from agents import react
    path = _fresh()
    calls = []

    async def create(model, **kwargs):
        calls.append(kwargs)
        if kwargs.get("tool_choice"):
            call = SimpleNamespace(id="c1", type="function", function=SimpleNamespace(
                name="routine_schedule", arguments=json.dumps({"action": "change", "routine": "price_watch", "every_hours": 6})))
            return SimpleNamespace(choices=[SimpleNamespace(
                finish_reason="tool_calls", message=SimpleNamespace(content=None, tool_calls=[call]))], usage=None)
        return SimpleNamespace(choices=[SimpleNamespace(finish_reason="stop", message=SimpleNamespace(
            content="The price watch now runs every 6 hours.", tool_calls=None))], usage=None)

    fake = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=create)))
    real = react._get_client
    react._get_client = lambda: fake
    try:
        reply = asyncio.run(react.handle("Run the price watch every 6 hours", [], force_tool="routine_schedule"))
    finally:
        react._get_client = real
    _check("the tool is required on the first call",
           calls[0].get("tool_choice", {}).get("function", {}).get("name") == "routine_schedule", str(calls[0].get("tool_choice")))
    result = next(m["content"] for m in calls[1]["messages"] if m.get("role") == "tool")
    _check("and its answer comes back to the turn", "every 6 hours" in result, result)
    _check("the file holds the change", "## price_watch\nevery: 6 hours" in path.read_text(encoding="utf-8"))
    _check("and the reply says it", "every 6 hours" in reply, reply)


async def test_config_edit_hands_schedules_on() -> None:
    from agents import prod_memory
    path = _fresh()

    async def writes_schedule(system, messages, max_tokens=2048, model=None):
        return "schedule_config.md\n<<<FILE\n## morning_digest\ntime: 08:00 local\n## price_watch\nevery: 6 hours\n>>>FILE"

    out = await prod_memory.handle("move the digest to 8am and run the price watch every 6 hours", writes_schedule)
    _check("ConfigEdit no longer writes schedules: it hands them to React", out is None, repr(out))
    _check("and the file is untouched", path.read_text(encoding="utf-8") == FILE)
    _check("it is not shown the schedule file", "schedule_config.md" not in prod_memory._CONFIG_EDITABLE
           and "schedule_config.md" not in prod_memory._ALLOWED_FILES)


if __name__ == "__main__":
    for name, fn in [(n, f) for n, f in globals().items() if n.startswith("test_") and callable(f)]:
        print(name)
        result = fn()
        if asyncio.iscoroutine(result):
            asyncio.run(result)
    if _failures:
        print(f"\n{len(_failures)} FAILED: {', '.join(_failures)}")
        sys.exit(1)
    print("\nall pass")
