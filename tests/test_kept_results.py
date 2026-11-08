"""A tool result too long for the context is kept whole for the turn (Hermes' tool_result_storage).

Run: python tests/test_kept_results.py   (repo root, stdlib only, no API calls)

IGOR cut every result at 4000 characters and the rest was gone. Hermes saves it and hands back the
start with where the rest is. Here it is held in memory for the turn and read on with read_file
"result:N", so web text never outlives the turn that read it, past the quarantine.
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

_failures: list[str] = []


def _check(name: str, condition: bool, detail: str = "") -> None:
    print(f"  {'pass' if condition else 'FAIL'}  {name}" + ("" if condition else f"  {detail}"))
    if not condition:
        _failures.append(name)


PAGE = " ".join(f"kettle{i}" for i in range(2000))


def _tool_call(name, args, n):
    call = SimpleNamespace(id=f"c{n}", type="function",
                           function=SimpleNamespace(name=name, arguments=json.dumps(args)))
    return SimpleNamespace(choices=[SimpleNamespace(
        finish_reason="tool_calls", message=SimpleNamespace(content=None, tool_calls=[call]))], usage=None)


def _handle(script, message="Look up the kettle page.", **kwargs):
    from agents import react
    calls = []

    async def create(model, **request):
        calls.append(request)
        step = script[min(len(calls), len(script)) - 1]
        if isinstance(step, tuple):
            return _tool_call(step[0], step[1], len(calls))
        return SimpleNamespace(choices=[SimpleNamespace(
            finish_reason="stop", message=SimpleNamespace(content=step, tool_calls=None))], usage=None)

    fake = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=create)))
    real_execute = react._execute_tool

    async def execute(name, inputs, allow_private=False):
        if name == "search":
            return f"{react._UNTRUSTED_OPEN}\n{PAGE}\n{react._UNTRUSTED_CLOSE}"
        if name == "shell":
            return "line\n" * 2000
        return await real_execute(name, inputs, allow_private)

    real_client = react._get_client
    try:
        react._get_client, react._execute_tool = (lambda: fake), execute
        reply = asyncio.run(react.handle(message, [], **kwargs))
    finally:
        react._get_client, react._execute_tool = real_client, real_execute
    return reply, calls


def _tool_results(call):
    return [m["content"] for m in call["messages"] if m.get("role") == "tool"]


def test_a_long_web_result_is_kept_for_the_turn() -> None:
    from agents import react
    reply, calls = _handle([("search", {"query": "kettle"}),
                            ("read_file", {"path": "result:1", "offset": 3600}), "Done."])
    first = _tool_results(calls[1])[0]
    _check("the model gets the start, still marked as web text",
           first.startswith(react._UNTRUSTED_OPEN) and react._UNTRUSTED_CLOSE in first and "kettle0 " in first, first[:120])
    _check("and is told how long it is and how to read on",
           f"is {len(PAGE)} characters" in first and 'path "result:1"' in first and "offset=3600" in first, first[-260:])
    _check("which fits the room a result has", len(first) <= react._TOOL_RESULT_CAP, str(len(first)))
    nxt = _tool_results(calls[2])[1]
    _check("read on, it carries on from that point", PAGE[3600:3640] in nxt, nxt[:200])
    _check("still marked as web text", nxt.startswith(react._UNTRUSTED_OPEN) and react._UNTRUSTED_CLOSE in nxt)
    _check("and says where the next part starts", "offset=7300" in nxt, nxt[-200:])
    _check("nothing kept once the turn is over", react._SPILLED.get() is None)
    reply, calls = _handle([("read_file", {"path": "result:1"}), "Done."])
    gone = _tool_results(calls[1])[0]
    _check("a later turn cannot read it", gone.startswith("[not found: result:1"), gone)


def test_ordinary_output_is_kept_unmarked() -> None:
    reply, calls = _handle([("shell", {"command": "cat big.log"}), ("read_file", {"path": "result:1", "offset": 3600}),
                            "Done."])
    from agents import react
    first = _tool_results(calls[1])[0]
    _check("a long shell output is kept too, not marked as web text",
           "result:1" in first and react._UNTRUSTED_OPEN not in first, first[-200:])
    _check("and read on", "line" in _tool_results(calls[2])[1])


def test_where_read_file_is_not_offered_it_is_cut_as_before() -> None:
    reply, calls = _handle([("search", {"query": "kettle"}), "Done."], allowed_tools=["search"])
    first = _tool_results(calls[1])[0]
    _check("no read_file, no pointer to it: cut as before", "result:1" not in first and "[truncated" in first,
           first[-150:])


def test_outside_a_turn() -> None:
    from agents import react
    _check("outside a turn there is nothing kept", react._read_spilled("result:1", 0).startswith("[not found"))
    cut = react._spill("search", "x" * 5000, can_read=True)
    _check("and a long result is cut as before", "[truncated" in cut and len(cut) < 4100, cut[-120:])


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            print(name)
            fn()
    if _failures:
        print(f"\n{len(_failures)} FAILED: {', '.join(_failures)}")
        sys.exit(1)
    print("\nall pass")
