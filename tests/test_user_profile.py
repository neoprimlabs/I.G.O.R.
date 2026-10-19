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
    block = prompt.split("(memory/user.md) ===\n", 1)[1]
    _check("it is cut at the cap and says so",
           block.startswith("x" * config.USER_PROFILE_CAP + "\n[cut at"), block[-80:])
    (config.MEMORY_DIR / "user.md").unlink()


def test_memory_write_keeps_the_cap() -> None:
    from agents import react
    path = config.MEMORY_DIR / "user.md"
    path.write_text("# User\n" + "a" * 1400, encoding="utf-8")

    def write(content, mode):
        return asyncio.run(react._execute_tool("memory_write", {"file": "user.md", "content": content, "mode": mode}))

    reply = write("- New fact: " + "b" * 200, "append")
    _check("an append past the cap is refused", reply.startswith("Not written") and "free" in reply, reply)
    _check("and the file is untouched", len(path.read_text(encoding="utf-8")) == len("# User\n") + 1400)
    reply = write("# User\n- Short", "overwrite")
    _check("a tidy overwrite goes through", reply == "Written successfully.", reply)
    reply = write("- Another fact", "append")
    _check("an append under the cap goes through", reply == "Written successfully.", reply)
    (config.MEMORY_DIR / "tasks.md").write_text("# Tasks\n", encoding="utf-8")
    reply = asyncio.run(react._execute_tool("memory_write", {"file": "tasks.md", "content": "c" * 5000, "mode": "append"}))
    _check("other files are not capped", reply == "Written successfully.", reply)


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            print(name)
            fn()
    if _failures:
        print(f"\n{len(_failures)} FAILED: {', '.join(_failures)}")
        sys.exit(1)
    print("\nall pass")
