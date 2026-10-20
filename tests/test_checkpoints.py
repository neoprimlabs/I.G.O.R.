"""checkpoints.py: a before-image of each file IGOR's tools change, and rollback.

Run: python tests/test_checkpoints.py   (repo root, stdlib only, no API calls)
"""

import asyncio
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import config
import checkpoints

_failures: list[str] = []


def _check(name: str, condition: bool, detail: str = "") -> None:
    if condition:
        print(f"  pass  {name}")
    else:
        print(f"  FAIL  {name}  {detail}")
        _failures.append(name)


class _Home:
    """A throwaway IGOR root with memory/ inside it."""

    def __enter__(self):
        self.tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.real = (config.BASE_DIR, config.MEMORY_DIR)
        base = Path(self.tmp.name)
        (base / "memory").mkdir()
        (base / "agents").mkdir()
        config.BASE_DIR, config.MEMORY_DIR = base, base / "memory"
        (base / "agents" / "x.py").write_text("A = 1\n", encoding="utf-8")
        (base / "memory" / "tasks.md").write_text("- old task\n", encoding="utf-8")
        checkpoints._turn.set(None)
        return base

    def __exit__(self, *exc):
        config.BASE_DIR, config.MEMORY_DIR = self.real
        checkpoints._turn.set(None)
        self.tmp.cleanup()


def _text(p: Path) -> str:
    return p.read_text(encoding="utf-8") if p.exists() else "<absent>"


def _turn_a(base: Path) -> None:
    from agents import prod_memory, react
    checkpoints.start_turn("tidy my tasks and fix x")
    asyncio.run(react._write_server_file("notes.md", "# new\n"))
    asyncio.run(react._patch_server_file("agents/x.py", "A = 1", "A = 2"))
    asyncio.run(react._patch_server_file("agents/x.py", "A = 2", "A = 3"))
    prod_memory._write_to_memory("tasks.md", "- new task", "append")


def test_one_checkpoint_per_turn() -> None:
    with _Home() as base:
        checkpoints.start_turn("just reading")
        _check("a turn that changes nothing leaves nothing behind", checkpoints._folders() == [])
        _turn_a(base)
        folders = checkpoints._folders()
        _check("three writes to three files in one turn make one checkpoint", len(folders) == 1,
               str(folders))
        files = checkpoints._load(folders[0])["files"]
        _check("each file once", sorted(f["path"] for f in files)
               == ["agents/x.py", "memory/tasks.md", "notes.md"], str(files))
        x = next(f for f in files if f["path"] == "agents/x.py")
        _check("the first before-image in a turn wins",
               (folders[0] / x["blob"]).read_text(encoding="utf-8") == "A = 1\n")
        listing = checkpoints.list_text()
        _check("the list names the turn and its files",
               '1.' in listing and "tidy my tasks" in listing and "agents/x.py" in listing, listing)


def test_rollback_and_undo() -> None:
    from agents import prod_memory
    with _Home() as base:
        _turn_a(base)
        checkpoints.start_turn("rewrite the list")
        prod_memory._write_to_memory("tasks.md", "- only this", "overwrite")
        diff = checkpoints.diff_text(1)
        _check("diff shows what a rollback would change",
               "- only this" in diff and "+- new task" in diff, diff)
        reply = checkpoints.restore(2)
        _check("rollback 2 restores every file to before turn A",
               _text(base / "agents" / "x.py") == "A = 1\n"
               and _text(base / "memory" / "tasks.md") == "- old task\n", reply)
        _check("and deletes the file turn A created", not (base / "notes.md").exists(), reply)
        _check("the reply says what happened and how to undo it",
               "Restored:" in reply and "Removed" in reply and "rollback 1" in reply
               and "next restart" in reply, reply)
        _check("the rollback is itself checkpoint 1", "before rollback 2" in checkpoints.list_text())
        checkpoints.restore(1)
        _check("rollback 1 undoes the rollback",
               _text(base / "agents" / "x.py") == "A = 3\n" and (base / "notes.md").exists()
               and "- only this" in _text(base / "memory" / "tasks.md"))


def test_hand_edits_are_kept() -> None:
    with _Home() as base:
        _turn_a(base)
        (base / "agents" / "x.py").write_text("A = 99  # edited by hand\n", encoding="utf-8")
        reply = checkpoints.restore(1)
        _check("a file changed after IGOR wrote it is kept",
               "99" in _text(base / "agents" / "x.py") and "Kept" in reply, reply)
        _check("and the 'all' it suggests counts this rollback's own checkpoint",
               "rollback 2 all" in reply, reply)
        _check("the others are still restored", _text(base / "memory" / "tasks.md") == "- old task\n")
        checkpoints.restore(2, overwrite=True)
        _check("'all' overwrites it", _text(base / "agents" / "x.py") == "A = 1\n")


def test_one_file() -> None:
    with _Home() as base:
        _turn_a(base)
        reply = checkpoints.run(checkpoints.parse("rollback 1 agents/x.py"))
        _check("only the named file is restored",
               _text(base / "agents" / "x.py") == "A = 1\n" and (base / "notes.md").exists(), reply)
        _check("a file the checkpoints never changed is refused, not guessed",
               "did not change" in checkpoints.restore(2, "memory/user.md"))
        _check("an unknown number is refused", "no checkpoint 9" in checkpoints.restore(9))


def test_only_twenty_are_kept() -> None:
    with _Home() as base:
        for i in range(25):
            checkpoints.start_turn(f"turn {i}")
            checkpoints.before_write(base / "agents" / "x.py")
        _check("the newest 20 are kept", len(checkpoints._folders()) == 20, str(len(checkpoints._folders())))
        _check("and they are the newest", '"turn 24"' in checkpoints.list_text().split("\n")[1])


def test_a_broken_store_never_blocks_a_write() -> None:
    from agents import react
    with _Home() as base:
        (base / "memory" / ".checkpoints").write_text("not a folder", encoding="utf-8")
        checkpoints.start_turn("write anyway")
        reply = asyncio.run(react._write_server_file("agents/x.py", "A = 7\n"))
        _check("the write still happens when no checkpoint can be saved",
               reply.startswith("Written") and _text(base / "agents" / "x.py") == "A = 7\n", reply)
        _check("and rollback says there is nothing to restore", "No checkpoints" in checkpoints.list_text())


def test_parse() -> None:
    cases = {
        "rollback": ("list",), "/rollback": ("list",), "Rollback list": ("list",),
        "rollback diff 2": ("diff", 2), "rollback 3": ("restore", 3, None, False),
        "rollback 3 all": ("restore", 3, None, True),
        "rollback 2 ./memory/Tasks.md": ("restore", 2, "memory/Tasks.md", False),
    }
    for text, want in cases.items():
        _check(f"parses {text!r}", checkpoints.parse(text) == want, str(checkpoints.parse(text)))
    for text in ("rollback the last change please", "can you rollback 2", "rollback 2 3 4", "roll back"):
        _check(f"leaves {text!r} to the router", checkpoints.parse(text) is None)


def test_routed_in_code() -> None:
    import orchestrator
    with _Home():
        async def notify(_):
            pass

        o = orchestrator.Orchestrator(notify)
        asked = []

        async def no_model(*a, **k):
            asked.append(1)
            raise RuntimeError("a model was called")

        o._client.chat.completions.create = no_model
        destination = asyncio.run(o._classify("rollback"))
        _check("'rollback' skips the router", destination == "Rollback" and not asked,
               f"{destination} {asked}")
        reply = asyncio.run(o._route("Rollback", "rollback"))
        _check("and answers from code", "No checkpoints" in reply, reply)


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            print(name)
            fn()
    if _failures:
        print(f"\n{len(_failures)} FAILED: {', '.join(_failures)}")
        sys.exit(1)
    print("\nall pass")
