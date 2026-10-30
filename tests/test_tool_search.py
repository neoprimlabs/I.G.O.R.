"""React starts with the tools it uses and loads the rest on request (Hermes' Tool Search).

Run: python tests/test_tool_search.py   (repo root, stdlib only, no API calls)

Measured 2026-10-30: schemas were 2770 of the 4714 tokens every React call starts with, on
an 8000 TPM bucket where a 429 means a wait of 20 to 50 seconds. Over the journal, eight tools
took all but two of 105 calls and eleven were never called.
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


CORE = {"search", "fetch_url", "read_file", "memory_read", "search_memory", "memory_write",
        "scheduled_message", "shell"}


def _names(call) -> set:
    return {t["function"]["name"] for t in call.get("tools") or []}


def _tool_call(name, args, n):
    call = SimpleNamespace(id=f"c{n}", type="function",
                           function=SimpleNamespace(name=name, arguments=json.dumps(args)))
    return SimpleNamespace(choices=[SimpleNamespace(
        finish_reason="tool_calls", message=SimpleNamespace(content=None, tool_calls=[call]))], usage=None)


def _handle(script, message="Do the thing.", **kwargs):
    """React against a scripted model. A step is (tool, args), a final string, or an exception."""
    from agents import react
    calls = []

    async def create(model, **request):
        calls.append(request)
        step = script[min(len(calls), len(script)) - 1]
        if isinstance(step, Exception):
            raise step
        if isinstance(step, tuple):
            return _tool_call(step[0], step[1], len(calls))
        return SimpleNamespace(choices=[SimpleNamespace(
            finish_reason="stop", message=SimpleNamespace(content=step, tool_calls=None))], usage=None)

    fake = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=create)))
    ran = []

    async def execute(name, inputs, allow_private=False):
        ran.append(name)
        return f"{react._UNTRUSTED_OPEN}\nA page.\n{react._UNTRUSTED_CLOSE}" if name == "search" else "ok"

    real_client, real_execute = react._get_client, react._execute_tool
    try:
        react._get_client, react._execute_tool = (lambda: fake), execute
        reply = asyncio.run(react.handle(message, [], **kwargs))
    finally:
        react._get_client, react._execute_tool = real_client, real_execute
    return reply, calls, ran


def test_the_catalog() -> None:
    from agents import react
    every = {t["name"] for t in react._TOOLS}
    deferred = set(react._DEFERRED_TOOLS)
    _check("every deferred tool is a real tool", deferred <= every, str(deferred - every))
    _check("core and deferred are the whole list, with nothing in both",
           CORE | deferred == every and not CORE & deferred, str(every ^ (CORE | deferred)))
    description = react._LOAD_TOOLS["function"]["description"]
    _check("the bridge names every deferred tool", all(name in description for name in deferred), description)
    _check("the search tool, which writes no receipts, is not deferred", "search" not in deferred)

    sizes = {t["function"]["name"]: len(json.dumps(t)) for t in react._openai_tools()}
    start = sum(sizes[name] for name in CORE) + len(json.dumps(react._LOAD_TOOLS))
    _check("a call starts with well under two thirds of the schemas",
           start < sum(sizes.values()) * 0.66, f"{start} of {sum(sizes.values())}")
    prompt = react._DEFAULT_SYSTEM_PROMPT
    for moved in ("Self-modification workflow", "- write_file:", "- proposal:", "- goal:", "- watch_history:"):
        _check(f"{moved!r} no longer rides in every prompt", moved not in prompt)
    _check("the prompt says where the rest are", "- load_tools:" in prompt and "search_history (load it first)" in prompt)
    _check("and the user is never 'him'", " him " not in prompt)


def test_a_turn_starts_with_the_core() -> None:
    reply, calls, _ = _handle(["Done."])
    _check("the first call offers the core and the bridge", _names(calls[0]) == CORE | {"load_tools"},
           str(_names(calls[0])))


def test_loading_opens_a_tool_for_the_turn() -> None:
    from agents import react
    reply, calls, ran = _handle([("load_tools", {"names": ["task_update", "write_file"]}),
                                 ("task_update", {"action": "list"}), "Done."])
    _check("the next call offers what was loaded", {"task_update", "write_file"} <= _names(calls[1]), str(_names(calls[1])))
    _check("and keeps it after that", "task_update" in _names(calls[2]))
    _check("but nothing else", "patch_file" not in _names(calls[1]) and "search_history" not in _names(calls[1]))
    _check("the bridge itself runs no tool", ran == ["task_update"], str(ran))
    result = next(m for m in calls[1]["messages"] if m.get("role") == "tool")["content"]
    _check("a tool's rules come back with it", "needs deploying" in result and "explicitly asks" in result, result)
    loaded = set()
    tools = react._openai_tools()
    _check("and so does a shorter rule", "goal: loaded. add records it" in react._load_tools(["goal"], tools, set()))
    _check("an unknown tool is named as such", "no such tool" in react._load_tools(["teleport"], tools, loaded) and not loaded)
    _check("a core tool is already there", "already available" in react._load_tools(["search"], tools, loaded))
    _check("a bare name is forgiven", "loaded" in react._load_tools("goal", tools, loaded) and loaded == {"goal"})
    _check("an empty request says what to do", "at least one" in react._load_tools([], tools, set()))


def test_the_quarantine_holds() -> None:
    reply, calls, ran = _handle([("search", {"query": "desks"}), ("load_tools", {"names": ["write_file", "search_history"]}),
                                 ("write_file", {"path": "x", "content": "y"}), "Done."])
    result = [m for m in calls[2]["messages"] if m.get("role") == "tool"][-1]["content"]
    _check("after a web read a closed tool cannot be loaded", "write_file: closed" in result, result)
    _check("what is still open can", "search_history: loaded" in result, result)
    _check("it is not offered", "write_file" not in _names(calls[2]), str(_names(calls[2])))
    _check("and refused if called anyway", "write_file" not in ran, str(ran))
    reply, calls, ran = _handle(["Done."], carry={"private": True})
    _check("a turn closed to the web still has the bridge", "load_tools" in _names(calls[0]) and "search" not in _names(calls[0]))


def test_a_direct_call_loads_and_retries() -> None:
    """Groq rejects a call to a tool not in the request, as tool_use_failed."""
    import httpx
    import openai
    refused = openai.BadRequestError(
        "Error code: 400 - tool_use_failed: attempted to call tool 'task_update' which was not in request.tools",
        response=httpx.Response(400, request=httpx.Request("POST", "http://groq")), body=None)
    reply, calls, ran = _handle([refused, ("task_update", {"action": "list"}), "Done."])
    _check("the tool it named is offered on the retry", "task_update" in _names(calls[1]), str(_names(calls[1])))
    _check("and runs", ran == ["task_update"], str(ran))
    other = openai.BadRequestError("Error code: 400 - tool_use_failed: attempted to call tool 'teleport'",
                                   response=httpx.Response(400, request=httpx.Request("POST", "http://groq")), body=None)
    reply, calls, ran = _handle([other, "Done."])
    _check("a tool that does not exist opens nothing", _names(calls[1]) == CORE | {"load_tools"}, str(_names(calls[1])))


def test_others_are_left_alone() -> None:
    reply, calls, _ = _handle(["Done."], allowed_tools=["search", "search_files"])
    _check("a named list is offered as named, with no bridge", _names(calls[0]) == {"search", "search_files"}, str(_names(calls[0])))
    reply, calls, _ = _handle(["Done."], _depth=1)
    _check("a worker gets no bridge", "load_tools" not in _names(calls[0]) and "search_files" in _names(calls[0]))
    reply, calls, _ = _handle(["Done."], force_tool="goal")
    _check("a forced tool is offered from the start", "goal" in _names(calls[0]) and calls[0].get("tool_choice"), str(_names(calls[0])))


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            print(name)
            fn()
    if _failures:
        print(f"\n{len(_failures)} FAILED: {', '.join(_failures)}")
        sys.exit(1)
    print("\nall pass")
