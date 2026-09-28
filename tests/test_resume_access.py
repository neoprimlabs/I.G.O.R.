"""Who may see the resume, and who may not.

Run: python tests/test_resume_access.py   (repo root, stdlib only, no API calls)

The rule is not "the resume is hidden". It is "the resume may reach anything that
cannot act on it". For a while the implementation was "the resume may not reach the
agent the user talks to", which is a different rule and a sillier one: asked "can
you read it now?", IGOR said no, while the same work history was ranking his job
postings every morning. The user's response was that this does not make much sense,
and he was right.

- Direct has no tools at all. It cannot fetch a URL, run a command, touch a file or
  write memory, so nothing it reads can leave except in a reply to the one person
  it talks to. It gets the resume, on the turns that are about it.
- React reads the open web and can act outward. It keeps the seal.
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


RESUME = "Brian Barnhart\n(412) 912-6399\nKitchen Manager, Pizzeria Davide 2022-2024\n"


def _fresh():
    root = Path(tempfile.mkdtemp())
    (root / "memory").mkdir()
    config.BASE_DIR = root
    config.MEMORY_DIR = root / "memory"
    (root / "memory" / "resume.md").write_text(RESUME, encoding="utf-8")
    return root


def test_direct_gets_it_when_asked() -> None:
    from agents import direct

    _fresh()
    asking = [
        "what's on my resume?",
        "can you read it now?, I mean the CV",
        "what does my work history look like",
        "help me with a cover letter",
        "how much experience do I have with kitchens",
    ]
    for message in asking:
        _check(f"loaded for: {message[:40]}",
               "Pizzeria Davide" in direct._resume_if_asked(message), message)

    not_asking = [
        "what's the weather looking like",
        "how's it going tonight",
        "run the digest",
        "what did you send me this morning",
    ]
    for message in not_asking:
        _check(f"not loaded for: {message[:40]}",
               direct._resume_if_asked(message) == "", message)


def test_a_missing_file_is_not_a_crash() -> None:
    from agents import direct

    config.MEMORY_DIR = Path(tempfile.mkdtemp())
    _check("no resume on disk returns nothing rather than raising",
           direct._resume_if_asked("what's on my resume?") == "")


def test_react_still_cannot_reach_it() -> None:
    """The seal that matters. React reads the open web and can act outward."""
    import asyncio
    from agents import react

    _fresh()
    out = asyncio.run(react._read_server_file("memory/resume.md"))
    _check("react read_file is still refused",
           "private file" in out.lower() and "912" not in out, out[:90])
    # Assert on leaked content, not the search term: a miss echoes the query back
    # ("No matches for 'Pizzeria'"), which fails on its own input. Second time today.
    out = asyncio.run(react._search_memory_files("Pizzeria"))
    _check("react search_memory still finds nothing",
           "resume.md" not in out and "Kitchen Manager" not in out and "912" not in out,
           out[:90])


def test_the_import_that_py_compile_cannot_see() -> None:
    """_resume_if_asked uses re. py_compile checks syntax, not names, so a missing
    import passes every gate and fails when the first message arrives."""
    import importlib
    from agents import direct

    importlib.reload(direct)
    _fresh()
    _check("direct resolves its own names at runtime",
           "Pizzeria" in direct._resume_if_asked("my resume"))


if __name__ == "__main__":
    print("direct, when asked")
    test_direct_gets_it_when_asked()
    print("\nno file")
    test_a_missing_file_is_not_a_crash()
    print("\nreact is still sealed")
    test_react_still_cannot_reach_it()
    print("\nimports resolve")
    test_the_import_that_py_compile_cannot_see()

    if _failures:
        print(f"\n{len(_failures)} FAILED: {', '.join(_failures)}")
        sys.exit(1)
    print("\nall pass")
