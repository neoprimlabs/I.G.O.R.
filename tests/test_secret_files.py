"""No tool can read or change a file that holds credentials.

Run: python tests/test_secret_files.py   (repo root, stdlib only, no API calls)

Found 2026-10-30: read_file('.env') returned every API key. The sandbox hid the file from the
shell and python_run, but read_file had only the private-memory list, and it stays open after a
web read, as fetch_url does: a page could have asked for the keys and for a URL carrying them.
The journal showed no such read. Hermes denies these paths outright, with no approval prompt.
"""

import asyncio
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import config

_failures: list[str] = []


def _check(name: str, condition: bool, detail: str = "") -> None:
    print(f"  {'pass' if condition else 'FAIL'}  {name}" + ("" if condition else f"  {detail}"))
    if not condition:
        _failures.append(name)


SECRETS = (".env", ".env.local", "memory/../.env", "./.env", "deploy.key", "certs/server.pem", ".ssh/id_rsa", "id_ed25519",
           ".git/config", "CLAUDE.local.md", "scripts/backup_memory.local.ps1", "credentials.json", "webhook.txt", "memory/.env")
ORDINARY = ("agents/react.py", "README.md", "ARCHITECTURE.md", "memory/tasks.md", ".gitignore", "tests/test_redact.py",
            "docs/environment.md", "keyboard.py", "agents/tokens.py")


def _home() -> Path:
    root = Path(tempfile.mkdtemp())
    (root / "memory").mkdir()
    (root / ".env").write_text("GROQ_API_KEY=not-a-real-key-for-testing\n", encoding="utf-8")
    (root / "agents").mkdir()
    (root / "agents" / "real.py").write_text("GROQ_API_KEY = 'in the source'\n", encoding="utf-8")
    return root


def test_reading() -> None:
    from agents import react
    root, real = _home(), (config.BASE_DIR, config.MEMORY_DIR)
    try:
        config.BASE_DIR, config.MEMORY_DIR = root, root / "memory"
        for path in SECRETS:
            problem = react._path_problem(path, allow_private=True)
            _check(f"{path} is refused, even with private files open", bool(problem) and "credentials" in problem, str(problem))
        for path in ORDINARY:
            _check(f"{path} is an ordinary path", react._path_problem(path, allow_private=True) is None,
                   str(react._path_problem(path, allow_private=True)))
        out = asyncio.run(react._read_server_file(".env", allow_private=True))
        _check("read_file returns none of it", "not-a-real-key" not in out and "credentials" in out, out)
        _check("it says the file exists, so nobody is asked to paste a key", "exists" in out, out)
    finally:
        config.BASE_DIR, config.MEMORY_DIR = real


def test_searching_and_writing() -> None:
    from agents import react
    root, real = _home(), (config.BASE_DIR, config.MEMORY_DIR)
    try:
        config.BASE_DIR, config.MEMORY_DIR = root, root / "memory"
        found = asyncio.run(react._search_source("GROQ_API_KEY"))
        _check("source search skips it", "not-a-real-key" not in found and "real.py" in found, found)
        for call in (react._write_server_file(".env", "GROQ_API_KEY=mine"),
                     react._patch_server_file(".env", "not-a-real-key", "mine")):
            out = asyncio.run(call)
            _check("no tool can change it", "mine" not in (root / ".env").read_text(encoding="utf-8"), out)
        _check("what is there is untouched", "not-a-real-key" in (root / ".env").read_text(encoding="utf-8"))
        _check("a file of that name cannot be created elsewhere", react._unwritable((root / "agents" / "server.pem").resolve()))
        _check("an ordinary file can be", not react._unwritable((root / "agents" / "new.py").resolve()))
        _check("reading one counts as a private read, so the web closes with it",
               react._targets_private("read_file", {"path": ".env"}))
    finally:
        config.BASE_DIR, config.MEMORY_DIR = real


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            print(name)
            fn()
    if _failures:
        print(f"\n{len(_failures)} FAILED: {', '.join(_failures)}")
        sys.exit(1)
    print("\nall pass")
