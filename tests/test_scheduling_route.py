"""Scheduling requests route to React in code, and repeating messages see their past.

Run: python tests/test_scheduling_route.py   (repo root, no API calls)

Live 2026-10-16: "actually make it 11:30", "every weekday at 9am send me..." and
"pause that one" all routed CONFIG despite a router rule, and a weekday series got
one specific idea as its brief, so it would have repeated it every day.
"""

import asyncio
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import config

config.MEMORY_DIR = Path(tempfile.mkdtemp())

_failures: list[str] = []


def _check(name: str, condition: bool, detail: str = "") -> None:
    print(f"  {'pass' if condition else 'FAIL'}  {name}" + ("" if condition else f"  {detail}"))
    if not condition:
        _failures.append(name)


TO_REACT = [
    "Ok thanks could you message me around 100am with some thought provoking material?",
    "actually make it 11:30",
    "Every weekday at 9am send me one short, practical idea for improving IGOR",
    "pause that one for now",
    "remind me to call mom tomorrow",
    "Message me at 11pm with something thought-provoking",
    "resume my daily check-in",
    "cancel that reminder",
    "move it to 10pm",
    "ping me in 20 minutes",
]
LEFT_TO_THE_ROUTER = [
    "what messages are scheduled?",            # a current value: Monitor
    "tell me about your shell tool",           # about IGOR: SelfDescribe
    "change the digest time to 14:00",         # a saved setting: ConfigEdit
    "drop weather from the digest",
    "add a price watch for the AG01 every day",
    "make it shorter",                         # an edit, not a time
    "tell me a story in five sentences",       # chat
    "what do you think about self hosting",
    "how much of the token budget is left today",
]


async def test_routing() -> None:
    import orchestrator

    async def notify(_):
        pass

    o = orchestrator.Orchestrator(notify)
    routed = []

    async def no_router(*a, **k):
        routed.append(1)
        raise RuntimeError("router was called")

    o._client.chat.completions.create = no_router
    for message in TO_REACT:
        routed.clear()
        got = await o._classify(orchestrator._spell_out_times(message))
        _check(f"to the scheduling route in code: {message!r}", got == "Schedule" and not routed,
               f"{got}, router={bool(routed)}")
    for message in LEFT_TO_THE_ROUTER:
        routed.clear()
        await o._classify(message)
        _check(f"left to the router: {message!r}", bool(routed))

    # 2026-10-22 and 10-23: "Remember that..." drew "Got it" twice and saved nothing.
    for message in TO_MEMORY:
        routed.clear()
        got = await o._classify(message)
        _check(f"to the memory route in code: {message!r}", got == "Remember" and not routed,
               f"{got}, router={bool(routed)}")
    for message in NOT_TO_MEMORY:
        routed.clear()
        got = await o._classify(message)
        _check(f"not the memory route: {message!r}", got != "Remember", got)
    o._last_route = "Remember"
    _check("a short correction right after a save stays on the memory route",
           await o._classify("Actually make that Monday.") == "Remember")
    o._last_route = "Direct"
    routed.clear()
    await o._classify("Actually make that Monday.")
    _check("but after anything else it is left to the router", bool(routed))


TO_MEMORY = [
    "Remember that I want to try IGOR's MCP server from Claude Code this weekend",
    "please remember my day off is Thursday",
    "Note that the AG01 dock arrived",
    "don't forget that I prefer short replies",
    "Keep in mind that Helfen may call next week",
]
NOT_TO_MEMORY = [
    "Do you remember what I said about the demo video?",
    "remember when we fixed the router",
    "Remember to call mom",
    "remember what I told you?",
    "Remember that thing from yesterday?",
]


async def test_remember_requires_the_memory_tool() -> None:
    import context_store
    import orchestrator
    from agents import react
    tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
    real = (config.MEMORY_DIR, context_store._DB_PATH, react.handle, config.AUTHORIZED_USER_ID)
    seen = {}

    async def fake_handle(message, context, **kw):
        seen.update(kw, message=message)
        return "Saved to tasks.md: try IGOR's MCP server from Claude Code this weekend."

    async def notify(_):
        pass

    try:
        config.MEMORY_DIR = Path(tmp.name)
        context_store._DB_PATH = config.MEMORY_DIR / "context.db"
        config.AUTHORIZED_USER_ID = 42
        react.handle = fake_handle
        o = orchestrator.Orchestrator(notify)
        reply, _ = await o.process(42, "Remember that I want to try IGOR's MCP server this weekend")
        _check("a save request reaches React with the memory tool required",
               seen.get("force_tool") == "memory_write" and reply.startswith("Saved"), str(seen))
        _check("and is told a duplicate is refused for it, so it need not read first",
               "do not read the file first" in seen.get("message", ""), seen.get("message", ""))
        import memory_entries
        memory_entries.last_written = ("tasks.md", "- Try IGOR's MCP server this weekend (2026-10-23)")
        seen.clear()
        await o.process(42, "Actually make that Monday.")
        _check("the correction that follows has the tool required too",
               seen.get("force_tool") == "memory_write", str(seen))
        _check("and carries the saved entry's exact text, so the patch needs no read",
               "reads exactly: - Try IGOR's MCP server this weekend (2026-10-23)" in seen.get("message", ""),
               seen.get("message", ""))
        memory_entries.last_written = None
    finally:
        config.MEMORY_DIR, context_store._DB_PATH, react.handle, config.AUTHORIZED_USER_ID = real
        tmp.cleanup()


async def test_continuity() -> None:
    from agents import scheduled
    seen = []

    async def writer(brief, now, recent=None):
        seen.append(list(recent) if recent is not None else None)
        return f"idea number {len(seen)}"

    async def send(text):
        return True

    fri = datetime(2026, 10, 30, 12, 0, tzinfo=timezone.utc)
    scheduled.add(brief="one practical idea for improving IGOR", at="2026-10-30 09:00",
                  repeat="daily", now=fri)
    for day in (30, 31):
        await scheduled.deliver_due(send, now=datetime(2026, 10, day, 13, 0, 30, tzinfo=timezone.utc),
                                    compose=writer)
    await scheduled.deliver_due(send, now=datetime(2026, 11, 1, 14, 0, 30, tzinfo=timezone.utc),
                                compose=writer)
    await scheduled.deliver_due(send, now=datetime(2026, 11, 2, 14, 0, 30, tzinfo=timezone.utc),
                                compose=writer)
    _check("the first run of a series has nothing before it", seen[0] == [], str(seen))
    _check("each later run sees what the series already said, newest first",
           seen[2] == ["idea number 2", "idea number 1"], str(seen))
    _check("and only the last three", seen[3] == ["idea number 3", "idea number 2", "idea number 1"]
           and len(seen[3]) == 3, str(seen))

    one_off = []

    async def plain(brief, now):
        one_off.append(brief)
        return "done"

    scheduled.add(brief="call the bank", at="2026-11-02 12:00", now=datetime(2026, 11, 2, 15, 0, tzinfo=timezone.utc))
    await scheduled.deliver_due(send, now=datetime(2026, 11, 2, 17, 0, 30, tzinfo=timezone.utc), compose=plain)
    _check("a one-off message is written exactly as before", one_off == ["call the bank"], str(one_off))


async def test_write_passes_the_series_on() -> None:
    from agents import compose, react
    asked = {}

    async def agent(message, context, **kw):
        asked["message"] = message
        return "something new"

    real = react.handle
    react.handle = agent
    try:
        await compose.write("one practical idea", recent=["Add a CLI summary command"])
        with_past = asked["message"]
        await compose.write("one practical idea")
        without = asked["message"]
    finally:
        react.handle = real
    _check("the send-time agent is shown the earlier messages",
           "Add a CLI summary command" in with_past and "do not repeat" in with_past, with_past)
    _check("and told a single named idea is an example, not the content", "example" in with_past)
    _check("a message with no past gets the brief alone", "repeats" not in without, without)


async def test_schedule_cannot_be_claimed_without_the_tool() -> None:
    """Live 2026-10-16: "every weekday at 10am send me one practical idea for IGOR"
    got "Scheduled a recurring message for you..." from one model call that never
    used the tool, and scheduled.json stayed empty. This fake model does exactly that
    whenever it is allowed to - and only calls the tool when it is required to."""
    import json
    from types import SimpleNamespace
    from agents import react

    store = Path(tempfile.mkdtemp())
    config.MEMORY_DIR = store
    calls = []

    def reply(text):
        return SimpleNamespace(choices=[SimpleNamespace(
            finish_reason="stop", message=SimpleNamespace(content=text, tool_calls=None))], usage=None)

    async def create(model, **kwargs):
        calls.append(kwargs)
        if kwargs.get("tool_choice"):
            args = json.dumps({"action": "add", "in_minutes": 60, "repeat": "weekdays",
                               "brief": "one practical idea for IGOR"})
            call = SimpleNamespace(id="c1", type="function",
                                   function=SimpleNamespace(name="scheduled_message", arguments=args))
            return SimpleNamespace(choices=[SimpleNamespace(
                finish_reason="tool_calls", message=SimpleNamespace(content=None, tool_calls=[call]))],
                usage=None)
        if any(m.get("role") == "tool" for m in kwargs["messages"]):
            return reply("Done - every weekday, starting with the first one in an hour.")
        return reply("Scheduled a recurring message for you: every weekday at 10:00 AM.")

    fake = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=create)))
    real = react._get_client
    react._get_client = lambda: fake
    try:
        claimed = await react.handle("every weekday at 10am send me one practical idea for IGOR", [])
        stored_free = json.loads((store / "scheduled.json").read_text()) if (store / "scheduled.json").exists() else []
        _check("unforced, this model claims success and stores nothing (the 10-16 failure)",
               claimed.startswith("Scheduled") and stored_free == [], f"{claimed!r} {stored_free}")

        calls.clear()
        answer = await react.handle("every weekday at 10am send me one practical idea for IGOR", [],
                                    force_tool="scheduled_message")
        stored = json.loads((store / "scheduled.json").read_text())
    finally:
        react._get_client = real
    _check("on the scheduling route the tool is required on the first call",
           calls[0].get("tool_choice", {}).get("function", {}).get("name") == "scheduled_message", str(calls[0].keys()))
    _check("so the message really is stored, repeating",
           len(stored) == 1 and stored[0]["repeat"] == "weekdays", str(stored))
    _check("and once the tool has run, the model is free to answer",
           "tool_choice" not in calls[1] and answer.startswith("Done"), f"{calls[1].keys()} {answer!r}")


async def test_monitor_gets_no_history() -> None:
    """Live 2026-10-16: Monitor said a weekday message was scheduled because React
    had said so a turn earlier, while its own status block said none."""
    import orchestrator
    from agents import monitor
    seen = {}

    async def handle(content, context, call):
        seen["context"] = context
        return "status"

    async def notify(_):
        pass

    o = orchestrator.Orchestrator(notify)
    o._context = [{"role": "user", "content": "every weekday at 10am send me an idea"},
                  {"role": "assistant", "content": "Scheduled a recurring message for you."}]
    real = monitor.handle
    monitor.handle = handle
    try:
        await o._route("Monitor", "whats scheduled?")
    finally:
        monitor.handle = real
    _check("Monitor answers from its status block alone, with no conversation",
           seen.get("context") == [], str(seen.get("context")))


if __name__ == "__main__":
    asyncio.run(test_routing())
    asyncio.run(test_continuity())
    asyncio.run(test_write_passes_the_series_on())
    asyncio.run(test_schedule_cannot_be_claimed_without_the_tool())
    asyncio.run(test_monitor_gets_no_history())
    asyncio.run(test_remember_requires_the_memory_tool())
    if _failures:
        print(f"\n{len(_failures)} FAILED: {', '.join(_failures)}")
        sys.exit(1)
    print("\nall pass")
