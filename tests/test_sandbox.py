"""sandbox.py: shell and python_run run inside bubblewrap, or not at all.

The wrapping and the refusal are checked everywhere. What the sandbox actually
stops is checked only where bubblewrap exists - the server.

Run: python tests/test_sandbox.py   (repo root, stdlib only, no API calls)
"""

import asyncio
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import config
import sandbox

_failures: list[str] = []


def _check(name: str, condition: bool, detail: str = "") -> None:
    if condition:
        print(f"  pass  {name}")
    else:
        print(f"  FAIL  {name}  {detail}")
        _failures.append(name)


def test_command_hides_what_the_tools_hide() -> None:
    tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
    real = config.BASE_DIR
    base = Path(tmp.name)
    (base / ".git").mkdir()
    (base / ".env").write_text("SECRET=1")
    (base / "memory" / "private").mkdir(parents=True)
    (base / "memory" / "drafts.md").write_text("x")
    try:
        config.BASE_DIR = base
        args = sandbox.command(["bash", "-c", "ls"])
    finally:
        config.BASE_DIR = real
    joined = " ".join(args)
    _check("the whole filesystem is read-only first", args[:4] == ["bwrap", "--ro-bind", "/", "/"], joined[:80])
    _check("no network, own process table, clean environment",
           all(f in args for f in ("--unshare-net", "--unshare-pid", "--clearenv")))
    _check(".env is covered", f"/dev/null {base / '.env'}" in joined)
    _check("an existing sealed memory file is covered", f"/dev/null {base / 'memory' / 'drafts.md'}" in joined)
    _check("a missing one is skipped, since bubblewrap cannot mount over nothing",
           "resume.md" not in joined)
    _check("memory/private is replaced by an empty tmpfs", f"--tmpfs {base / 'memory' / 'private'}" in joined)
    _check(".git stays writable for the pre-edit commit", f"--bind {base / '.git'} {base / '.git'}" in joined)
    _check("the command runs last, under prlimit, from the repo",
           args[-3:] == ["bash", "-c", "ls"] and "prlimit" in args and f"--chdir {base}" in joined)
    _check("PATH is set explicitly, so the venv python is found",
           f"PATH {base}/venv/bin:" in joined)
    tmp.cleanup()


def test_unavailable_refuses() -> None:
    real_which, real_ready = shutil.which, sandbox._ready
    try:
        sandbox._ready = False
        shutil.which = lambda name: None
        try:
            sandbox.prepare(["true"])
            raised = ""
        except sandbox.Unavailable as e:
            raised = str(e)
        _check("no bubblewrap means Unavailable, with a message for the model",
               raised.startswith("NOT RUN") and "not installed" in raised, raised)
        _check("a failure is not remembered, so the next call probes again", sandbox._ready is False)
        _check("the startup check reports why", "not installed" in sandbox.startup_check())
    finally:
        shutil.which, sandbox._ready = real_which, real_ready


def test_success_is_remembered() -> None:
    real_probe, real_ready = sandbox._probe, sandbox._ready
    calls = []
    try:
        sandbox._ready = False
        sandbox._probe = lambda: calls.append(1) or ""
        sandbox.prepare(["true"])
        sandbox.prepare(["true"])
        _check("one probe, then the result is reused", len(calls) == 1, str(calls))
    finally:
        sandbox._probe, sandbox._ready = real_probe, real_ready


def test_tools_never_run_bare() -> None:
    """With the sandbox down, shell and python_run return the refusal and start nothing."""
    from agents import react
    real_prepare, real_run = sandbox.prepare, subprocess.run
    started = []

    def down(argv):
        raise sandbox.Unavailable("NOT RUN: test")

    try:
        sandbox.prepare = down
        subprocess.run = lambda *a, **kw: started.append(a)
        shell = asyncio.run(react._run_shell("ls"))
        code = asyncio.run(react._run_code("print(1)"))
    finally:
        sandbox.prepare, subprocess.run = real_prepare, real_run
    _check("shell returns the refusal", shell == "NOT RUN: test", shell)
    _check("python_run returns the refusal", code == "NOT RUN: test", code)
    _check("and no process was started", started == [], str(started))


def test_live_on_the_server() -> None:
    """Only where bubblewrap exists. What a command inside can and cannot do."""
    if not shutil.which("bwrap") or not Path("/opt/igor").is_dir():
        print("  skip  no bubblewrap here - run on the server")
        return
    real = config.BASE_DIR
    config.BASE_DIR = Path("/opt/igor")
    try:
        def sh(cmd):
            r = subprocess.run(sandbox.prepare(["bash", "-c", cmd]), capture_output=True,
                               text=True, timeout=20)
            return (r.stdout + r.stderr).strip()

        env = sh("env | cut -d= -f1")
        _check("no API keys or tokens in the environment",
               not any(k in env for k in ("KEY", "TOKEN", "SECRET")), env)
        _check("the secrets file cannot be read", "SECRET" not in sh("cat /opt/igor/.env; echo done"))
        _check("no network", "unreachable" in sh(
            "python3 -c \"import socket; socket.create_connection(('1.1.1.1', 53), 3)\" 2>&1").lower())
        _check("the repo is read-only", "Read-only" in sh("touch /opt/igor/sandbox-test 2>&1"))
        _check("/tmp is writable", sh("echo ok > /tmp/t && cat /tmp/t") == "ok")
        _check("only its own processes are visible", int(sh("ps -e --no-headers | wc -l")) < 10)
    finally:
        config.BASE_DIR = real


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            print(name)
            fn()
    if _failures:
        print(f"\n{len(_failures)} FAILED: {', '.join(_failures)}")
        sys.exit(1)
    print("\nall pass")
