"""execute_code: a script calls read-only tools, and only what it prints reaches the model.

Run: python tests/test_execute_code.py   (repo root, stdlib only, no network)

Hermes' programmatic tool calling. Every React step resends the prompt with its tool results,
so twelve lookups cost twelve whole model calls. A script makes them in one step. The danger is
the same as any turn holding the web and private data, so the same rules apply to each call the
script makes, and the sandbox hides the memory folder so nothing gets round them.

These run real child processes. The sandbox itself is Linux-only and is replaced here by a plain
interpreter; test_sandbox.py covers bubblewrap, and this was checked on the server too.
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


def _plain_sandbox():
    import sandbox
    real = sandbox.prepare
    sandbox.prepare = lambda argv, hide_memory=False: argv
    return lambda: setattr(sandbox, "prepare", real)


def _run(code, answers=None, timeout=20):
    import script_tool
    calls = []

    async def call_tool(name, args):
        calls.append((name, args))
        return (answers or {}).get(name, f"result of {name}")

    restore = _plain_sandbox()
    try:
        out = asyncio.run(script_tool.run(code, {"search": "query", "read_file": "path"}, call_tool, timeout=timeout))
    finally:
        restore()
    return out, calls


def test_the_channel() -> None:
    out, calls = _run("from igor_tools import search\nprint('got:', search('desks'))")
    _check("a tool call reaches the agent, and its answer the script", out.strip() == "got: result of search", out)
    _check("with the argument named", calls == [("search", {"query": "desks"})], str(calls))

    code = ("from igor_tools import search\nfound = [search(q) for q in ('a', 'b', 'c')]\n"
            "print(len(found), 'results')")
    out, calls = _run(code, answers={"search": "a very long page " * 100})
    _check("only what it prints comes back", out.strip() == "3 results", out[:100])
    _check("however much it read", len(calls) == 3, str(len(calls)))

    out, calls = _run("from igor_tools import search\nprint(search(queries=['a', 'b'], recency_days=30))")
    _check("keyword arguments pass through", calls == [("search", {"queries": ["a", "b"], "recency_days": 30})], str(calls))
    out, _ = _run("from igor_tools import read_file\nprint(read_file('x.md', offset=40))")
    _check("each tool has its own first argument", _ and _[0] == ("read_file", {"path": "x.md", "offset": 40}), str(_))
    out, _ = _run("from igor_tools import search\nprint(search('café – menü'))", answers={"search": "résumé — ok"})
    _check("text that is not ASCII survives both ways", "é" in out and _[0][1]["query"] == "café – menü", repr(out))


def test_when_a_script_goes_wrong() -> None:
    out, _ = _run("print('before')\nraise ValueError('broken')")
    _check("an exception comes back to be fixed", "ValueError: broken" in out and "before" in out, out)
    out, _ = _run("def broken(:\n  pass")
    _check("so does a syntax error", "SyntaxError" in out, out)
    out, _ = _run("x = 1")
    _check("printing nothing says so", "printed nothing" in out, out)
    out, _ = _run("from igor_tools import shell")
    _check("a tool it was not given does not exist", "ImportError" in out or "cannot import" in out, out)
    out, _ = _run("import sys\nsys.exit(3)")
    _check("sys.exit is not a crash", "printed nothing" in out, out)
    out, _ = _run("while True:\n    pass", timeout=2)
    _check("a script that never ends is stopped", "timed out after 2s" in out, out)
    out, _ = _run("print('x' * 50000)")
    _check("output is held to size and says so", len(out) < 3500 and "output cut" in out, str(len(out)))
    out, _ = _run("import os\nos.write(1, b'not json at all\\n')")
    _check("noise on the channel stops it cleanly", "the script failed" in out, out)
    out, _ = _run("import os\nos._exit(7)")
    _check("a crash says so", "exit code 7" in out, out)


def test_sandbox_unavailable() -> None:
    import sandbox
    import script_tool

    def down(argv, hide_memory=False):
        raise sandbox.Unavailable("NOT RUN: the sandbox could not start.")

    real = sandbox.prepare
    sandbox.prepare = down
    try:
        out = asyncio.run(script_tool.run("print(1)", {}, None))
    finally:
        sandbox.prepare = real
    _check("no sandbox, no script", out.startswith("NOT RUN"), out)


def test_the_sandbox_hides_memory() -> None:
    import sandbox
    plain = sandbox.command(["python3", "-c", "x"])
    hidden = sandbox.command(["python3", "-c", "x"], hide_memory=True)
    _check("a script's sandbox hides the whole memory folder",
           any(hidden[i] == "--tmpfs" and hidden[i + 1] == str(config.MEMORY_DIR) for i in range(len(hidden) - 1)), str(hidden))
    _check("python_run's does not", str(config.MEMORY_DIR) not in [plain[i + 1] for i in range(len(plain) - 1) if plain[i] == "--tmpfs"])
    _check("it still has no network", "--unshare-net" in hidden)


def _policy(private_read=False, job_turn=False, tools=None):
    """react._script_call against recorded tools, with its budget."""
    from agents import react
    ran = []
    # The real layout, where memory/ is under the root: the private check goes by the path.
    real_memory = config.MEMORY_DIR
    config.MEMORY_DIR = config.BASE_DIR / "memory"

    async def execute(name, inputs, allow_private=False):
        ran.append((name, inputs, allow_private))
        return f"{react._UNTRUSTED_OPEN}\nA page with a key {'gsk_' + 'a1B2c3D4' * 4}\n{react._UNTRUSTED_CLOSE}"

    real = react._execute_tool
    react._execute_tool = execute
    budget = {"left": react._SCRIPT_LOOKUPS}

    def call(name, args):
        return asyncio.run(react._script_call(name, args, budget, private_read, job_turn))

    def done():
        react._execute_tool = real
        config.MEMORY_DIR = real_memory
    return call, ran, budget, done


def test_the_rules_hold_inside_a_script() -> None:
    from agents import react
    call, ran, budget, done = _policy()
    try:
        out = call("search", {"query": "desks"})
        _check("a read-only tool runs", ran and ran[0][0] == "search" and "A page" in out, out)
        _check("never with private files open", ran[0][2] is False)
        _check("its result is scrubbed of keys", "gsk_" not in out and "[REDACTED]" in out, out)
        _check("and the markers do not nest", react._UNTRUSTED_OPEN not in out and react._UNTRUSTED_CLOSE not in out, out)
        ran.clear()
        for name, args in (("shell", {"command": "ls"}), ("write_file", {"path": "x", "content": "y"}),
                           ("memory_write", {"file": "tasks.md"}), ("send_message", {"content": "hi"}),
                           ("delegate_task", {"goal": "x"}), ("execute_code", {"code": "x"})):
            _check(f"{name} is not available", "not available" in call(name, args))
        _check("none of them ran", ran == [], str(ran))
        for name, args in (("read_file", {"path": "memory/resume.md"}), ("read_file", {"path": ".env"}),
                           ("read_file", {"path": "memory/private/profile.md"}), ("memory_read", {"file": "user.md"})):
            _check(f"{args} is closed", "closed" in call(name, args) and ran == [], str(ran))
    finally:
        done()

    call, ran, budget, done = _policy(private_read=True)
    try:
        _check("after a private read the web stays closed", "unavailable" in call("search", {"query": "x"}) and ran == [], str(ran))
        _check("and fetch_url", "unavailable" in call("fetch_url", {"url": "https://e.example"}) and ran == [], str(ran))
        call("read_file", {"path": "README.md"})
        _check("reading on is fine", ran and ran[0][0] == "read_file", str(ran))
    finally:
        done()

    call, ran, budget, done = _policy(job_turn=True)
    try:
        call("search", {"query": "qa roles"})
        _check("in a job search a search goes to the boards", ran[0][1].get("source") == "jobs", str(ran))
    finally:
        done()

    call, ran, budget, done = _policy()
    try:
        out = [call("search", {"query": str(n)}) for n in range(react._SCRIPT_LOOKUPS)]
        _check("the budget allows what it says", len(ran) == react._SCRIPT_LOOKUPS, str(len(ran)))
        ran.clear()
        over = call("fetch_url", {"url": "https://e.example"})
        _check("and then stops, saying so", "lookups" in over and ran == [], over)
    finally:
        done()
    call, ran, budget, done = _policy()
    try:
        call("search", {"queries": [str(n) for n in range(5)]})
        _check("several queries count as several", budget["left"] == react._SCRIPT_LOOKUPS - 5, str(budget))
        call("read_file", {"path": "README.md"})
        _check("reading a local file costs none", budget["left"] == react._SCRIPT_LOOKUPS - 5, str(budget))
        ran.clear()
        over = call("search", {"queries": [str(n) for n in range(9)]})
        _check("a batch past what is left does not run", "lookups" in over and ran == [], over)
    finally:
        done()


def test_inside_a_real_turn() -> None:
    from agents import react
    calls, seen = [], {}

    async def create(model, **kwargs):
        calls.append(kwargs)
        if len(calls) == 1:
            call = SimpleNamespace(id="c1", type="function", function=SimpleNamespace(
                name="load_tools", arguments=json.dumps({"names": ["execute_code"]})))
        elif len(calls) == 2:
            call = SimpleNamespace(id="c2", type="function", function=SimpleNamespace(
                name="execute_code", arguments=json.dumps({"code": "print('the answer is 42')"})))
        else:
            return SimpleNamespace(choices=[SimpleNamespace(finish_reason="stop", message=SimpleNamespace(
                content="Done.", tool_calls=None))], usage=None)
        return SimpleNamespace(choices=[SimpleNamespace(finish_reason="tool_calls", message=SimpleNamespace(
            content=None, tool_calls=[call]))], usage=None)

    fake = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=create)))
    real_client = react._get_client
    restore = _plain_sandbox()
    carry = {}
    try:
        react._get_client = lambda: fake
        asyncio.run(react.handle("Work something out.", [], carry=carry))
    finally:
        react._get_client = real_client
        restore()
    names = lambda c: {t["function"]["name"] for t in c.get("tools") or []}
    _check("it loads on request", "execute_code" not in names(calls[0]) and "execute_code" in names(calls[1]), str(names(calls[1])))
    result = [m for m in calls[2]["messages"] if m.get("role") == "tool"][-1]["content"]
    _check("what it printed comes back, marked untrusted",
           "the answer is 42" in result and result.startswith(react._UNTRUSTED_OPEN) and result.endswith(react._UNTRUSTED_CLOSE), result)
    _check("and the tools that change things close, as after a web read",
           not (names(calls[2]) & react._QUARANTINED_AFTER_WEB) and carry.get("web") is True, str(carry))
    _check("a worker cannot have it", "execute_code" in react._WORKER_FORBIDDEN)
    _check("it is not itself a tool that changes things", "execute_code" not in react._QUARANTINED_AFTER_WEB)


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            print(name)
            fn()
    if _failures:
        print(f"\n{len(_failures)} FAILED: {', '.join(_failures)}")
        sys.exit(1)
    print("\nall pass")
