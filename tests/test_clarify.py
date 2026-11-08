"""Asking the user mid-turn and waiting for the answer (Hermes' clarify).

Run: python tests/test_clarify.py   (repo root, stdlib only, no API calls)
"""

import asyncio
import json
import sys
import tempfile
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import config

config.MEMORY_DIR = Path(tempfile.mkdtemp())

import clarify

_failures: list[str] = []


def _check(name: str, condition: bool, detail: str = "") -> None:
    print(f"  {'pass' if condition else 'FAIL'}  {name}" + ("" if condition else f"  {detail}"))
    if not condition:
        _failures.append(name)


def _asking(reply_with, question="Which kettle?", choices=("Blue", "Red")):
    """Ask, with the user's reply arriving shortly after the question is sent."""
    sent, said = [], []

    async def send(text):
        sent.append(text)
        if reply_with is not None:
            asyncio.get_running_loop().call_later(0.01, lambda: said.append(clarify.answer(reply_with)))
        return True

    async def run():
        clarify.set_sender(send)
        return await clarify.ask(question, list(choices) if choices else None)

    got = asyncio.run(run())
    return got, sent, said


def test_a_choice_by_number_or_name() -> None:
    got, sent, said = _asking("2")
    _check("the question goes out numbered", sent and "Before I go on: Which kettle?" in sent[0]
           and "1. Blue" in sent[0] and "2. Red" in sent[0] and "skip" in sent[0], sent[0] if sent else "")
    _check("a number picks that choice", got == "The user chose 2: Red", got)
    _check("and the user hears it was taken", said == ["Got it: Red."], str(said))
    got, _, _ = _asking("blue")
    _check("so does the choice's own name", got == "The user chose 1: Blue", got)
    _check("nothing waits after", clarify.waiting() is None)


def test_open_and_skipped() -> None:
    got, sent, said = _asking("The one by the window, please", choices=None)
    _check("with no choices, any words are the answer", got == "The user answered, in their own words: "
           "The one by the window, please" and said == ["Got it."], f"{got} {said}")
    got, _, said = _asking("skip")
    _check("skip leaves it to IGOR", got.startswith("SKIPPED") and said == ["Understood, I will decide."], got)


def test_a_new_message_is_not_swallowed() -> None:
    got, _, said = _asking("Actually, what is the weather tomorrow?")
    _check("with choices, other words drop the question", got.startswith("NO ANSWER") and "new message" in got, got)
    _check("and are handled as a message of their own", said == [None], str(said))


def test_no_answer_and_stop() -> None:
    real = clarify.TIMEOUT_S
    try:
        clarify.TIMEOUT_S = 0.05
        got, _, _ = _asking(None)
        _check("no reply in time: go on, and say what was assumed", got.startswith("NO ANSWER") and "assumed" in got, got)
    finally:
        clarify.TIMEOUT_S = real

    async def stopped():
        async def send(text):
            asyncio.get_running_loop().call_later(0.01, clarify.cancel)
            return True
        clarify.set_sender(send)
        return await clarify.ask("Which kettle?", ["Blue", "Red"])
    got = asyncio.run(stopped())
    _check("stop ends the wait", got.startswith("NO ANSWER") and "Stop now" in got, got)


def test_what_it_refuses() -> None:
    clarify._send = None
    _check("with nowhere to send, it says so", asyncio.run(clarify.ask("Which?")).startswith("[clarify refused"))
    _check("no question, no wait", asyncio.run(clarify.ask("  ")).startswith("[clarify refused"))
    _check("with nothing waiting, a message is just a message", clarify.answer("2") is None)

    async def two_at_once():
        async def send(text):
            return True
        clarify.set_sender(send)
        first = asyncio.create_task(clarify.ask("Which kettle?", ["Blue", "Red"]))
        await asyncio.sleep(0.01)
        second = await clarify.ask("And which lid?")
        clarify.answer("1")
        return await first, second
    first, second = asyncio.run(two_at_once())
    _check("one question at a time", second.startswith("[clarify refused") and first == "The user chose 1: Blue",
           f"{first} | {second}")
    _check("choices are trimmed to four", clarify._clean_choices(["a", "b", "c", "d", "e", "a"]) == ["a", "b", "c", "d"])


def test_inside_a_turn() -> None:
    from agents import react
    calls = []

    async def create(model, **request):
        calls.append(request)
        if len(calls) == 1:
            call = SimpleNamespace(id="c1", type="function", function=SimpleNamespace(
                name="clarify", arguments=json.dumps({"question": "Which kettle?", "choices": ["Blue", "Red"]})))
            return SimpleNamespace(choices=[SimpleNamespace(
                finish_reason="tool_calls", message=SimpleNamespace(content=None, tool_calls=[call]))], usage=None)
        return SimpleNamespace(choices=[SimpleNamespace(
            finish_reason="stop", message=SimpleNamespace(content="Ordering the red one.", tool_calls=None))], usage=None)

    async def send(text):
        asyncio.get_running_loop().call_later(0.01, lambda: clarify.answer("2"))
        return True

    fake = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=create)))
    real_client = react._get_client
    try:
        react._get_client = lambda: fake
        clarify.set_sender(send)
        reply = asyncio.run(react.handle("Order me a kettle", []))
    finally:
        react._get_client = real_client
    _check("the turn is offered clarify", "clarify" in {t["function"]["name"] for t in calls[0]["tools"]})
    result = next(m["content"] for m in calls[1]["messages"] if m.get("role") == "tool")
    _check("the answer comes back to the same turn", result == "The user chose 2: Red", result)
    _check("which goes on to finish", reply.startswith("Ordering the red one."), reply)
    _check("a worker cannot ask", "clarify" in react._WORKER_FORBIDDEN)


def test_the_route() -> None:
    import orchestrator

    async def notify(_):
        pass

    async def run():
        o = orchestrator.Orchestrator(notify)

        async def send(text):
            return True
        clarify.set_sender(send)
        waiting = asyncio.create_task(clarify.ask("Which kettle?", ["Blue", "Red"]))
        await asyncio.sleep(0.01)
        said = await o.process(config.AUTHORIZED_USER_ID, "1")
        return said, await waiting
    said, got = asyncio.run(run())
    _check("the next message answers the waiting question, before any turn",
           said == ("Got it: Blue.", False) and got == "The user chose 1: Blue", f"{said} {got}")


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            print(name)
            fn()
    if _failures:
        print(f"\n{len(_failures)} FAILED: {', '.join(_failures)}")
        sys.exit(1)
    print("\nall pass")
