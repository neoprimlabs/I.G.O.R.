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
    _check("confirmation shows the local time back", "2026-09-12 14:00 EDT" in reply, reply)

    path = _fresh_store()
    oct30 = _utc(2026, 10, 30, 12, 0)
    reply = scheduled.add("Checking in.", at="2026-11-02 14:00", now=oct30)
    entries = _stored(path)
    _check("2pm Eastern after the November switch is stored as 19:00 UTC",
           len(entries) == 1 and entries[0]["due"] == "2026-11-02T19:00:00+00:00", str(entries))
    _check("confirmation says EST after the switch", "2026-11-02 14:00 EST" in reply, reply)

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

    if _failures:
        print(f"\n{len(_failures)} FAILED: {', '.join(_failures)}")
        sys.exit(1)
    print("\nall pass")
