"""The user's profile in every chat prompt, capped the way Hermes caps USER.md.

Run: python tests/test_user_profile.py   (repo root, no network)

Hermes puts a small USER.md (1375 characters) into every session and refuses a
memory write that would pass the cap, telling the agent how much to free. IGOR's
Direct, which takes most messages, could see none of memory/user.md before
2026-10-19.
"""

import asyncio
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import config

config.MEMORY_DIR = Path(tempfile.mkdtemp())

_failures: list[str] = []


def _check(name: str, condition: bool, detail: str = "") -> None:
    print(f"  {'pass' if condition else 'FAIL'}  {name}" + ("" if condition else f"  {detail}"))
    if not condition:
        _failures.append(name)


def _direct_prompt(message: str = "how should I spend tonight?") -> str:
    from agents import direct
    seen = {}

    async def call(system, messages, max_tokens, model):
        seen["system"] = system
        return "ok"

    asyncio.run(direct.handle(message, [], call))
    return seen["system"]


def test_profile_reaches_direct() -> None:
    (config.MEMORY_DIR / "user.md").write_text("# User\n- Location: Pittsburgh, PA\n- Learning n8n\n", encoding="utf-8")
    prompt = _direct_prompt()
    _check("the profile is in Direct's prompt", "Location: Pittsburgh, PA" in prompt and "Learning n8n" in prompt, prompt[-300:])
    _check("after the instructions, under a plain heading", prompt.index("Notes about the user") > prompt.index("Style:"))
    (config.MEMORY_DIR / "user.md").unlink()
    _check("no file, no block", "Notes about the user" not in _direct_prompt())


def test_an_overlong_profile_is_cut() -> None:
    (config.MEMORY_DIR / "user.md").write_text("x" * (config.USER_PROFILE_CAP + 400), encoding="utf-8")
    prompt = _direct_prompt()
    header, block = prompt.split("=== Notes about the user", 1)[1].split(" ===\n", 1)
    _check("it is cut at the cap and says so",
           block.startswith("x" * config.USER_PROFILE_CAP + "\n[cut at"), block[-80:])
    _check("the header says how full the profile is, as Hermes' does",
           f"{config.USER_PROFILE_CAP + 400:,}/{config.USER_PROFILE_CAP:,} characters" in header, header)
    (config.MEMORY_DIR / "user.md").unlink()


def test_memory_write_keeps_the_cap() -> None:
    from agents import react
    path = config.MEMORY_DIR / "user.md"
    fixture = "# User\n- Old note " + "a" * 1382
    path.write_text(fixture, encoding="utf-8")

    def write(**inputs):
        return asyncio.run(react._execute_tool("memory_write", {"file": "user.md", **inputs}))

    reply = write(action="add", content="New fact: " + "b" * 200)
    _check("an add past the cap is refused, saying how much to free",
           reply.startswith("Not written") and "Free at least" in reply, reply)
    _check("and lists the current entries, as Hermes does", "Current entries:" in reply, reply)
    _check("and the file is untouched", path.read_text(encoding="utf-8") == fixture)
    reply = write(operations=[{"action": "remove", "old_text": "Old note"},
                              {"action": "add", "content": "New fact: " + "b" * 200}])
    _check("the tool takes the room-making and the save in one call",
           "Removed from user.md" in reply and "Added to user.md: New fact" in reply, reply)
    path.write_text(fixture, encoding="utf-8")
    reply = write(action="remove", old_text="Old note")
    _check("removing an entry frees the room", reply.startswith("Removed from user.md"), reply)
    reply = write(action="add", content="Another fact")
    _check("an add under the cap goes through, dated", reply.startswith("Added to user.md: Another fact ("), reply)
    reply = write(content="# User\n- Short", mode="overwrite")
    _check("a whole-file overwrite is refused, even in the old schema",
           reply.startswith("Not written: whole-file overwrite is gone"), reply)
    (config.MEMORY_DIR / "tasks.md").write_text("# Tasks\n", encoding="utf-8")
    reply = asyncio.run(react._execute_tool("memory_write", {"file": "tasks.md", "content": "c" * 5000, "mode": "append"}))
    _check("other files are not capped, and the old append still adds", reply.startswith("Added to tasks.md"), reply)


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            print(name)
            fn()
    if _failures:
        print(f"\n{len(_failures)} FAILED: {', '.join(_failures)}")
        sys.exit(1)
    print("\nall pass")
