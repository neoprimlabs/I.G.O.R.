"""Asking the user before a risky shell command runs (approvals.py).

Run: python tests/test_approvals.py   (repo root, no network)

Hermes' middle tier between "runs" and "always refused": risky but sometimes
legitimate commands wait for the user's yes, and no answer in five minutes is a no.
"""

import asyncio
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import config

config.MEMORY_DIR = Path(tempfile.mkdtemp())

import approvals

_failures: list[str] = []


def _check(name: str, condition: bool, detail: str = "") -> None:
    print(f"  {'pass' if condition else 'FAIL'}  {name}" + ("" if condition else f"  {detail}"))
    if not condition:
        _failures.append(name)


def test_what_needs_approval() -> None:
    risky = ["rm -rf build/", "rm -r old_logs", "find . -name '*.tmp' -delete", "ls | xargs rm",
             "chmod 777 script.sh", "chmod o+w notes.md", "kill 4121", "pkill -f python",
             "systemctl restart igor", "echo KEY=x > .env", "cat key.pub | tee -a ~/.ssh/authorized_keys",
             "sed -i 's/a/b/' /etc/hosts", "sqlite3 memory/context.db 'DROP TABLE messages'",
             "sqlite3 memory/history.db 'DELETE FROM observations;'", "bash -c 'ls'",
             "python3 -c 'import os'", "git reset --hard HEAD~1", "git -C /opt/igor clean -fd",
             "git checkout -- agents/react.py"]
    safe = ["ls -la", "git status", "git log -3 --oneline", "rm notes.txt", "cat .env.example",
            "grep -r TODO agents", "python3 scripts/check.py", "journalctl -u igor -n 50", "df -h",
            "sqlite3 memory/context.db 'DELETE FROM messages WHERE id = 3'", "echo hi > notes.txt",
            "git diff", "pip list"]
    for command in risky:
        _check(f"asks first: {command}", approvals.risk(command) is not None)
    for command in safe:
        _check(f"runs freely: {command}", approvals.risk(command) is None, str(approvals.risk(command)))


async def _flow() -> None:
    sent = []

    async def send(text):
        sent.append(text)
        return True

    approvals.set_sender(send)
    task = asyncio.create_task(approvals.ask("rm -rf build/", "it deletes recursively"))
    await asyncio.sleep(0)
    _check("the request reaches the user with the command and the reason",
           len(sent) == 1 and "rm -rf build/" in sent[0] and "deletes recursively" in sent[0], str(sent))
    _check("an ordinary message is not taken as an answer", approvals.answer("what is it doing?") is None)
    _check("a yes settles it", approvals.answer("Yes") == "Approved. Running it now.")
    _check("and the waiting turn gets approval", await task == (True, ""))
    _check("with nothing waiting, yes is an ordinary message", approvals.answer("yes") is None)

    task = asyncio.create_task(approvals.ask("kill 4121", "it stops or kills processes"))
    await asyncio.sleep(0)
    second = await approvals.ask("pkill python", "it stops or kills processes")
    _check("a second request while one waits is refused", second[0] is False and "already waiting" in second[1])
    approvals.answer("no")
    approved, reason = await task
    _check("a no is a refusal with the reason", approved is False and "said no" in reason, reason)

    approvals.TIMEOUT_S = 0.05
    approved, reason = await approvals.ask("chmod 777 x", "it opens up file permissions")
    _check("silence is a no", approved is False and "did not answer" in reason, reason)
    approvals.TIMEOUT_S = 300

    approvals.set_sender(None)
    approved, reason = await approvals.ask("kill 1", "it stops or kills processes")
    _check("with no way to reach the user, nothing is approved", approved is False and "cannot reach" in reason)


def test_ask_and_answer() -> None:
    asyncio.run(_flow())


async def _shell_flow() -> None:
    from agents import react
    ran = []

    async def fake_run(command, timeout=10):
        ran.append(command)
        return "done"

    real = react._run_shell
    react._run_shell = fake_run
    try:
        async def send_and_refuse(text):
            asyncio.get_running_loop().call_soon(approvals.answer, "no")
            return True

        approvals.set_sender(send_and_refuse)
        out = await react._execute_tool("shell", {"command": "rm -rf old_logs"})
        _check("a refused risky command does not run", ran == [] and out.startswith("NOT RUN"), out)
        _check("and React is told not to retry", "Do not retry" in out)

        async def send_and_approve(text):
            asyncio.get_running_loop().call_soon(approvals.answer, "yes")
            return True

        approvals.set_sender(send_and_approve)
        out = await react._execute_tool("shell", {"command": "rm -rf old_logs"})
        _check("an approved one runs", ran == ["rm -rf old_logs"] and out == "done", f"{ran} {out}")

        asked = []

        async def record(text):
            asked.append(text)
            return True

        approvals.set_sender(record)
        await react._execute_tool("shell", {"command": "git status"})
        _check("a safe command runs without asking", asked == [] and ran[-1] == "git status")
    finally:
        react._run_shell = real
    out = await react._execute_tool("shell", {"command": "rm -rf /"})
    _check("a hardline command is refused without asking", out.startswith("BLOCKED") and asked == [], out[:80])


def test_shell_tool_asks_first() -> None:
    asyncio.run(_shell_flow())


async def _orchestrator_flow() -> None:
    import orchestrator

    async def notify(_):
        pass

    o = orchestrator.Orchestrator(notify)
    routed = []

    async def route(*a, **k):
        routed.append(a)
        return "routed"

    o._route = route
    approvals.set_sender(lambda text: asyncio.sleep(0, result=True))
    task = asyncio.create_task(approvals.ask("kill 99", "it stops or kills processes"))
    await asyncio.sleep(0)
    reply = await o.process(config.AUTHORIZED_USER_ID, "yes")
    _check("the user's yes goes to the waiting command, not a new turn",
           reply == ("Approved. Running it now.", False) and routed == [], f"{reply} {routed}")
    _check("and the command is approved", (await task)[0] is True)
    approvals.set_sender(None)


def test_orchestrator_hands_over_the_answer() -> None:
    asyncio.run(_orchestrator_flow())


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            print(name)
            fn()
    if _failures:
        print(f"\n{len(_failures)} FAILED: {', '.join(_failures)}")
        sys.exit(1)
    print("\nall pass")
