"""Long commands, and the folder commands share (Hermes' terminal background and persistence).

Run: python tests/test_background.py   (repo root, stdlib only, no API calls)

Elsewhere the sandbox is stood in for by a plain Python subprocess. On the server, with bubblewrap,
the second half runs the real thing: a command's file lasts, and a scheduled script cannot write there.
"""

import asyncio
import os
import shutil
import sys
import tempfile
import time
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import config

config.MEMORY_DIR = Path(tempfile.mkdtemp())

import background
import sandbox

_failures: list[str] = []


def _check(name: str, condition: bool, detail: str = "") -> None:
    print(f"  {'pass' if condition else 'FAIL'}  {name}" + ("" if condition else f"  {detail}"))
    if not condition:
        _failures.append(name)


def _stand_in(argv, **kw):
    """The command after `bash -c` is Python source here, so this runs on any machine."""
    return [sys.executable, "-c", argv[-1]]


def _reset():
    background._jobs.clear()
    shutil.rmtree(background._folder(), ignore_errors=True)


async def _settle(job_id):
    await background._jobs[job_id]["task"]


def _job_id(reply: str) -> str:
    return reply.split("id ")[1].split(".")[0]


def test_lifecycle() -> None:
    _reset()
    real = sandbox.prepare
    sandbox.prepare = _stand_in
    told = []

    async def notify(text):
        told.append(text)

    async def run():
        reply = await background.start("print('working'); print('line two')", notify)
        job_id = _job_id(reply)
        await _settle(job_id)
        return reply, job_id

    try:
        reply, job_id = asyncio.run(run())
    finally:
        sandbox.prepare = real
    _check("it says where it stands", reply.startswith("Started in the background") and "do not wait" in reply, reply)
    _check("the user is told when it ends", len(told) == 1 and "has finished" in told[0], str(told))
    _check("with its exit code and output", "exit code 0" in told[0] and "line two" in told[0], str(told))
    _check("list shows it", job_id in background.listing() and "exit code 0" in background.listing(), background.listing())
    poll = background.run_tool({"action": "poll", "id": job_id})
    _check("poll gives what is new", "working" in poll, poll)
    poll = background.run_tool({"action": "poll", "id": job_id})
    _check("and nothing twice", "(none)" in poll, poll)
    log = background.run_tool({"action": "log", "id": job_id})
    _check("log reads from the start", "working" in log, log)
    log = background.run_tool({"action": "log", "id": job_id, "offset": "bad"})
    _check("a bad offset is the start", "working" in log, log)
    _check("no such id says so", background.run_tool({"action": "poll", "id": "nope"}).startswith("[no background command"))
    _check("nor an unknown action", background.run_tool({"action": "write", "id": job_id}).startswith("[use list"))
    _check("killing a finished one is harmless", "already" in background.run_tool({"action": "kill", "id": job_id}))


def test_failure_and_kill() -> None:
    _reset()
    real = sandbox.prepare
    sandbox.prepare = _stand_in
    told = []

    async def notify(text):
        told.append(text)

    async def run():
        failed = _job_id(await background.start("import sys; print('bad'); sys.exit(3)", notify))
        await _settle(failed)
        quiet = _job_id(await background.start("pass", notify))
        await _settle(quiet)
        slow = _job_id(await background.start("import time; time.sleep(60)", notify))
        busy = _job_id(await background.start("import time; time.sleep(60)", notify))
        third = await background.start("print('third')", notify)
        running = background.listing()
        stopped = background.run_tool({"action": "kill", "id": slow})
        background.run_tool({"action": "kill", "id": busy})
        await _settle(slow)
        await _settle(busy)
        return third, running, stopped, slow

    try:
        third, running, stopped, slow = asyncio.run(run())
    finally:
        sandbox.prepare = real
    _check("a failure is reported as one", "exit code 3" in told[0] and "bad" in told[0], str(told))
    _check("a silent command says so", "printed nothing" in told[1], str(told))
    _check("two at a time", third.startswith("NOT RUN") and "already running" in third, third)
    _check("running ones show", "running for" in running, running)
    _check("a kill says so", stopped == f"Stopped {slow}.", stopped)
    _check("and tells nobody twice", len(told) == 2, str(told))
    _check("it reads as stopped", "stopped on request" in background.listing(), background.listing())


def test_time_limit() -> None:
    _reset()
    real, limit = sandbox.prepare, background._MAX_SECONDS
    sandbox.prepare, background._MAX_SECONDS = _stand_in, 0.2
    told = []

    async def notify(text):
        told.append(text)

    async def run():
        job_id = _job_id(await background.start("import time; print('begun', flush=True); time.sleep(60)", notify))
        await _settle(job_id)

    try:
        asyncio.run(run())
    finally:
        sandbox.prepare, background._MAX_SECONDS = real, limit
    _check("a command past the limit is stopped, and the user told", len(told) == 1 and "limit" in told[0], str(told))


def test_cannot_start() -> None:
    _reset()
    real = sandbox.prepare

    def unavailable(argv, **kw):
        raise sandbox.Unavailable("NOT RUN: no sandbox")

    sandbox.prepare = unavailable
    try:
        reply = asyncio.run(background.start("true", None))
    finally:
        sandbox.prepare = real
    _check("no sandbox, no command: never bare", reply == "NOT RUN: no sandbox" and not background._jobs, reply)


def test_a_restart() -> None:
    _reset()
    background._folder().mkdir(parents=True)
    background._running_path().write_text('[{"id": "abc", "command": "run the tests"}]', encoding="utf-8")
    text = background.lost_in_restart()
    _check("the next start says what a restart ended", "run the tests" in text and "1 background command" in text, text)
    _check("once", background.lost_in_restart() == "")
    background._running_path().write_text("not json", encoding="utf-8")
    _check("a bad file is no news", background.lost_in_restart() == "")
    _reset()
    _check("nor a missing one", background.lost_in_restart() == "")
    background._folder().mkdir(parents=True)
    background._jobs["abc"] = {"id": "abc", "command": "x", "code": None, "started": time.time(), "log": ""}
    background._running_path().write_text('[{"id": "abc", "command": "x"}]', encoding="utf-8")
    _check("a reconnect does not call a live command lost", background.lost_in_restart() == "")
    background._jobs.clear()


def test_old_logs_go() -> None:
    _reset()
    background._folder().mkdir(parents=True)
    old, new = background._folder() / "old.log", background._folder() / "new.log"
    old.write_text("x")
    new.write_text("y")
    os.utime(old, (time.time() - 30 * 86400, time.time() - 30 * 86400))
    background._prune(time.time())
    _check("a month-old log goes, today's stays", not old.exists() and new.exists())


def test_the_tool_keeps_the_shell_rules() -> None:
    from agents import react
    _reset()
    reply = asyncio.run(react._run_shell("rm -rf /", background=True))
    _check("a hardline command is refused in the background too", reply.startswith("BLOCKED") and not background._jobs, reply[:60])
    _check("a worker cannot hold the tool", "process" in react._WORKER_FORBIDDEN)
    _check("nor can a turn that read the web", "process" in react._QUARANTINED_AFTER_WEB and "shell" in react._QUARANTINED_AFTER_WEB)

    asked = []
    real_risk, real_ask = react.__dict__.get("x"), None
    import approvals

    async def ask(command, why):
        asked.append(command)
        return False, "the user said no"

    real_ask = approvals.ask
    approvals.ask = ask
    try:
        reply = asyncio.run(react._execute_tool("shell", {"command": "pkill -f something", "background": True}))
    finally:
        approvals.ask = real_ask
    _check("a risky one still asks first", asked == ["pkill -f something"] and reply.startswith("NOT RUN") and not background._jobs, reply[:80])

    called = {}

    async def run_shell(command, timeout, background=False):
        called.update(background=background)
        return "ok"

    real_run = react._run_shell
    react._run_shell = run_shell
    try:
        asyncio.run(react._execute_tool("shell", {"command": "ls", "background": True}))
    finally:
        react._run_shell = real_run
    _check("the shell tool passes background on", called == {"background": True}, str(called))


def test_a_worker_cannot_background() -> None:
    from agents import react
    import json
    seen = []

    async def create(model, **request):
        call = SimpleNamespace(id="c1", type="function",
                               function=SimpleNamespace(name="shell", arguments=json.dumps({"command": "ls", "background": True})))
        if seen:
            return SimpleNamespace(choices=[SimpleNamespace(finish_reason="stop", message=SimpleNamespace(content="Done.", tool_calls=None))], usage=None)
        seen.append(1)
        return SimpleNamespace(choices=[SimpleNamespace(finish_reason="tool_calls", message=SimpleNamespace(content=None, tool_calls=[call]))], usage=None)

    fake = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=create)))
    ran = []

    async def execute(name, inputs, allow_private=False):
        ran.append(inputs)
        return "ok"

    real_client, real_execute = react._get_client, react._execute_tool
    react._get_client, react._execute_tool = (lambda: fake), execute
    try:
        asyncio.run(react.handle("look", [], allowed_tools=["shell"], _depth=1))
    finally:
        react._get_client, react._execute_tool = real_client, real_execute
    _check("the end of a worker's command would reach the user, so it runs in the foreground",
           ran == [{"command": "ls"}], str(ran))


def test_workspace_args() -> None:
    folder = sandbox.workspace_folder()
    folder.mkdir(parents=True, exist_ok=True)
    binds = lambda args: [args[i + 1] for i, a in enumerate(args) if a == "--bind"]
    _check("shell and python_run can write there", str(folder) in binds(sandbox.command(["true"], workspace=True)))
    _check("nothing else can by default", str(folder) not in binds(sandbox.command(["true"])))
    _check("a script with the network never can", str(folder) not in binds(sandbox.command(["true"], network=True, workspace=True)))
    _check("execute_code sees no memory at all", str(folder) not in binds(sandbox.command(["true"], hide_memory=True, workspace=True)))


def test_workspace_tidy() -> None:
    folder = sandbox.workspace_folder()
    shutil.rmtree(folder, ignore_errors=True)
    folder.mkdir(parents=True)
    (folder / "sub").mkdir()
    old, new, deep = folder / "old.csv", folder / "new.csv", folder / "sub" / "deep.csv"
    for path in (old, new, deep):
        path.write_text("x")
    long_ago = time.time() - 8 * 86400
    os.utime(old, (long_ago, long_ago))
    sandbox._tidied = 0.0
    sandbox._tidy_workspace()
    _check("a week-old file goes, new ones stay", not old.exists() and new.exists() and deep.exists())
    (folder / "big1").write_bytes(b"x" * 100)
    (folder / "big2").write_bytes(b"x" * 100)
    os.utime(folder / "big1", (time.time() - 100, time.time() - 100))
    limit = sandbox._WORKSPACE_BYTES
    sandbox._WORKSPACE_BYTES, sandbox._tidied = 120, 0.0
    sandbox._tidy_workspace()
    sandbox._WORKSPACE_BYTES = limit
    _check("past the cap, oldest first", not (folder / "big1").exists() and (folder / "big2").exists())
    (folder / "again").write_text("x")
    os.utime(folder / "again", (long_ago, long_ago))
    sandbox._tidied = time.time()
    sandbox._tidy_workspace()
    _check("at most hourly", (folder / "again").exists())


def test_real_sandbox() -> None:
    if not shutil.which("bwrap") or not Path("/opt/igor").is_dir():
        print("  skip  no bubblewrap here - run on the server")
        return
    import subprocess
    real_memory = config.MEMORY_DIR
    folder = sandbox.workspace_folder()
    argv = sandbox.prepare(["bash", "-c", f"echo kept > {folder}/made.txt && cat {folder}/made.txt"], workspace=True)
    made = subprocess.run(argv, capture_output=True, text=True, timeout=30)
    _check("a command writes in the workspace", made.stdout.strip() == "kept", made.stderr[:200])
    _check("and the file is there afterwards", (folder / "made.txt").read_text().strip() == "kept")
    argv = sandbox.prepare(["bash", "-c", f"cat {folder}/made.txt; echo no >> {folder}/made.txt"])
    script = subprocess.run(argv, capture_output=True, text=True, timeout=30)
    _check("without the flag it can read but not write", (folder / "made.txt").read_text().strip() == "kept", script.stderr[:200])
    argv = sandbox.prepare(["bash", "-c", "touch /opt/igor/memory/tasks.md"], workspace=True)
    touched = subprocess.run(argv, capture_output=True, text=True, timeout=30)
    _check("the rest of memory stays read-only", touched.returncode != 0, touched.stderr[:200])
    _ = real_memory

    told = []

    async def notify(text):
        told.append(text)

    async def run():
        background._jobs.clear()
        job_id = _job_id(await background.start("echo in the box; env | grep -c GROQ; curl -s -m 3 https://example.com | wc -c", notify))
        await _settle(job_id)

    asyncio.run(run())
    _check("the real thing finishes and reports", len(told) == 1 and "in the box" in told[0], str(told))
    _check("with no keys and no network", "\n0\n0" in told[0], told[0])


if __name__ == "__main__":
    print("\nthe life of a command")
    test_lifecycle()
    test_failure_and_kill()
    test_time_limit()
    test_cannot_start()
    test_a_restart()
    test_old_logs_go()
    print("\nthe shell's rules")
    test_the_tool_keeps_the_shell_rules()
    test_a_worker_cannot_background()
    print("\nthe workspace")
    test_workspace_args()
    test_workspace_tidy()
    print("\nin the real sandbox")
    test_real_sandbox()
    print(f"\n{len(_failures)} failed" if _failures else "\nAll passed.")
    sys.exit(1 if _failures else 0)
