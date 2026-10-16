"""Tests for delegate_task - React handing a job to an isolated worker.

Run: python tests/test_delegation.py   (repo root, stdlib only, no API calls)

The gap this closes: react.py:1069 gathers parallel *tool calls within one turn*,
which is not the same as a worker with its own context. Without that, every job has
to fit in one context window with one agent, which is why IGOR could not take on
anything large. The Hermes tool table calls this delegate_task.

Three things make it dangerous rather than merely useful, and each is a test here:

1. **Quarantine laundering.** A parent that has already read the web has lost its
   state-changing tools. If it could hand write_file to a worker, the rule would be
   one indirection deep and meaningless. Worker tools are intersected with the
   parent's CURRENT set, not the full list.
2. **Untrusted return.** A worker allowed on the web returns text the parent then
   acts on. That is web content entering the turn, so it closes the parent's
   state-changing tools exactly as a direct read would.
3. **Rate limits.** Groq free tier is 8000 TPM on shared buckets. Fanning workers
   out in parallel is the one sure way to 429 the hot path, so delegation is
   serialized and workers run on a different model from the parent.
"""

import asyncio
import sys
import tempfile
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


def _fresh():
    root = Path(tempfile.mkdtemp())
    (root / "memory").mkdir()
    config.BASE_DIR = root
    config.MEMORY_DIR = root / "memory"
    return root


ALL_TOOLS = ["search", "fetch_url", "read_file", "write_file", "patch_file", "shell",
             "python_run", "search_files", "search_memory", "memory_read",
             "memory_write", "send_message", "scheduled_message", "restart_self",
             "delegate_task"]


class _Runner:
    """Stands in for react.handle, recording how each worker was launched."""

    def __init__(self, reply="worker finished", fail=False, delay=0.0):
        self.calls = []
        self.reply = reply
        self.fail = fail
        self.delay = delay
        self.overlapped = False
        self._inside = 0

    async def __call__(self, message, context, **kw):
        self._inside += 1
        if self._inside > 1:
            self.overlapped = True
        self.calls.append({"message": message, "context": context, **kw})
        try:
            if self.delay:
                await asyncio.sleep(self.delay)
            if self.fail:
                raise RuntimeError("groq exploded")
            return self.reply
        finally:
            self._inside -= 1


def _run(coro):
    return asyncio.run(coro)


def test_the_worker_is_isolated() -> None:
    from agents import react

    _fresh()
    runner = _Runner("found three call sites")
    out = _run(react._delegate(
        goal="find every call site of _lowest_price",
        tools=["search_files", "read_file"],
        parent_tools=ALL_TOOLS, depth=0, run=runner))

    _check("the worker's answer comes back", "three call sites" in out, out)
    _check("exactly one worker ran", len(runner.calls) == 1, str(len(runner.calls)))

    call = runner.calls[0]
    _check("it starts with an empty context, which is the whole point",
           call["context"] == [], str(call["context"]))
    _check("the goal is what it was asked to do",
           "_lowest_price" in call["message"], call["message"])
    _check("it gets its own system prompt, not React's",
           call.get("system_override"), str(call.get("system_override"))[:80])
    _check("it is bounded in iterations",
           0 < call["max_iterations"] <= 8, str(call.get("max_iterations")))


def test_a_worker_runs_on_a_different_model_bucket() -> None:
    """react is gpt-oss-120b, shared with chat. A worker on the same model stacks
    two calls on one 8000 TPM bucket while the parent is mid-turn."""
    from agents import react

    _fresh()
    runner = _Runner()
    _run(react._delegate(goal="x", tools=["read_file"], parent_tools=ALL_TOOLS,
                         depth=0, run=runner))
    used = runner.calls[0].get("model")
    _check("the worker names a model explicitly", used, str(used))
    _check("and it is not the parent's react model",
           used != config.MODELS["react"], f"worker={used} react={config.MODELS['react']}")


def test_forbidden_tools_are_stripped() -> None:
    """A worker must not be able to reach the user, restart the service, schedule
    anything, write memory, or delegate again."""
    from agents import react

    _fresh()
    runner = _Runner()
    _run(react._delegate(
        goal="x",
        tools=["read_file", "send_message", "restart_self", "scheduled_message",
               "memory_write", "delegate_task", "task_update"],
        parent_tools=ALL_TOOLS, depth=0, run=runner))

    granted = runner.calls[0]["allowed_tools"]
    _check("the useful tool survives", "read_file" in granted, str(granted))
    for banned in ("send_message", "restart_self", "scheduled_message",
                   "memory_write", "delegate_task", "task_update"):
        _check(f"{banned} is withheld", banned not in granted, str(granted))

    # task_update was added after this list was written and was missed, which a live
    # end-to-end check caught rather than any offline test. Asserting the whole set
    # means the next state-changing tool cannot be forgotten the same way.
    _check("the forbidden set is exactly what is intended",
           react._WORKER_FORBIDDEN == frozenset({
               "delegate_task", "goal", "send_message", "scheduled_message", "restart_self",
               "memory_write", "task_update"}),
           str(sorted(react._WORKER_FORBIDDEN)))


def test_quarantine_cannot_be_laundered_through_a_worker() -> None:
    """The one that matters. A parent that has read the web has lost write_file.
    If it could delegate a write, the symmetric quarantine would be one indirection
    deep and worth nothing."""
    from agents import react

    _fresh()
    runner = _Runner()
    # What handle() passes once the web has closed its state-changing tools.
    after_web = [t for t in ALL_TOOLS if t not in react._QUARANTINED_AFTER_WEB]
    _run(react._delegate(
        goal="rewrite the config",
        tools=["write_file", "shell", "read_file"],
        parent_tools=after_web, depth=0, run=runner))

    granted = runner.calls[0]["allowed_tools"]
    _check("a tool the parent no longer holds is not granted",
           "write_file" not in granted, str(granted))
    _check("nor is shell", "shell" not in granted, str(granted))
    _check("what the parent still holds is granted",
           "read_file" in granted, str(granted))


def test_a_worker_cannot_delegate() -> None:
    from agents import react

    _fresh()
    runner = _Runner()
    out = _run(react._delegate(goal="x", tools=["read_file"], parent_tools=ALL_TOOLS,
                               depth=react._MAX_DELEGATION_DEPTH, run=runner))
    _check("the call is refused at the depth cap",
           "depth" in out.lower() or "cannot delegate" in out.lower(), out)
    _check("and no worker was started", not runner.calls, str(runner.calls))


def test_workers_do_not_run_in_parallel() -> None:
    """Fanning out on an 8000 TPM shared bucket is the one sure way to 429 the hot
    path while the user is waiting on the parent."""
    from agents import react

    _fresh()
    runner = _Runner(delay=0.05)

    async def two():
        return await asyncio.gather(
            react._delegate(goal="a", tools=["read_file"], parent_tools=ALL_TOOLS,
                            depth=0, run=runner),
            react._delegate(goal="b", tools=["read_file"], parent_tools=ALL_TOOLS,
                            depth=0, run=runner))

    _run(two())
    _check("both ran", len(runner.calls) == 2, str(len(runner.calls)))
    _check("but never at the same time", not runner.overlapped,
           "two workers overlapped - the TPM bucket will 429")


def test_a_broken_worker_is_not_a_crash() -> None:
    from agents import react

    _fresh()
    runner = _Runner(fail=True)
    out = _run(react._delegate(goal="x", tools=["read_file"], parent_tools=ALL_TOOLS,
                               depth=0, run=runner))
    _check("the failure comes back as text the model can read",
           "fail" in out.lower() or "error" in out.lower(), out)
    _check("and names nothing the parent should act on blindly",
           "groq exploded" in out or "RuntimeError" in out, out)


def test_an_empty_goal_is_refused() -> None:
    from agents import react

    _fresh()
    runner = _Runner()
    out = _run(react._delegate(goal="   ", tools=["read_file"], parent_tools=ALL_TOOLS,
                               depth=0, run=runner))
    _check("no worker is started without a goal", not runner.calls, str(runner.calls))
    _check("and it says so", "goal" in out.lower(), out)


def test_no_tools_left_is_refused() -> None:
    from agents import react

    _fresh()
    runner = _Runner()
    out = _run(react._delegate(goal="do a write", tools=["write_file"],
                               parent_tools=["read_file"], depth=0, run=runner))
    _check("a worker with nothing it may use is not started",
           not runner.calls, str(runner.calls))
    _check("and the refusal says which tools were unavailable",
           "write_file" in out, out)


def test_delegating_web_work_counts_as_reading_the_web() -> None:
    """A worker on the open web returns text the parent then acts on. That is
    untrusted content entering the turn by a longer route."""
    from agents import react

    _check("a delegation granting search is treated as a web read",
           react._delegation_wants_web({"tools": ["search", "read_file"]}))
    _check("so is fetch_url",
           react._delegation_wants_web({"tools": ["fetch_url"]}))
    _check("a local-only delegation is not",
           not react._delegation_wants_web({"tools": ["read_file", "search_files"]}))
    _check("neither is a malformed one",
           not react._delegation_wants_web({}))
    _check("search_files is local and must not be mistaken for the web",
           not react._delegation_wants_web({"tools": ["search_files"]}))


def test_the_tool_is_registered() -> None:
    from agents import react

    specs = {t["function"]["name"]: t for t in react._openai_tools()}
    _check("delegate_task is offered to the model", "delegate_task" in specs,
           str(list(specs)))
    spec = specs.get("delegate_task")
    if spec:
        props = spec["function"]["parameters"]["properties"]
        _check("it takes a goal", "goal" in props, str(props))
        _check("and a tool list", "tools" in props, str(props))
        _check("goal is required",
               "goal" in spec["function"]["parameters"].get("required", []), str(spec))

    _check("it is switched off after a web read, like other state-changing tools",
           "delegate_task" in react._QUARANTINED_AFTER_WEB,
           str(sorted(react._QUARANTINED_AFTER_WEB)))


if __name__ == "__main__":
    print("worker isolation")
    test_the_worker_is_isolated()
    print("\nmodel bucket")
    test_a_worker_runs_on_a_different_model_bucket()
    print("\nforbidden tools")
    test_forbidden_tools_are_stripped()
    print("\nquarantine laundering")
    test_quarantine_cannot_be_laundered_through_a_worker()
    print("\ndepth cap")
    test_a_worker_cannot_delegate()
    print("\nserialization")
    test_workers_do_not_run_in_parallel()
    print("\na broken worker")
    test_a_broken_worker_is_not_a_crash()
    print("\nbad input")
    test_an_empty_goal_is_refused()
    test_no_tools_left_is_refused()
    print("\nweb delegation closes the turn")
    test_delegating_web_work_counts_as_reading_the_web()
    print("\nregistration")
    test_the_tool_is_registered()

    if _failures:
        print(f"\n{len(_failures)} FAILED: {', '.join(_failures)}")
        sys.exit(1)
    print("\nall pass")
