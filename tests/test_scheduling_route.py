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
        _check(f"to React in code: {message!r}", got == "React" and not routed, f"{got}, router={bool(routed)}")
    for message in LEFT_TO_THE_ROUTER:
        routed.clear()
        await o._classify(message)
        _check(f"left to the router: {message!r}", bool(routed))


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


if __name__ == "__main__":
    asyncio.run(test_routing())
    asyncio.run(test_continuity())
    asyncio.run(test_write_passes_the_series_on())
    if _failures:
        print(f"\n{len(_failures)} FAILED: {', '.join(_failures)}")
        sys.exit(1)
    print("\nall pass")
