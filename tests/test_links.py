"""A link reaches the user only if IGOR read it (links.py).

2026-11-05: asked for entry-level remote jobs, React listed five after a real job-board search. Three
links were wrong: two RemoteOK numbers that exist nowhere, and a real RemoteOK posting given a made-up
We Work Remotely address. These are that case, with invented companies.

Run: python tests/test_links.py   (repo root, stdlib only, no API calls)
"""

import asyncio
import json
import sys
import tempfile
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import config
import context_store
import links

_failures: list[str] = []

REAL = "https://remoteOK.com/remote-jobs/remote-qa-tester-entry-level-kettle-works-1136701"
MADE_UP = "https://remoteok.com/remote-jobs/remote-data-analyst-entry-level-1123456"
WRONG_SITE = "https://weworkremotely.com/remote-jobs/qa-tester-entry-level"


def _check(name: str, condition: bool, detail: str = "") -> None:
    if condition:
        print(f"  pass  {name}")
    else:
        print(f"  FAIL  {name}  {detail}")
        _failures.append(name)


def test_the_check() -> None:
    seen = links.found(f"Results:\n- QA Tester, Kettle Works: {REAL}\n")
    reply = (f"QA Tester - Kettle Works\n{REAL.lower()}\n\nData Analyst - RemoteOK\n{MADE_UP}\n\n"
             f"QA Tester - We Work Remotely\n{WRONG_SITE}.")
    out = links.guard(reply, seen)
    _check("a link it read stays, whatever its case", REAL.lower() in out, out)
    _check("a made-up number is taken out", MADE_UP not in out, out)
    _check("and so is a real posting on the wrong site", WRONG_SITE not in out, out)
    _check("the sentence's own full stop survives", out.splitlines()[out.splitlines().index("QA Tester - We Work Remotely") + 1] == ".", out)
    _check("the user is told how many, and what that means", out.endswith(
        "-# 2 links taken out: not in anything I read for this reply, so treat what they pointed to as unconfirmed."), out)
    _check("a markdown link keeps its words", links.guard(f"See [the posting]({MADE_UP}).", set())
           .startswith("See the posting."), links.guard(f"See [the posting]({MADE_UP}).", set()))
    _check("www, the scheme and a closing slash are the same page",
           links.guard("https://www.kettle.example/a/", links.found("http://kettle.example/a")) == "https://www.kettle.example/a/")
    _check("a site read without its scheme may be written with one",
           links.guard("Their site: https://kettleworks.example", links.found("Website: kettleworks.example"))
           == "Their site: https://kettleworks.example")
    _check("an address inside an email is not a site read", "taken out" in
           links.guard("https://kettleworks.example", links.found("sales@kettleworks.example")))
    _check("a reply with no links is untouched", links.guard("No links here.", set()) == "No links here.")
    _check("nor one whose links were all read", links.guard(f"Here: {REAL}", seen) == f"Here: {REAL}")


def _script(*steps):
    calls = []

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


def test_a_react_turn() -> None:
    from agents import react
    root = Path(tempfile.mkdtemp())
    (root / "memory").mkdir()
    (root / "memory" / "jobs_found.md").write_text(f"# Jobs\n\n- QA Tester, Kettle Works\n  {REAL}\n", encoding="utf-8")
    real = (config.BASE_DIR, config.MEMORY_DIR, react._get_client)
    try:
        config.BASE_DIR, config.MEMORY_DIR = root, root / "memory"
        react._get_client = lambda: _script(("memory_read", {"file": "jobs_found.md"}),
                                            f"Entry-level roles:\n{REAL}\n{MADE_UP}")
        out = asyncio.run(react.handle("only entry level positions please", []))
        _check("what a tool returned this turn is kept", REAL in out, out)
        _check("what it did not is taken out", MADE_UP not in out and "1 link taken out" in out, out)

        react._get_client = lambda: _script(f"You sent me {MADE_UP}, which I cannot open.")
        out = asyncio.run(react.handle(f"what is at {MADE_UP}?", []))
        _check("a link the user sent can be named back", MADE_UP in out and "taken out" not in out, out)

        react._get_client = lambda: _script(f"As I said earlier: {REAL}")
        out = asyncio.run(react.handle("say that again", [{"role": "assistant", "content": f"Try {REAL}"}]))
        _check("and so can one already in the conversation", REAL in out and "taken out" not in out, out)
    finally:
        config.BASE_DIR, config.MEMORY_DIR, react._get_client = real


def test_direct() -> None:
    import orchestrator
    from agents import direct
    tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
    real = (config.MEMORY_DIR, context_store._DB_PATH, direct.handle, config.AUTHORIZED_USER_ID)

    async def chat(message, context, call):
        return f"Hermes lives at {MADE_UP}"

    async def notify(_):
        pass

    async def as_chat(text):
        return "Direct"

    try:
        config.MEMORY_DIR = Path(tmp.name)
        context_store._DB_PATH = config.MEMORY_DIR / "context.db"
        direct.handle, config.AUTHORIZED_USER_ID = chat, 42
        o = orchestrator.Orchestrator(notify)
        o._classify = as_chat
        reply, _ = asyncio.run(o.process(42, "where does Hermes live?"))
        _check("Direct, with no tools, cannot hand over a link it made up", MADE_UP not in reply
               and "taken out" in reply, reply)
        (config.MEMORY_DIR / "user.md").write_text("# User\n\n- Website: kettleworks.example\n", encoding="utf-8")

        async def from_notes(message, context, call):
            return "Your site is https://kettleworks.example"

        direct.handle = from_notes
        reply, _ = asyncio.run(o.process(42, "what is my website?"))
        _check("but can name one from the user's notes", reply == "Your site is https://kettleworks.example", reply)
    finally:
        (config.MEMORY_DIR, context_store._DB_PATH, direct.handle, config.AUTHORIZED_USER_ID) = real
        tmp.cleanup()


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            print(name)
            fn()
    if _failures:
        print(f"\n{len(_failures)} FAILED: {', '.join(_failures)}")
        sys.exit(1)
    print("\nall pass")
