"""Tests for scheduled messages: agents/scheduled.py and clock.py.

Run: python tests/test_scheduled.py   (repo root, stdlib only, no API calls)

The design rests on two measurements taken on the server on 2026-09-12, both of
which a plain reading of the docs would have missed:

- APScheduler 3.11.3 drops a date job more than 1 second late with only a WARNING.
  Per-message jobs would lose anything due while IGOR is down, so delivery polls a
  file instead and an overdue message is simply due.
- The model supplies wall-clock time; the code does every conversion. Duration
  arithmetic was the worst category in Test of Time (arXiv 2406.09170).

Every expected value below is worked out by hand, not by the code under test.
"""

import asyncio
import json
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import config

_failures: list[str] = []


def _check(name: str, condition: bool, detail: str = "") -> None:
    if condition:
        print(f"  pass  {name}")
    else:
        print(f"  FAIL  {name}  {detail}")
        _failures.append(name)


def _utc(*args) -> datetime:
    return datetime(*args, tzinfo=timezone.utc)


# 01:14 UTC on Sep 12 is 21:14 EDT on Sep 11.
NOW = _utc(2026, 9, 12, 1, 14)


def _fresh_store() -> Path:
    config.MEMORY_DIR = Path(tempfile.mkdtemp())
    return config.MEMORY_DIR / "scheduled.json"


def _stored(path: Path) -> list[dict]:
    if not path.exists():
        return []
    return json.loads(path.read_text(encoding="utf-8"))


class _Sender:
    """Stands in for discord_bot.send_to_user: records text, returns delivered or not."""

    def __init__(self, delivers=True, raises=False, during=None):
        self.sent: list[str] = []
        self.calls = 0
        self._delivers, self._raises, self._during = delivers, raises, during

    async def __call__(self, content: str) -> bool:
        self.calls += 1
        if self._during:
            self._during()
        if self._raises:
            raise RuntimeError("discord unavailable")
        if self._delivers:
            self.sent.append(content)
        return self._delivers


def test_clock() -> None:
    import clock
    _check("time line in daylight time, with the weekday",
           clock.time_line(NOW) == "Fri 2026-09-11 21:14 EDT (01:14 UTC)", clock.time_line(NOW))
    nov = _utc(2026, 11, 15, 19, 0)
    _check("time line after the November switch",
           clock.time_line(nov) == "Sun 2026-11-15 14:00 EST (19:00 UTC)", clock.time_line(nov))


def test_add() -> None:
    from agents import scheduled

    path = _fresh_store()
    reply = scheduled.add("Checking in.", at="2026-09-12 14:00", now=NOW)
    entries = _stored(path)
    _check("2pm Eastern in September is stored as 18:00 UTC",
           len(entries) == 1 and entries[0]["due"] == "2026-09-12T18:00:00+00:00", str(entries))
    # The confirmation is read aloud by React, so it must sound like a person, not
    # like a receipt. "Scheduled 64c49d for Fri 2026-09-12 14:00 EDT" is what the
    # user saw before and specifically did not want.
    _check("confirmation gives the time in words", "2:00 PM" in reply, reply)
    _check("confirmation says which day", "Saturday" in reply or "tomorrow" in reply, reply)
    _check("confirmation hides the internal id", entries[0]["id"] not in reply, reply)

    path = _fresh_store()
    oct30 = _utc(2026, 10, 30, 12, 0)
    reply = scheduled.add("Checking in.", at="2026-11-02 14:00", now=oct30)
    entries = _stored(path)
    _check("2pm Eastern after the November switch is stored as 19:00 UTC",
           len(entries) == 1 and entries[0]["due"] == "2026-11-02T19:00:00+00:00", str(entries))
    _check("confirmation still reads naturally after the switch", "2:00 PM" in reply, reply)

    path = _fresh_store()
    scheduled.add("Checking in.", in_minutes=90, now=NOW)
    entries = _stored(path)
    _check("in_minutes=90 from 01:14 UTC is 02:44 UTC",
           len(entries) == 1 and entries[0]["due"] == "2026-09-12T02:44:00+00:00", str(entries))


def test_add_rejects() -> None:
    from agents import scheduled

    rejects = [
        ("a time already past", dict(content="x", at="2026-09-11 20:00")),
        ("more than 30 days out", dict(content="x", at="2026-10-13 10:00")),
        ("empty content", dict(content="   ", in_minutes=5)),
        ("no time at all", dict(content="x")),
        ("both at and in_minutes", dict(content="x", at="2026-09-12 14:00", in_minutes=5)),
        ("a time the code cannot parse", dict(content="x", at="tomorrow 2pm")),
        ("in_minutes of zero", dict(content="x", in_minutes=0)),
    ]
    for name, kwargs in rejects:
        path = _fresh_store()
        reply = scheduled.add(now=NOW, **kwargs)
        _check(f"rejects {name}", reply.startswith("Not scheduled") and _stored(path) == [],
               f"{reply!r} {_stored(path)}")

    path = _fresh_store()
    for i in range(20):
        scheduled.add(f"message {i}", in_minutes=10 + i, now=NOW)
    reply = scheduled.add("one too many", in_minutes=60, now=NOW)
    _check("the 21st pending message is refused",
           reply.startswith("Not scheduled") and len(_stored(path)) == 20, reply)


def test_list_and_cancel() -> None:
    from agents import scheduled

    path = _fresh_store()
    scheduled.add("Checking in.", at="2026-09-12 14:00", now=NOW)
    entry_id = _stored(path)[0]["id"]
    listing = scheduled.list_pending()
    _check("list shows the id and the local time",
           entry_id in listing and "2026-09-12 14:00 EDT" in listing, listing)

    reply = scheduled.cancel("nope")
    _check("cancelling an unknown id changes nothing", len(_stored(path)) == 1, reply)
    scheduled.cancel(entry_id)
    _check("cancel removes the entry", _stored(path) == [])


async def test_deliver() -> None:
    from agents import scheduled

    path = _fresh_store()
    scheduled.add("Due now.", in_minutes=5, now=NOW)
    scheduled.add("Due later.", in_minutes=120, now=NOW)
    send = _Sender()
    count = await scheduled.deliver_due(send, now=_utc(2026, 9, 12, 1, 20))
    remaining = [e["content"] for e in _stored(path)]
    _check("only the due message is sent, verbatim",
           count == 1 and send.sent == ["Due now."], str(send.sent))
    _check("the sent message leaves the store, the later one stays",
           remaining == ["Due later."], str(remaining))

    # IGOR was down for hours across the due time - the case APScheduler drops.
    path = _fresh_store()
    scheduled.add("Checking in.", at="2026-09-12 14:00", now=NOW)
    send = _Sender()
    await scheduled.deliver_due(send, now=_utc(2026, 9, 12, 20, 0))
    _check("a message overdue by two hours is still delivered",
           len(send.sent) == 1 and "Checking in." in send.sent[0], str(send.sent))
    _check("and it says it is late, with the original local time",
           len(send.sent) == 1 and "late" in send.sent[0].lower() and "14:00" in send.sent[0],
           str(send.sent))
    _check("and it leaves the store", _stored(path) == [])


async def test_delivery_failures() -> None:
    from agents import scheduled

    path = _fresh_store()
    scheduled.add("Checking in.", in_minutes=5, now=NOW)
    later = _utc(2026, 9, 12, 1, 30)
    failing = _Sender(delivers=False)
    await scheduled.deliver_due(failing, now=later)
    entries = _stored(path)
    _check("a failed send keeps the message", len(entries) == 1, str(entries))
    _check("and counts the attempt", bool(entries) and entries[0]["attempts"] == 1, str(entries))

    for _ in range(9):
        await scheduled.deliver_due(failing, now=later)
    _check("ten failures in, it has been tried ten times", failing.calls == 10, str(failing.calls))
    await scheduled.deliver_due(failing, now=later)
    _check("after ten failures it stops trying", failing.calls == 10, str(failing.calls))
    _check("and keeps the message, marked failed, for a human to see",
           len(_stored(path)) == 1 and _stored(path)[0].get("failed") is True, str(_stored(path)))

    path = _fresh_store()
    scheduled.add("Checking in.", in_minutes=5, now=NOW)
    raised = False
    try:
        await scheduled.deliver_due(_Sender(raises=True), now=later)
    except Exception:
        raised = True
    _check("a send that raises does not escape the job", not raised)
    _check("and the message is kept", len(_stored(path)) == 1)


async def test_add_during_send_survives() -> None:
    """deliver_due awaits the Discord send. React can schedule during that await, and
    writing back the list read before it would silently delete the new message."""
    from agents import scheduled

    path = _fresh_store()
    scheduled.add("Due now.", in_minutes=5, now=NOW)
    send = _Sender(during=lambda: scheduled.add("Added mid-send.", in_minutes=60, now=NOW))
    await scheduled.deliver_due(send, now=_utc(2026, 9, 12, 1, 20))
    remaining = [e["content"] for e in _stored(path)]
    _check("a message scheduled during a send is not lost",
           remaining == ["Added mid-send."], str(remaining))


async def test_corrupt_store() -> None:
    from agents import scheduled

    path = _fresh_store()
    path.write_text("{not json", encoding="utf-8")
    reply = scheduled.add("x", in_minutes=5, now=NOW)
    _check("add refuses rather than overwrite an unreadable store",
           reply.startswith("Not scheduled") and path.read_text(encoding="utf-8") == "{not json", reply)
    raised = False
    try:
        count = await scheduled.deliver_due(_Sender(), now=NOW)
    except Exception:
        raised, count = True, None
    _check("delivery survives an unreadable store", not raised and count == 0, str(count))


async def test_react_wiring() -> None:
    from agents import react

    names = [t["name"] for t in react._TOOLS]
    _check("React offers scheduled_message", "scheduled_message" in names, str(names))
    _check("scheduled_message is switched off after a web read",
           "scheduled_message" in react._QUARANTINED_AFTER_WEB)
    _fresh_store()
    reply = await react._execute_tool("scheduled_message", {"action": "list"})
    _check("React dispatches the tool", not reply.startswith("Unknown tool"), reply)


def test_parse_tasks() -> None:
    """Every digest since has said "Open Tasks: None" while tasks.md held three.

    _parse_tasks matched only "- [ ]" checkboxes; memory_write writes plain bullets,
    so the writer and the reader disagreed and nobody noticed.
    """
    from agents import monitor

    content = (
        "# Tasks\n\n"
        "- Investigate X/Twitter fetching for the Research agent\n\n"
        "- [ ] Test write confirmation\n"
        "- [x] Something already finished\n\n"
        "## Deep Research Task - Ready to Run\n"
        "**Task:** Run a multi-hour research loop\n"
    )
    tasks = monitor._parse_tasks(content)
    _check("a plain bullet is a task", any("X/Twitter" in t for t in tasks), str(tasks))
    _check("a checkbox is still a task", any("write confirmation" in t for t in tasks), str(tasks))
    _check("a finished task is not listed", not any("already finished" in t for t in tasks), str(tasks))
    _check("headings and bold lines are not tasks",
           not any("Deep Research" in t or t.startswith("**") for t in tasks), str(tasks))
    _check("exactly the two open tasks", len(tasks) == 2, str(tasks))


def test_window_picks_the_time() -> None:
    """The user asked for "some random times". The model picked one number once and
    called it random. The code picks, inside a window the model names."""
    from agents import scheduled
    import clock

    def due_local(path):
        from datetime import datetime
        return datetime.fromisoformat(_stored(path)[0]["due"]).astimezone(clock.USER_TZ)

    # NOW is Friday 21:14 EDT. Evening is underway, so the pick must still be future.
    path = _fresh_store()
    scheduled.add("x", window="evening", now=NOW)
    picked = due_local(path)
    _check("an evening window picks a time later this evening",
           picked.day == 11 and picked.hour >= 21 and picked.hour < 23, str(picked))

    # Morning has already gone by at 21:14, so it belongs to tomorrow.
    path = _fresh_store()
    scheduled.add("x", window="morning", now=NOW)
    picked = due_local(path)
    _check("a morning window that has passed rolls to tomorrow",
           picked.day == 12 and 10 <= picked.hour < 12, str(picked))

    # Varied, not a fixed number dressed up as a choice.
    picks = set()
    for _ in range(12):
        _fresh_store()
        scheduled.add("x", window="tomorrow", now=NOW)
        picks.add(_stored(config.MEMORY_DIR / "scheduled.json")[0]["due"])
    _check("repeated windows give different times", len(picks) > 1, str(sorted(picks)[:3]))

    path = _fresh_store()
    reply = scheduled.add("x", window="whenever", now=NOW)
    _check("an unknown window is refused, not guessed",
           reply.startswith("Not scheduled") and _stored(path) == [], reply)


async def test_compose_at_delivery() -> None:
    """A message written on Monday cannot know about Thursday. Entries can carry a
    brief instead of fixed text, and the words are written when it sends."""
    from agents import scheduled

    path = _fresh_store()
    scheduled.add(brief="check how the deploy went", in_minutes=5, now=NOW)
    _check("a brief is stored instead of fixed text",
           _stored(path)[0].get("brief") == "check how the deploy went"
           and not _stored(path)[0].get("content"), str(_stored(path)))

    send = _Sender()
    seen = {}

    async def _compose(brief, now=None):
        seen["brief"] = brief
        return "How did the deploy end up going?"

    await scheduled.deliver_due(send, now=_utc(2026, 9, 12, 1, 20), compose=_compose)
    _check("the composer is given the brief", seen.get("brief") == "check how the deploy went", str(seen))
    _check("what sends is the composed message", send.sent == ["How did the deploy end up going?"], str(send.sent))

    # Composition is a model call, and a check-in that arrives plainly beats one
    # that vanishes because the model returned nothing.
    path = _fresh_store()
    scheduled.add(brief="check in about the deploy", in_minutes=5, now=NOW)

    async def _empty(brief, now=None):
        return None

    send = _Sender()
    await scheduled.deliver_due(send, now=_utc(2026, 9, 12, 1, 20), compose=_empty)
    _check("a composer that returns nothing falls back to the brief",
           len(send.sent) == 1 and "check in about the deploy" in send.sent[0], str(send.sent))
    _check("and the entry is still cleared", _stored(path) == [])

    path = _fresh_store()
    scheduled.add(brief="check in", in_minutes=5, now=NOW)

    async def _raises(brief, now=None):
        raise RuntimeError("model down")

    send = _Sender()
    await scheduled.deliver_due(send, now=_utc(2026, 9, 12, 1, 20), compose=_raises)
    _check("a composer that raises does not lose the message",
           len(send.sent) == 1 and "check in" in send.sent[0], str(send.sent))


def test_the_1am_message() -> None:
    """2026-10-14, replayed. Asked for thought-provoking material at 1am, IGOR sent
    content and brief together, was refused, and kept the pre-written words. Then
    "I said 1am" added a second entry beside the misread 10am one, and all of them
    sent - two at 1am, one at 10am."""
    from agents import scheduled

    oct14 = _utc(2026, 10, 14, 22, 11)
    path = _fresh_store()
    reply = scheduled.add("Imagine a single point of consciousness...", at="2026-10-15 01:00",
                          brief="Philosophical thought prompt", now=oct14)
    entries = _stored(path)
    _check("content and brief together is scheduled, not refused",
           len(entries) == 1 and not reply.startswith("Not scheduled"), reply)
    _check("and the brief is kept, so the words are written when it sends",
           bool(entries) and entries[0]["brief"] == "Philosophical thought prompt"
           and entries[0]["content"] == "", str(entries))

    path = _fresh_store()
    reply = scheduled.add(brief="thought provoking material", at="2026-10-15 10:00", now=oct14)
    _check("a first message lists nothing else as pending", "pending" not in reply, reply)
    wrong = _stored(path)[0]["id"]
    reply = scheduled.add(brief="thought provoking material", at="2026-10-15 01:00", now=oct14)
    _check("the correction shows the misread entry still pending, by id",
           wrong in reply and "10:00" in reply, reply)
    _check("and says both will send unless the old one is cancelled", "cancel" in reply, reply)
    _check("the confirmation of the new one still comes first",
           reply.startswith("Okay, tomorrow at 1:00 AM."), reply)


def test_react_cannot_prewrite() -> None:
    """2026-10-16, live: offered a content field, React pre-wrote the message three
    times out of three, once cancelling a correct brief to do it."""
    from agents import react, scheduled

    schema = next(t for t in react._TOOLS if t["name"] == "scheduled_message")
    _check("the tool offers no content field",
           "content" not in schema["input_schema"]["properties"], str(schema["input_schema"]["properties"].keys()))

    path = _fresh_store()
    scheduled.run_tool({"action": "add", "in_minutes": 90,
                        "content": "Imagine a single point of consciousness..."})
    entries = _stored(path)
    _check("a stray content from React is stored as a brief, written at send time",
           len(entries) == 1 and entries[0]["content"] == "" and entries[0]["brief"].startswith("Imagine"),
           str(entries))


def test_monitor_sees_pending() -> None:
    """2026-10-16, live: Monitor said nothing was scheduled while one was pending."""
    from agents import scheduled

    _fresh_store()
    _check("an empty store says none, plainly", scheduled.status() == "Messages scheduled for the user: none.",
           scheduled.status())
    scheduled.add(brief="something thought-provoking", at="2026-09-12 23:30", now=NOW)
    status = scheduled.status()
    _check("a pending message is listed with its local time and what it is for",
           "Sat 2026-09-12 23:30 EDT" in status and "thought-provoking" in status, status)


def test_same_slot_merges() -> None:
    """2026-10-16, live: React added a second 1am entry beside the first, though the
    add result listed it and said to cancel. Both would have sent."""
    from agents import scheduled

    oct14 = _utc(2026, 10, 14, 22, 11)
    path = _fresh_store()
    scheduled.add(brief="a philosophical prompt", at="2026-10-15 01:00", now=oct14)
    reply = scheduled.add(brief="a deeper one, written fresh", at="2026-10-15 01:00", now=oct14)
    entries = _stored(path)
    _check("a second request for the same time becomes one message", len(entries) == 1, str(entries))
    _check("and the merged brief keeps both requests",
           bool(entries) and "deeper" in entries[0]["brief"] and "philosophical" in entries[0]["brief"],
           str(entries))
    _check("and the user is told one message will send", "one message" in reply, reply)

    scheduled.add(brief="call mom", at="2026-10-15 09:00", now=oct14)
    _check("a request for a different time stays separate", len(_stored(path)) == 2, str(_stored(path)))


async def test_write_at_delivery() -> None:
    """The writer runs an agent like Hermes does at fire time, and every failure of
    that falls back - to compose, and past that to the brief - rather than losing
    the message."""
    from agents import compose, react

    seen = {}
    real_handle, real_compose = react.handle, compose.compose

    async def agent_ok(message, context, **kw):
        seen.update(kw, message=message, context=context)
        return "What would you keep if every belief you hold were replaced one by one?"

    async def agent_raises(*a, **kw):
        raise RuntimeError("groq down")

    async def agent_empty(*a, **kw):
        return "   "

    async def compose_stub(brief, now=None):
        return "composed fallback"

    try:
        compose.compose = compose_stub
        react.handle = agent_ok
        text = await compose.write("a deep philosophical question")
        _check("the agent's message is what sends", text.startswith("What would you keep"), text)
        _check("the agent gets the brief", "a deep philosophical question" in seen.get("message", ""), str(seen))
        _check("with no conversation history", seen.get("context") == [], str(seen.get("context")))
        banned = {"shell", "python_run", "write_file", "patch_file", "restart_self", "memory_write",
                  "send_message", "scheduled_message", "delegate_task", "task_update"}
        tools = set(seen.get("allowed_tools") or [])
        _check("and only read-only tools - nobody is watching when it runs",
               bool(tools) and not tools & banned, str(tools))

        react.handle = agent_raises
        _check("an agent that raises falls back to compose",
               await compose.write("x") == "composed fallback")
        react.handle = agent_empty
        _check("an agent that returns nothing falls back to compose",
               await compose.write("x") == "composed fallback")
    finally:
        react.handle, compose.compose = real_handle, real_compose


def test_update_in_place() -> None:
    """A correction is one call, as in Hermes. Cancel-then-add was two, and React
    skipped the cancel often enough that both messages sent."""
    from agents import scheduled

    oct14 = _utc(2026, 10, 14, 22, 11)
    path = _fresh_store()
    scheduled.add(brief="thought-provoking material", at="2026-10-15 10:00", now=oct14)
    eid = _stored(path)[0]["id"]

    reply = scheduled.update(eid, at="2026-10-15 01:00", now=oct14)
    entries = _stored(path)
    _check("update moves the time, and still one entry",
           len(entries) == 1 and entries[0]["due"] == "2026-10-15T05:00:00+00:00", str(entries))
    _check("and keeps the id and the brief",
           entries[0]["id"] == eid and entries[0]["brief"] == "thought-provoking material", str(entries))
    _check("the reply says the new time in words", "1:00 AM" in reply, reply)

    scheduled.update(eid, brief="deeper, written fresh, nothing about sleep", now=oct14)
    entries = _stored(path)
    _check("update changes the brief without moving the time",
           entries[0]["brief"].startswith("deeper") and entries[0]["due"] == "2026-10-15T05:00:00+00:00",
           str(entries))

    before = _stored(path)
    for name, kwargs in [
        ("an unknown id", dict(entry_id="nope", at="2026-10-15 02:00")),
        ("a time already past", dict(entry_id=eid, at="2026-10-14 08:00")),
        ("nothing to change", dict(entry_id=eid)),
        ("two times at once", dict(entry_id=eid, at="2026-10-15 02:00", in_minutes=5)),
    ]:
        reply = scheduled.update(now=oct14, **kwargs)
        _check(f"update refuses {name} and changes nothing",
               reply.startswith("Not updated") and _stored(path) == before, reply)

    path = _fresh_store()
    scheduled.run_tool({"action": "add", "in_minutes": 60, "brief": "x"})
    eid = _stored(path)[0]["id"]
    reply = scheduled.run_tool({"action": "update", "id": eid, "in_minutes": "90"})
    _check("React reaches update through the tool", reply.startswith("Okay, now"), reply)


if __name__ == "__main__":
    print("clock")
    test_clock()
    print("\nadd")
    test_add()
    test_add_rejects()
    print("\nlist and cancel")
    test_list_and_cancel()
    print("\ndelivery")
    asyncio.run(test_deliver())
    asyncio.run(test_delivery_failures())
    asyncio.run(test_add_during_send_survives())
    asyncio.run(test_corrupt_store())
    print("\nreact wiring")
    asyncio.run(test_react_wiring())
    print("\ndigest tasks")
    test_parse_tasks()
    print("\nwindows and composition")
    test_window_picks_the_time()
    asyncio.run(test_compose_at_delivery())
    print("\nthe 1am message")
    test_the_1am_message()
    test_react_cannot_prewrite()
    test_monitor_sees_pending()
    test_same_slot_merges()
    asyncio.run(test_write_at_delivery())
    test_update_in_place()

    if _failures:
        print(f"\n{len(_failures)} FAILED: {', '.join(_failures)}")
        sys.exit(1)
    print("\nall pass")
