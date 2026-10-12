"""Tests for task_update - adding and completing tasks without rewriting the file.

Run: python tests/test_task_tool.py   (repo root, stdlib only, no API calls)

React could already reach tasks.md through memory_write, which rewrites the whole
file from whatever the model produced. That is how ConfigEdit destroyed
schedule_config.md: asked to move a check-in, it rewrote the file and the loss went
unnoticed for six days. tasks.md is read by the morning digest and by presence, so
the same mistake there is silent in the same way.

This tool edits in place: one line appended, or one line marked [x], with every
other line in the file untouched. Hermes' table calls the equivalent `todo`.
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


TASKS = """# Tasks

Notes at the top that must survive any edit.

- Investigate X/Twitter fetching for the Research agent
- [x] Test write confirmation
- [ ] Decide on the eBay Browse API

## Someday

- Learn to juggle
"""


def _fresh():
    config.MEMORY_DIR = Path(tempfile.mkdtemp())
    (config.MEMORY_DIR / "tasks.md").write_text(TASKS, encoding="utf-8")
    return config.MEMORY_DIR


def _run(action, text=""):
    import asyncio
    from agents import react
    return asyncio.run(react._task_update(action, text))


def _file():
    return (config.MEMORY_DIR / "tasks.md").read_text(encoding="utf-8")


def test_listing_matches_what_the_digest_sees() -> None:
    from agents import monitor

    _fresh()
    out = _run("list")
    _check("an open plain bullet is listed", "X/Twitter" in out, out)
    _check("an open checkbox is listed", "eBay Browse" in out, out)
    _check("a completed task is not", "Test write confirmation" not in out, out)

    # One parser, so the tool and the digest can never disagree about what is open.
    parsed = monitor._parse_tasks(_file())
    _check("the tool and the digest agree on the count",
           len(parsed) == 3, f"tool listed differently: {parsed}")


def test_adding_appends_and_preserves_everything() -> None:
    _fresh()
    out = _run("add", "Generalise the research loop")
    _check("it confirms what it added", "Generalise" in out, out)

    text = _file()
    _check("the task is in the file", "- Generalise the research loop" in text, text)
    _check("the header survives", text.startswith("# Tasks"), text[:40])
    _check("the prose at the top survives", "must survive any edit" in text, text)
    _check("the completed task is untouched", "- [x] Test write confirmation" in text, text)
    _check("the other open tasks survive",
           "X/Twitter" in text and "eBay Browse" in text, text)
    _check("the later section survives", "## Someday" in text and "juggle" in text, text)


def test_adding_the_same_task_twice_is_refused() -> None:
    _fresh()
    _run("add", "Generalise the research loop")
    out = _run("add", "generalise the RESEARCH loop")
    _check("a duplicate is refused rather than appended",
           "already" in out.lower(), out)
    _check("and only one copy exists",
           _file().lower().count("generalise the research loop") == 1, _file())


def test_completing_marks_in_place() -> None:
    _fresh()
    out = _run("done", "X/Twitter")
    _check("it confirms what it completed", "X/Twitter" in out, out)

    text = _file()
    _check("the line is marked done",
           "- [x] Investigate X/Twitter fetching for the Research agent" in text, text)
    _check("it is no longer open", "X/Twitter" not in _run("list"), _run("list"))
    _check("nothing else changed",
           "eBay Browse" in text and "juggle" in text and "must survive" in text, text)
    _check("the file did not lose its header", text.startswith("# Tasks"), text[:40])


def test_completing_an_unchecked_checkbox() -> None:
    _fresh()
    _run("done", "eBay")
    _check("a '- [ ]' task becomes '- [x]'",
           "- [x] Decide on the eBay Browse API" in _file(), _file())
    _check("and is not duplicated",
           _file().count("eBay Browse API") == 1, _file())


def test_an_ambiguous_completion_is_refused() -> None:
    """Guessing which task was meant is how the wrong one gets closed silently."""
    _fresh()
    _run("add", "Review the research notes")
    out = _run("done", "research")
    _check("it refuses rather than picking one",
           "more than one" in out.lower() or "ambiguous" in out.lower(), out)
    _check("it lists the candidates so the model can be specific",
           "X/Twitter" in out and "Review the research notes" in out, out)
    _check("and nothing was marked done", "[x] Investigate X/Twitter" not in _file(), _file())


def test_completing_something_that_is_not_there() -> None:
    _fresh()
    out = _run("done", "buy a boat")
    _check("it says there was no match", "no open task" in out.lower(), out)
    _check("and the file is unchanged", _file() == TASKS, "file was rewritten")


def test_bad_input_changes_nothing() -> None:
    _fresh()
    _check("an unknown action is refused", "unknown" in _run("destroy", "x").lower())
    _check("add with no text is refused", "text" in _run("add", "   ").lower())
    _check("done with no text is refused", "text" in _run("done", "").lower())
    _check("none of that touched the file", _file() == TASKS, "file was rewritten")


def test_a_missing_file_is_created_not_crashed() -> None:
    config.MEMORY_DIR = Path(tempfile.mkdtemp())
    out = _run("add", "First ever task")
    _check("adding to a missing file works", "First ever task" in out, out)
    _check("and the file now exists with a header",
           (config.MEMORY_DIR / "tasks.md").read_text(encoding="utf-8").startswith("# Tasks"),
           (config.MEMORY_DIR / "tasks.md").read_text(encoding="utf-8"))

    config.MEMORY_DIR = Path(tempfile.mkdtemp())
    _check("listing a missing file says so rather than raising",
           "no open task" in _run("list").lower(), _run("list"))


def test_the_tool_is_registered_and_quarantined() -> None:
    from agents import react

    specs = {t["function"]["name"]: t for t in react._openai_tools()}
    _check("task_update is offered to the model", "task_update" in specs, str(list(specs)))
    spec = specs.get("task_update")
    if spec:
        props = spec["function"]["parameters"]["properties"]
        _check("it takes an action", "action" in props, str(props))
        _check("the actions are constrained to an enum",
               "enum" in props.get("action", {}), str(props.get("action")))

    # It writes to a file the digest and presence both read, so it is a state change.
    _check("it is switched off after a web read",
           "task_update" in react._QUARANTINED_AFTER_WEB,
           str(sorted(react._QUARANTINED_AFTER_WEB)))


if __name__ == "__main__":
    print("listing")
    test_listing_matches_what_the_digest_sees()
    print("\nadding")
    test_adding_appends_and_preserves_everything()
    test_adding_the_same_task_twice_is_refused()
    print("\ncompleting")
    test_completing_marks_in_place()
    test_completing_an_unchecked_checkbox()
    test_an_ambiguous_completion_is_refused()
    test_completing_something_that_is_not_there()
    print("\nbad input")
    test_bad_input_changes_nothing()
    print("\nmissing file")
    test_a_missing_file_is_created_not_crashed()
    print("\nregistration")
    test_the_tool_is_registered_and_quarantined()

    if _failures:
        print(f"\n{len(_failures)} FAILED: {', '.join(_failures)}")
        sys.exit(1)
    print("\nall pass")
