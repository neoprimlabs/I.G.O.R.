"""An application draft claims only what IGOR holds or read (applications.py).

2026-11-01: a cover letter from Direct claimed a router at 31/31, a 12% accuracy gain, BLEU and ROUGE
pipelines and weekly reports to stakeholders. Only the count of automated checks was in the resume.

Run: python tests/test_applications.py   (repo root, no API calls)
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

_failures: list[str] = []

RESUME = """# Resume

Kettle Works QA lead, 2021 to 2024. Built I.G.O.R., a personal agent with 1,580 automated checks,
in Python, run on Discord.
"""
POSTING = "Kettle Testing Contractor - $2,000/month, remote. Wants Python and test suites."

LETTER = """Below is a draft you can paste in.

Hi [Hiring Manager's Name],

I am writing about the Kettle Testing Contractor role at $2,000 a month. I built IGOR, a personal agent with 1,580 automated checks.

**Relevant experience**

* Wrote the test suites for Kettle Works from 2021 to 2024, in Python.
* Ran BLEU and ROUGE scoring over every kettle manual.
* Shipped pipelines on SageMaker for the boiling team.

My router scored 31/31 on live checks. I raised accuracy by 12 % across the line. I would be glad to talk.

Best regards,
Kettle Tester"""


def _check(name: str, condition: bool, detail: str = "") -> None:
    print(f"  {'pass' if condition else 'FAIL'}  {name}" + ("" if condition else f"  {detail}"))
    if not condition:
        _failures.append(name)


def _memory() -> Path:
    root = Path(tempfile.mkdtemp())
    (root / "memory").mkdir()
    (root / "memory" / "resume.md").write_text(RESUME, encoding="utf-8")
    return root


def test_the_check() -> None:
    import applications
    real = config.MEMORY_DIR
    try:
        config.MEMORY_DIR = _memory() / "memory"
        out = applications.guard(LETTER, "draft the cover letter", POSTING)
        for made_up in ("31/31", "12 %", "BLEU", "SageMaker"):
            _check(f"made up, taken out: {made_up}", made_up not in out.split("-# ")[0], out)
        _check("the line says what went", "-# Taken out of the draft" in out
               and "31/31" in out.split("-# ")[1] and "SageMaker" in out.split("-# ")[1], out)
        for held in ("1,580 automated checks", "$2,000 a month", "from 2021 to 2024, in Python",
                     "I would be glad to talk.", "Hi [Hiring Manager's Name],", "Best regards,"):
            _check(f"held, kept: {held}", held in out, out)
        _check("IGOR counts as the I.G.O.R. in the resume", "I built IGOR" in out, out)

        _check("a letter that claims only what is held passes untouched",
               applications.guard(LETTER.split("**Relevant")[0] + "Best regards,", "cover letter", POSTING)
               == LETTER.split("**Relevant")[0] + "Best regards,")
        chat = "Kettles boil at 100 C, and 12% of them whistle. The BLEU score is a translation metric."
        _check("a reply that is not an application is never touched", applications.guard(chat, "tell me") == chat)
        _check("nor is a letter-shaped reply without a closing",
               applications.guard("Hi [Hiring Manager], I raised accuracy 12%.", "draft it")
               == "Hi [Hiring Manager], I raised accuracy 12%.")
    finally:
        config.MEMORY_DIR = real


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
    root = _memory()
    (root / "memory" / "jobs_found.md").write_text(f"# Jobs\n\n- {POSTING}\n", encoding="utf-8")
    real = (config.BASE_DIR, config.MEMORY_DIR, react._get_client)
    try:
        config.BASE_DIR, config.MEMORY_DIR = root, root / "memory"
        react._get_client = lambda: _script(("memory_read", {"file": "jobs_found.md"}), LETTER)
        out = asyncio.run(react.handle("draft the application for the kettle role", []))
        _check("React: what a tool read this turn is held", "$2,000 a month" in out, out)
        _check("React: what nothing shows is taken out", "SageMaker" not in out.split("-# ")[0]
               and "Taken out of the draft" in out, out)
    finally:
        config.BASE_DIR, config.MEMORY_DIR, react._get_client = real


def test_direct() -> None:
    import orchestrator
    from agents import direct
    tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
    real = (config.MEMORY_DIR, context_store._DB_PATH, direct.handle, config.AUTHORIZED_USER_ID)

    async def drafting(message, context, call):
        return LETTER

    async def notify(_):
        pass

    async def as_chat(text):
        return "Direct"

    try:
        config.MEMORY_DIR = Path(tmp.name)
        (config.MEMORY_DIR / "resume.md").write_text(RESUME, encoding="utf-8")
        context_store._DB_PATH = config.MEMORY_DIR / "context.db"
        direct.handle, config.AUTHORIZED_USER_ID = drafting, 42
        o = orchestrator.Orchestrator(notify)
        o._classify = as_chat
        context_store.append("assistant", f"[sent proactively]\nNew postings:\n{POSTING}")
        context_store.append("user", "tell me more about the kettle one")
        context_store.append("assistant", "It wants BLEU and ROUGE scoring and SageMaker pipelines.")
        o._context = context_store.load_timed()
        reply, _ = asyncio.run(o.process(42, "I would happily accept that one, write it"))
        _check("Direct: the posting IGOR sent unprompted is held", "$2,000 a month" in reply, reply)
        _check("Direct: the made-up score is taken out", "31/31" not in reply.split("-# ")[0]
               and "Taken out of the draft" in reply, reply)
        _check("Direct: IGOR's own earlier reply is no source for BLEU", "BLEU" not in reply.split("-# ")[0], reply)
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
