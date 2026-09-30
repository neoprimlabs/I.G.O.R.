"""One rule: private data and the open web never share a turn.

Run: python tests/test_quarantine.py   (repo root, stdlib only, no API calls)

React already switched off state-changing tools once a turn read untrusted web
content. The other direction was missing, so instead of allowing the read and
closing the exit, resume.md was sealed from React entirely. That split the rule
across two agents - Direct could discuss the resume, React could not - and the
router decides which one answers, so from outside the behaviour looked random:

    "Can you read resume.md yet?"        -> TASK  -> React -> refused
    "what's on my resume?"               -> CHAT  -> Direct -> answered

Three separate patches went into the two paths before the design was the thing that
changed. Now React may read a private file when no web content has entered the turn,
and doing so switches off search and fetch_url for the rest of it. Symmetric, one
rule, same answer whichever agent the router picks.

Writes stay forbidden either way. Nothing should be quietly rewriting a resume.
"""

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
    (root / "memory" / "resume.md").write_text(
        "Brian Barnhart\nKitchen Manager, Pizzeria Davide 2022-2024\n", encoding="utf-8")
    (root / "memory" / "tasks.md").write_text("- something open\n", encoding="utf-8")
    (root / "ARCHITECTURE.md").write_text("# Architecture\n", encoding="utf-8")
    return root


def test_which_calls_target_private_data() -> None:
    from agents import react

    _fresh()
    _check("a read of the resume counts",
           react._targets_private("read_file", {"path": "memory/resume.md"}))
    _check("by a roundabout path too",
           react._targets_private("read_file", {"path": "memory/../memory/resume.md"}))
    _check("an ordinary file does not",
           not react._targets_private("read_file", {"path": "memory/tasks.md"}))
    _check("nor a repo file",
           not react._targets_private("read_file", {"path": "agents/react.py"}))
    _check("nor a tool without a path",
           not react._targets_private("search", {"query": "remote jobs"}))
    _check("a malformed path is not a private read",
           not react._targets_private("read_file", {}))


def test_the_two_directions() -> None:
    from agents import react

    _fresh()
    private_call = ("read_file", {"path": "memory/resume.md"})
    web_call = ("search", {"query": "remote jobs"})
    shell_call = ("shell", {"command": "ls"})

    # Nothing has happened yet: everything is allowed.
    for name, args in (private_call, web_call, shell_call):
        _check(f"clean turn allows {name}",
               react._quarantine_check(name, args, web_read=False, private_read=False) is None)

    # Web first: the existing rule, plus private reads now blocked too.
    _check("after a web read, shell is refused",
           react._quarantine_check(*shell_call, web_read=True, private_read=False) is not None)
    refusal = react._quarantine_check(*private_call, web_read=True, private_read=False)
    _check("after a web read, a private file is refused", refusal is not None, str(refusal))
    _check("and the refusal explains which direction",
           refusal and "web" in refusal.lower(), str(refusal))

    # Private first: the direction that was missing.
    refusal = react._quarantine_check(*web_call, web_read=False, private_read=True)
    _check("after a private read, search is refused", refusal is not None, str(refusal))
    _check("and so is fetch_url",
           react._quarantine_check("fetch_url", {"url": "https://x"},
                                   web_read=False, private_read=True) is not None)
    _check("the refusal says why",
           refusal and "private" in refusal.lower(), str(refusal))

    # Holding private data does not stop ordinary work.
    for name, args in (("read_file", {"path": "memory/tasks.md"}),
                       ("memory_read", {"file": "tasks.md"}),
                       ("python_run", {"code": "1+1"})):
        _check(f"after a private read, {name} still works",
               react._quarantine_check(name, args, web_read=False, private_read=True) is None)


async def test_reads_are_allowed_writes_are_not() -> None:
    from agents import react

    root = _fresh()
    out = await react._read_server_file("memory/resume.md")
    _check("a private read is still refused by default",
           "private file" in out.lower() and "Pizzeria" not in out, out[:80])

    out = await react._read_server_file("memory/resume.md", allow_private=True)
    _check("and allowed when the turn has earned it", "Pizzeria Davide" in out, out[:80])

    out = await react._write_server_file("memory/resume.md", "overwritten")
    _check("writing a private file is never allowed", "private file" in out.lower(), out[:80])
    _check("and the file is untouched",
           "Pizzeria" in (root / "memory" / "resume.md").read_text(encoding="utf-8"))

    out = await react._patch_server_file("memory/resume.md", "Kitchen", "Head")
    _check("nor patching one", "private file" in out.lower(), out[:80])


async def test_execute_tool_threads_the_flag() -> None:
    from agents import react

    _fresh()
    out = await react._execute_tool("read_file", {"path": "memory/resume.md"})
    _check("_execute_tool refuses by default", "private file" in out.lower(), out[:80])
    out = await react._execute_tool("read_file", {"path": "memory/resume.md"}, allow_private=True)
    _check("_execute_tool opens it when allowed", "Pizzeria Davide" in out, out[:80])


if __name__ == "__main__":
    import asyncio

    print("what counts as private")
    test_which_calls_target_private_data()
    print("\nboth directions")
    test_the_two_directions()
    print("\nreads versus writes")
    asyncio.run(test_reads_are_allowed_writes_are_not())
    print("\nthe flag reaches the tool")
    asyncio.run(test_execute_tool_threads_the_flag())

    if _failures:
        print(f"\n{len(_failures)} FAILED: {', '.join(_failures)}")
        sys.exit(1)
    print("\nall pass")
