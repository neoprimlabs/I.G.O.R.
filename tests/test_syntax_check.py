"""Tests for the check after write_file and patch_file, Hermes' post-write lint.

Run: python tests/test_syntax_check.py   (repo root, no API calls)

Hermes parses Python as it is written, keeps the file, and reports only what the edit broke. Before
this, a syntax error surfaced at the deploy gate, after the turn that made it had ended.
"""

import asyncio
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import checkpoints
import config
from agents import react

_failures: list[str] = []


def _check(name: str, condition: bool, detail: str = "") -> None:
    if condition:
        print(f"  pass  {name}")
    else:
        print(f"  FAIL  {name}  {detail}")
        _failures.append(name)


class _Home:
    def __enter__(self):
        self.tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.real = (config.BASE_DIR, config.MEMORY_DIR)
        base = Path(self.tmp.name)
        (base / "memory").mkdir()
        (base / "agents").mkdir()
        config.BASE_DIR, config.MEMORY_DIR = base, base / "memory"
        (base / "agents" / "x.py").write_text("A = 1\nB = 2\n", encoding="utf-8")
        (base / "agents" / "broken.py").write_text("def f(:\n    pass\nC = 1\n", encoding="utf-8")
        (base / "notes.md").write_text("A = 1\n", encoding="utf-8")
        checkpoints._turn.set(None)
        return base

    def __exit__(self, *exc):
        config.BASE_DIR, config.MEMORY_DIR = self.real
        checkpoints._turn.set(None)
        self.tmp.cleanup()


def _write(path, content):
    return asyncio.run(react._write_server_file(path, content))


def _patch(path, old, new):
    return asyncio.run(react._patch_server_file(path, old, new))


def test_a_clean_edit_says_nothing_new() -> None:
    with _Home():
        _check("a good write reads as it always did",
               _write("agents/new.py", "A = 1\n") == "Written: agents/new.py (6 bytes). Restart required for changes to take effect.")
        _check("a good patch too", _patch("agents/x.py", "A = 1", "A = 2") == "Patched: agents/x.py. Restart required for changes to take effect.")


def test_an_edit_that_breaks_python() -> None:
    with _Home() as base:
        out = _patch("agents/x.py", "A = 1", "A = = 1")
        _check("a broken patch is reported, with its line", "broke its syntax" in out and "line 1" in out, out)
        _check("and is kept, as in Hermes, for the model to fix", "A = = 1" in (base / "agents" / "x.py").read_text(encoding="utf-8"))
        _check("it says the file was still saved, so the receipt is not a failure", out.startswith("Patched:"), out)
        out = _patch("agents/x.py", "A = = 1", "A = 1")
        _check("the fix reports clean", "syntax" not in out, out)

        out = _write("agents/new.py", "def f(\n")
        _check("a new file that does not parse is reported", "broke its syntax" in out, out)
        out = _write("agents/x.py", "    A = 1\n")
        _check("so is a bad indent", "broke its syntax" in out, out)
        out = _write("agents/nul.py", "A = 1\x00\n")
        _check("a null byte does not crash the check", out.startswith("Written:") and "syntax" in out, out)


def test_only_what_the_edit_broke_is_its_fault() -> None:
    with _Home():
        out = _patch("agents/broken.py", "C = 1", "C = 2")
        _check("an edit to a file already broken does not take the blame", "already broken" in out and "broke its syntax" not in out, out)
        out = _patch("agents/broken.py", "def f(:", "def f():")
        _check("fixing it reports clean", "syntax" not in out and "already broken" not in out, out)
        out = _write("agents/broken.py", "def f(:\n")
        _check("writing over a good file with a bad one is this edit's fault", "broke its syntax" in out, out)


def test_notes_are_not_python() -> None:
    with _Home():
        _check("markdown is never parsed", "syntax" not in _write("notes.md", "def f(:\n"))
        _check("nor a patched note", "syntax" not in _patch("notes.md", "def f(:", "def g(:"))


def test_the_receipt_shows_it() -> None:
    token = react._RECEIPTS.set([])
    try:
        with _Home():
            out = _patch("agents/x.py", "A = 1", "A = = 1")
            react._record_receipt("patch_file", {"path": "agents/x.py"}, out)
            receipts = react._RECEIPTS.get()
            _check("the user sees a saved file with a broken syntax under the reply",
                   receipts[0][1] is True and "broke its syntax" in receipts[0][2], str(receipts))
    finally:
        react._RECEIPTS.reset(token)


if __name__ == "__main__":
    print("clean edits")
    test_a_clean_edit_says_nothing_new()
    print("\nbroken edits")
    test_an_edit_that_breaks_python()
    print("\nblame")
    test_only_what_the_edit_broke_is_its_fault()
    print("\nnotes")
    test_notes_are_not_python()
    print("\nreceipts")
    test_the_receipt_shows_it()

    if _failures:
        print(f"\n{len(_failures)} FAILED: {', '.join(_failures)}")
        sys.exit(1)
    print("\nall pass")
