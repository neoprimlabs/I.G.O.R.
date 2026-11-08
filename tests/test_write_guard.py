"""write_file replaces only what the turn has read in full and is unchanged since (Hermes' stale-overwrite guard).

Run: python tests/test_write_guard.py   (repo root, no API calls)

Hermes refuses a whole-file overwrite of content the task never saw or that changed since it read it,
before anything is written. IGOR's write_file replaced any file it could reach, read or not.
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
    print(f"  {'pass' if condition else 'FAIL'}  {name}" + ("" if condition else f"  {detail}"))
    if not condition:
        _failures.append(name)


class _Turn:
    """A temporary IGOR root, inside a turn's read tracking."""

    def __enter__(self):
        self.tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.real = (config.BASE_DIR, config.MEMORY_DIR)
        base = Path(self.tmp.name)
        (base / "memory").mkdir()
        (base / "agents").mkdir()
        config.BASE_DIR, config.MEMORY_DIR = base, base / "memory"
        (base / "agents" / "kettle.py").write_text("BOIL = 100\n", encoding="utf-8")
        (base / "agents" / "long.py").write_text("".join(f"STEP_{i} = {i}\n" for i in range(800)), encoding="utf-8")
        checkpoints._turn.set(None)
        self.token = react._READS.set({})
        return base

    def __exit__(self, *exc):
        react._READS.reset(self.token)
        config.BASE_DIR, config.MEMORY_DIR = self.real
        checkpoints._turn.set(None)
        self.tmp.cleanup()


def _run(coro):
    return asyncio.run(coro)


def _write(path, content):
    return _run(react._write_server_file(path, content))


def _read(path, offset=0):
    return _run(react._read_server_file(path, offset))


def test_an_unread_file_is_not_replaced() -> None:
    with _Turn() as base:
        out = _write("agents/kettle.py", "BOIL = 90\n")
        _check("an existing file never read is refused", out.startswith("[refused:") and "patch_file" in out, out)
        _check("and left as it was", (base / "agents" / "kettle.py").read_text(encoding="utf-8") == "BOIL = 100\n")
        _check("a new file needs no read", _write("agents/new.py", "A = 1\n").startswith("Written:"))
        _check("and can be written again by the turn that wrote it",
               _write("agents/new.py", "A = 2\n").startswith("Written:"))


def test_a_file_read_in_full_can_be_replaced() -> None:
    with _Turn() as base:
        _read("agents/kettle.py")
        _check("read whole, it can be replaced", _write("agents/kettle.py", "BOIL = 90\n").startswith("Written:"))
        _check("with the new content", (base / "agents" / "kettle.py").read_text(encoding="utf-8") == "BOIL = 90\n")


def test_a_partly_read_file_is_not_replaced() -> None:
    with _Turn():
        first = _read("agents/long.py")
        _check("the long file pages", "offset=" in first, first[-200:])
        _check("one page is not enough", _write("agents/long.py", "X = 1\n").startswith("[refused:"))
        offset = 0
        while True:
            page = _read("agents/long.py", offset)
            if "offset=" not in page:
                break
            offset = int(page.rsplit("offset=", 1)[1].split()[0])
        _check("every page in order is", _write("agents/long.py", "X = 1\n").startswith("Written:"))
    with _Turn():
        _read("agents/long.py", 3700)
        _check("a later page alone is not a full read", _write("agents/long.py", "X = 1\n").startswith("[refused:"))


def test_a_file_changed_since_it_was_read_is_not_replaced() -> None:
    with _Turn() as base:
        _read("agents/kettle.py")
        (base / "agents" / "kettle.py").write_text("BOIL = 100\nSTEAM = True\n", encoding="utf-8")
        out = _write("agents/kettle.py", "BOIL = 90\n")
        _check("changed underneath, it is refused", out.startswith("[refused:") and "changed since" in out, out)
        _check("and the other change survives", "STEAM" in (base / "agents" / "kettle.py").read_text(encoding="utf-8"))
        _read("agents/kettle.py")
        _check("read again, it can be replaced", _write("agents/kettle.py", "BOIL = 90\n").startswith("Written:"))


def test_patches() -> None:
    with _Turn():
        _check("a patch never needs a read", _run(react._patch_server_file("agents/kettle.py", "100", "95")).startswith("Patched:"))
        _check("but a blind patch gives nothing to replace from",
               _write("agents/kettle.py", "BOIL = 90\n").startswith("[refused:"))
    with _Turn():
        _read("agents/kettle.py")
        _run(react._patch_server_file("agents/kettle.py", "100", "95"))
        _check("a patch to a file read whole keeps it whole", _write("agents/kettle.py", "BOIL = 90\n").startswith("Written:"))


def test_outside_a_turn_nothing_is_guarded() -> None:
    tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
    real = (config.BASE_DIR, config.MEMORY_DIR)
    try:
        base = Path(tmp.name)
        (base / "memory").mkdir()
        (base / "agents").mkdir()
        (base / "agents" / "kettle.py").write_text("BOIL = 100\n", encoding="utf-8")
        config.BASE_DIR, config.MEMORY_DIR = base, base / "memory"
        checkpoints._turn.set(None)
        _check("no turn, no tracking: the write goes through", _write("agents/kettle.py", "BOIL = 90\n").startswith("Written:"))
    finally:
        config.BASE_DIR, config.MEMORY_DIR = real
        tmp.cleanup()


def test_the_model_is_told_before_it_tries() -> None:
    rule = react._DEFERRED_TOOLS["write_file"][1]
    _check("loading write_file says to read the whole file first", "read in full first" in rule and "patch_file" in rule)


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            print(name)
            fn()
    if _failures:
        print(f"\n{len(_failures)} FAILED: {', '.join(_failures)}")
        sys.exit(1)
    print("\nall pass")
