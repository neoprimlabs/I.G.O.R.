"""The reply's footer says what the turn really changed, from the tools' own results.

Hermes' file-mutation verifier warns when an edit failed "despite any wording above";
its CLI turn summary lists what landed. Live 2026-10-22 and 10-23, IGOR said "Got it"
for saves that never happened. Run: python tests/test_receipts.py
"""
import asyncio
import json
import sys
import tempfile
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import config

_failures: list[str] = []


def _check(name: str, condition: bool, detail: str = "") -> None:
    print(f"  {'pass' if condition else 'FAIL'}  {name}" + ("" if condition else f"  {detail}"))
    if not condition:
        _failures.append(name)


def test_footer() -> None:
    from agents import react
    saved = [("tasks.md", True, "Added to tasks.md: Try the MCP server (2026-10-26)")]
    _check("a save gets one subtext line", react._receipt_footer(saved) ==
           "-# Added to tasks.md: Try the MCP server (2026-10-26)", react._receipt_footer(saved))
    failed = [("user.md", False, "Not written: user.md would be 1520 characters, over its 1500 cap.")]
    footer = react._receipt_footer(failed)
    _check("a failed save is flagged whatever the reply says",
           "Not saved, whatever the reply above says" in footer and "user.md: Not written" in footer, footer)
    redone = failed + [("user.md", True, "Patched user.md: - Works Thursdays")]
    footer = react._receipt_footer(redone)
    _check("a failure redone in the same turn is not flagged",
           "Not saved" not in footer and "Patched user.md" in footer, footer)
    _check("no changes, no footer", react._receipt_footer([]) == "")
    many = [("tasks.md", True, f"Added to tasks.md: task {i}") for i in range(6)]
    _check("a busy turn is summarised, not listed", "...and 2 more changes" in react._receipt_footer(many))


def test_recording() -> None:
    from agents import react
    token = react._RECEIPTS.set([])
    try:
        react._record_receipt("memory_read", {"file": "tasks.md"}, "# Tasks")
        react._record_receipt("task_update", {"action": "list"}, "Open tasks:\n- a")
        react._record_receipt("memory_write", {"file": "tasks.md"}, "[you already made this exact call]")
        _check("reads, lists and repeated calls are not changes", react._RECEIPTS.get() == [],
               str(react._RECEIPTS.get()))
        react._record_receipt("write_file", {"path": "agents/x.py"},
                              "Written: agents/x.py (12 bytes). Restart required for changes to take effect.")
        react._record_receipt("memory_write", {"file": "tasks.md"}, "[unavailable: this turn has read untrusted content]")
        got = react._RECEIPTS.get()
        _check("a write is recorded without its advice",
               got[0] == ("agents/x.py", True, "Written: agents/x.py (12 bytes)"), str(got))
        _check("a refusal is recorded as a failure", got[1][1] is False, str(got))
    finally:
        react._RECEIPTS.reset(token)


def test_a_turn_reports_what_it_saved() -> None:
    from agents import react
    root = Path(tempfile.mkdtemp())
    (root / "memory").mkdir()
    (root / "memory" / "tasks.md").write_text("# Tasks\n", encoding="utf-8")
    real = (config.BASE_DIR, config.MEMORY_DIR, react._get_client)
    calls = []

    def script(*steps):
        async def create(model, **kwargs):
            calls.append(kwargs)
            step = steps[min(len(calls) - 1, len(steps) - 1)]
            if isinstance(step, tuple):
                call = SimpleNamespace(id=f"c{len(calls)}", type="function",
                                       function=SimpleNamespace(name=step[0], arguments=json.dumps(step[1])))
                return SimpleNamespace(choices=[SimpleNamespace(finish_reason="tool_calls", message=SimpleNamespace(
                    content=None, tool_calls=[call]))], usage=None)
            return SimpleNamespace(choices=[SimpleNamespace(finish_reason="stop", message=SimpleNamespace(
                content=step, tool_calls=None))], usage=None)
        return SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=create)))

    try:
        config.BASE_DIR, config.MEMORY_DIR = root, root / "memory"
        react._get_client = lambda: script(
            ("memory_write", {"file": "tasks.md", "action": "add", "content": "Try the MCP server"}),
            "Saved it.")
        reply = asyncio.run(react.handle("Remember that I want to try the MCP server", []))
        _check("the reply carries what was really saved",
               reply.startswith("Saved it.") and "-# Added to tasks.md: Try the MCP server" in reply, reply)

        calls.clear()
        react._get_client = lambda: script(
            ("memory_write", {"file": "tasks.md", "action": "overwrite", "content": "x"}),
            "Done, your list is replaced.")
        reply = asyncio.run(react.handle("Replace my task list", []))
        _check("and a claim the tools contradict is flagged under it",
               "Not saved, whatever the reply above says" in reply, reply)

        calls.clear()
        react._get_client = lambda: script("Nothing to change.")
        _check("a turn that changed nothing has no footer",
               asyncio.run(react.handle("hello", [])) == "Nothing to change.")
    finally:
        config.BASE_DIR, config.MEMORY_DIR, react._get_client = real


if __name__ == "__main__":
    print("footer")
    test_footer()
    print("\nrecording")
    test_recording()
    print("\na turn")
    test_a_turn_reports_what_it_saved()
    if _failures:
        print(f"\n{len(_failures)} FAILED: {', '.join(_failures)}")
        sys.exit(1)
    print("\nall pass")
