"""Commands in code, and one turn at a time.

Run: python tests/test_commands.py   (repo root, stdlib only, no API calls)

Hermes' gateway has /stop, /retry, /undo, /usage, /status and /help, none of which spends a
token. Here the same question cost a router call and a 120b call. And two messages sent close
together ran at once, sharing one 8000 TPM bucket and able to answer out of order, so they now
queue, as Hermes' queue mode does, with stop for the turn in flight.
"""

import asyncio
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import config

config.MEMORY_DIR = Path(tempfile.mkdtemp())

import context_store

context_store._DB_PATH = config.MEMORY_DIR / "context.db"

_failures: list[str] = []


def _check(name: str, condition: bool, detail: str = "") -> None:
    print(f"  {'pass' if condition else 'FAIL'}  {name}" + ("" if condition else f"  {detail}"))
    if not condition:
        _failures.append(name)


USER = config.AUTHORIZED_USER_ID


def _orchestrator(route=None):
    import orchestrator

    async def notify(_):
        pass

    context_store.clear()
    o = orchestrator.Orchestrator(notify)

    async def no_router(content):
        return "React"

    async def no_model(*a, **k):
        raise AssertionError("a model was called")

    o._classify = no_router
    o._client.chat.completions.create = no_model
    if route is not None:
        o._route = route
    return o


def test_parse() -> None:
    import commands
    for text, expected in (("stop", "stop"), ("/stop", "stop"), ("Stop.", "stop"), (" retry ", "retry"), ("/UNDO", "undo"),
                           ("usage", "usage"), ("status?", "status"), ("help", "help")):
        _check(f"{text!r} is {expected}", commands.parse(text) == expected, str(commands.parse(text)))
    for text in ("stop the car", "help me find a desk", "status of my order", "please retry", "", None, "undo that last change"):
        _check(f"{text!r} is an ordinary message", commands.parse(text) is None)


def test_answered_without_a_model() -> None:
    async def no_route(*a, **k):
        raise AssertionError("a turn was started")

    o = _orchestrator(no_route)

    async def run():
        help_text, _ = await o.process(USER, "help")
        usage_text, _ = await o.process(USER, "/usage")
        status_text, _ = await o.process(USER, "status")
        return help_text, usage_text, status_text
    help_text, usage_text, status_text = asyncio.run(run())
    _check("help names the commands", all(w in help_text for w in ("stop", "retry", "undo", "usage", "status", "rollback")), help_text)
    _check("usage reads the ledger", "Groq tokens" in usage_text, usage_text)
    _check("status says nothing is running", status_text.startswith("Nothing is running."), status_text[:100])
    _check("and shows the status block", "Scheduler:" in status_text and "Groq tokens" in status_text, status_text[:200])
    _check("none of it enters the conversation", context_store.load_timed(5) == [], str(context_store.load_timed(5)))


def test_one_turn_at_a_time() -> None:
    order = []
    release = {}

    async def route(destination, content, file_mode=False, carry=None):
        order.append(f"start {content}")
        release[content] = asyncio.Event()
        await release[content].wait()
        order.append(f"end {content}")
        return f"reply to {content}"

    o = _orchestrator(route)

    async def run():
        first = asyncio.create_task(o.process(USER, "first"))
        await asyncio.sleep(0)
        await asyncio.sleep(0)
        second = asyncio.create_task(o.process(USER, "second"))
        await asyncio.sleep(0)
        await asyncio.sleep(0)
        waiting = list(order)
        usage_text, _ = await o.process(USER, "usage")
        release["first"].set()
        one = await first
        for _ in range(5):
            await asyncio.sleep(0)
        release["second"].set()
        two = await second
        return waiting, usage_text, one, two
    waiting, usage_text, one, two = asyncio.run(run())
    _check("a second message waits for the first", waiting == ["start first"], str(waiting))
    _check("a command does not wait", "Groq tokens" in usage_text, usage_text)
    _check("they run in the order sent", order == ["start first", "end first", "start second", "end second"], str(order))
    _check("each gets its own reply", one[0].startswith("reply to first") and two[0].startswith("reply to second"), f"{one} {two}")


def test_stop() -> None:
    async def route(destination, content, file_mode=False, carry=None):
        began.set()
        await carry["stop"].wait()
        return ""

    o = _orchestrator(route)

    async def run():
        global began
        began = asyncio.Event()
        turn = asyncio.create_task(o.process(USER, "a long job"))
        await began.wait()
        status_text, _ = await o.process(USER, "status")
        reply, _ = await o.process(USER, "stop")
        return status_text, reply, await turn
    status_text, reply, result = asyncio.run(run())
    _check("status sees the turn", status_text.startswith("Working on a message."), status_text[:60])
    _check("stop answers at once", reply == "Stopping at the next step.", reply)
    _check("a stopped turn sends nothing", result is None, str(result))
    stored = context_store.load_timed(5)
    _check("it is on record that it was stopped",
           stored[0]["content"] == "a long job" and "stopped by the user" in stored[1]["content"], str(stored))
    _check("with nothing running, stop says so", asyncio.run(o.process(USER, "stop"))[0] == "Nothing is running.")

    from agents import research_loop
    done = asyncio.Event()

    async def spin():
        await done.wait()

    async def with_research():
        research_loop._loop_task = asyncio.create_task(spin())
        research_loop._stop_event = asyncio.Event()
        reply, _ = await o.process(USER, "stop")
        return reply, research_loop._loop_task
    reply, _ = asyncio.run(with_research())
    research_loop._loop_task, research_loop._stop_event = None, None
    _check("stop reaches a research loop too", "esearch" in reply, reply)

    async def finished(destination, content, file_mode=False, carry=None):
        began.set()
        await carry["stop"].wait()
        return "The answer it had already."

    o = _orchestrator(finished)

    async def run_finished():
        global began
        began = asyncio.Event()
        turn = asyncio.create_task(o.process(USER, "a job"))
        await began.wait()
        await o.process(USER, "stop")
        return await turn
    result = asyncio.run(run_finished())
    _check("an answer already written still goes out", result and result[0].startswith("The answer it had"), str(result))


def test_stop_reaches_the_follow_through() -> None:
    o = _orchestrator()
    follow = asyncio.Event()
    o._stop_follow = follow
    reply, _ = asyncio.run(o.process(USER, "stop"))
    _check("stop ends a follow-through", follow.is_set() and reply == "Stopping at the next step.", reply)


def test_undo_and_retry() -> None:
    calls = []

    async def route(destination, content, file_mode=False, carry=None):
        calls.append((destination, content))
        return f"reply {len(calls)}"

    o = _orchestrator(route)
    _check("nothing to undo says so", "nothing" in asyncio.run(o.process(USER, "undo"))[0].lower())
    _check("nothing to retry says so", "no message" in asyncio.run(o.process(USER, "retry"))[0].lower())

    asyncio.run(o.process(USER, "keep this"))
    asyncio.run(o.process(USER, "a mistake"))
    o.record_outbound("Morning digest")
    reply, _ = asyncio.run(o.process(USER, "undo"))
    stored = [m["content"] for m in context_store.load_timed(10)]
    _check("undo names what it removed", "a mistake" in reply, reply)
    _check("and says files stay as they are", "rollback" in reply, reply)
    _check("the exchange is gone", "a mistake" not in stored and "reply 2" not in stored, str(stored))
    _check("an earlier exchange stays", "keep this" in stored and "reply 1" in stored, str(stored))
    _check("and a post sent since stays", any("Morning digest" in s for s in stored), str(stored))
    _check("the prompt's copy matches", [m["content"] for m in o._context] == stored, str(o._context))

    calls.clear()
    reply, _ = asyncio.run(o.process(USER, "/retry"))
    stored = [m["content"] for m in context_store.load_timed(10)]
    _check("retry runs the last message again", calls == [("React", "keep this")] and reply.startswith("reply 1"), f"{calls} {reply}")
    _check("in place of the old exchange", stored.count("keep this") == 1, str(stored))

    seen = {}
    document = "[Document the user sent: a.txt - saved.]\nThe text."
    asyncio.run(o.process(USER, document, prefer_tools=True))

    async def classify(content):
        seen["classified"] = True
        return "Direct"
    o._classify = classify
    calls.clear()
    asyncio.run(o.process(USER, "retry"))
    _check("a retried document goes to the tool agent again", calls[0][0] == "React" and "classified" not in seen, str(calls))


def test_the_store() -> None:
    context_store.clear()
    _check("an empty store removes nothing", context_store.remove_last_exchange() is None)
    for role, content in (("user", "one"), ("assistant", "two"), ("user", "three"), ("assistant", "four"),
                          ("assistant", "a round's reply"), ("assistant", "[sent proactively]\nA post")):
        context_store.append(role, content)
    _check("it returns the user's words", context_store.remove_last_exchange() == "three")
    stored = [m["content"] for m in context_store.load_timed(10)]
    _check("every reply to it goes, the post stays", stored == ["one", "two", "[sent proactively]\nA post"], str(stored))


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            print(name)
            fn()
    if _failures:
        print(f"\n{len(_failures)} FAILED: {', '.join(_failures)}")
        sys.exit(1)
    print("\nall pass")
