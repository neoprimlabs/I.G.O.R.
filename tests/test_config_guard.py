"""ConfigEdit must not rewrite a setting nobody named.

Run: python tests/test_config_guard.py   (repo root, stdlib only, no API calls)

2026-09-14, verbatim from Discord. The user asked for a random check-in, IGOR
scheduled one, and the user followed up with "Do it later.. around 11:40". That
routed CONFIG, and ConfigEdit did not decline it: it wrote `time: 11:40 UTC` into
schedule_config.md - the morning digest - and answered "Updated schedule_config.md.
That file is read at startup, so it needs a restart to take effect."

The digest kept arriving at 09:00 EDT only because that file is read at startup. The
next restart would have moved it to 07:40. It was found six days later by reading the
file. The user did see a confirmation; it just never said what changed.

Two controls, both in code, because ConfigEdit cannot see the previous message and a
prompt rule would be re-derived wrong at the next call site:

1. The message must name the subject of the file being written.
2. The reply shows the lines that changed, so a wrong write is obvious on arrival.
"""

import asyncio
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


SCHEDULE_NOW = "# Schedule Config\n\n## morning_digest\ntime: 13:00 UTC\n"
SCHEDULE_1140 = "# Schedule Config\n\n## morning_digest\ntime: 11:40 UTC\n"
DIGEST_NOW = "# Digest Config\n\n## Sections\n- tasks\n- ai_news\n- daily_forecast\n"


def _fresh():
    config.MEMORY_DIR = Path(tempfile.mkdtemp())
    (config.MEMORY_DIR / "schedule_config.md").write_text(SCHEDULE_NOW, encoding="utf-8")
    (config.MEMORY_DIR / "digest_config.md").write_text(DIGEST_NOW, encoding="utf-8")
    (config.MEMORY_DIR / "watchlist.md").write_text("# Monitor Watchlist\n\n- System health\n",
                                                    encoding="utf-8")
    return config.MEMORY_DIR


def _replies(filename, content):
    async def _call(system, messages, max_tokens=2048, model=None):
        return f"{filename}\n<<<FILE\n{content}>>>FILE"
    return _call


def test_subject_gate() -> None:
    from agents import prod_memory

    named = [
        ("change the digest time to 14:00", "schedule_config.md"),
        ("move the morning digest to 8am", "schedule_config.md"),
        ("drop weather from the digest", "digest_config.md"),
        ("add unreal engine to the watchlist", "watchlist.md"),
    ]
    for message, filename in named:
        _check(f"names the subject: {message[:38]}",
               prod_memory._mentions_subject(message, filename), filename)

    unnamed = [
        ("Do it later.. around 11:40", "schedule_config.md"),
        ("make it 2pm instead", "schedule_config.md"),
        ("actually, a bit later", "schedule_config.md"),
        ("can you do 9 instead", "digest_config.md"),
    ]
    for message, filename in unnamed:
        _check(f"names nothing: {message[:38]}",
               not prod_memory._mentions_subject(message, filename), filename)


async def test_the_september_14_message() -> None:
    from agents import prod_memory

    path = _fresh()
    out = await prod_memory.handle("Do it later.. around 11:40",
                                   _replies("schedule_config.md", SCHEDULE_1140))
    _check("the follow-up is declined, not applied", out is None, repr(out))
    _check("and the digest schedule is untouched",
           (path / "schedule_config.md").read_text(encoding="utf-8") == SCHEDULE_NOW,
           (path / "schedule_config.md").read_text(encoding="utf-8"))


async def test_a_real_change_still_works_and_shows_itself() -> None:
    from agents import prod_memory

    path = _fresh()
    out = await prod_memory.handle("change the digest time to 11:40",
                                   _replies("schedule_config.md", SCHEDULE_1140))
    _check("a request that names the setting is applied",
           (path / "schedule_config.md").read_text(encoding="utf-8").strip() == SCHEDULE_1140.strip(),
           (path / "schedule_config.md").read_text(encoding="utf-8"))
    _check("the reply shows what it replaced", "13:00" in (out or ""), repr(out))
    _check("and what it wrote", "11:40" in (out or ""), repr(out))
    _check("and still says a restart is needed", "restart" in (out or "").lower(), repr(out))

    path = _fresh()
    out = await prod_memory.handle("drop daily_forecast from the digest",
                                   _replies("digest_config.md", "# Digest Config\n\n## Sections\n- tasks\n- ai_news\n"))
    _check("a removed line is shown too", "daily_forecast" in (out or ""), repr(out))


async def test_unchanged_is_still_treated_as_a_question() -> None:
    from agents import prod_memory

    _fresh()
    out = await prod_memory.handle("what time is the digest set for",
                                   _replies("schedule_config.md", SCHEDULE_NOW))
    _check("an identical rewrite is still reported, not written",
           out is not None and "already reads" in out, repr(out))


if __name__ == "__main__":
    print("subject gate")
    test_subject_gate()
    print("\nthe message that caused this")
    asyncio.run(test_the_september_14_message())
    print("\nreal changes still work")
    asyncio.run(test_a_real_change_still_works_and_shows_itself())
    print("\nquestions")
    asyncio.run(test_unchanged_is_still_treated_as_a_question())

    if _failures:
        print(f"\n{len(_failures)} FAILED: {', '.join(_failures)}")
        sys.exit(1)
    print("\nall pass")
