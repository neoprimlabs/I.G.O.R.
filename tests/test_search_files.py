"""Tests for React's search_files tool - grep over IGOR's own source.

Run: python tests/test_search_files.py   (repo root, stdlib only, no network)

React could read a file it already knew the path of, and search memory, but had no
way to find a string anywhere in the source. On 2026-08-13 it went looking for a
route in, invented a search_code tool that does not exist, ran out of iterations and
described a system that was never built. The Hermes tool table has search_files for
exactly this; IGOR had the gap.

The security requirement is the same one the symmetric quarantine enforces: a search
must never surface a line from a private file. read_file refuses those by path, and
a grep that ignored that would be a hole straight around it.
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


def _tree():
    root = Path(tempfile.mkdtemp())
    config.BASE_DIR = root
    config.MEMORY_DIR = root / "memory"
    (root / "agents").mkdir()
    (config.MEMORY_DIR / "private").mkdir(parents=True)

    (root / "agents" / "prices.py").write_text(
        "def _lowest_price(title):\n"
        "    # the cheapest figure in a title\n"
        "    return min(found) if found else None\n", encoding="utf-8")
    (root / "agents" / "jobs.py").write_text(
        "def rank(found):\n    return _lowest_price_unrelated\n", encoding="utf-8")
    (root / "ARCHITECTURE.md").write_text(
        "# Architecture\n\nPrice watch reads price_watch.md.\n", encoding="utf-8")

    # Must never appear in output.
    (config.MEMORY_DIR / "resume.md").write_text(
        "Jordan Testcase - Kitchen Manager at Example Pizzeria\n", encoding="utf-8")
    (config.MEMORY_DIR / "drafts.md").write_text(
        "A draft about Kitchen Manager work\n", encoding="utf-8")
    (config.MEMORY_DIR / "private" / "profile.md").write_text(
        "Kitchen Manager, food service management\n", encoding="utf-8")

    # Noise that should be skipped rather than searched.
    (root / ".git").mkdir()
    (root / ".git" / "COMMIT_EDITMSG").write_text("_lowest_price\n", encoding="utf-8")
    (root / "__pycache__").mkdir()
    (root / "__pycache__" / "prices.cpython-310.pyc").write_bytes(b"\x00_lowest_price\x00")
    (root / "venv").mkdir()
    (root / "venv" / "thing.py").write_text("_lowest_price\n", encoding="utf-8")
    return root


def _run(pattern, **kw):
    import asyncio
    from agents import react
    return asyncio.run(react._search_source(pattern, **kw))


def test_it_finds_a_symbol() -> None:
    _tree()
    out = _run("_lowest_price")
    _check("the definition is found", "prices.py" in out, out)
    _check("with a line number", ":1" in out or ":3" in out, out)
    _check("the other reference is found too", "jobs.py" in out, out)
    _check("the matching line is shown", "_lowest_price" in out, out)


def test_private_files_are_never_searched() -> None:
    """The whole point of _is_private is that these cannot be read. A grep that
    walked them would report their contents a line at a time."""
    _tree()
    out = _run("Kitchen Manager")
    _check("a guarded file's content does not appear",
           "Example Pizzeria" not in out, out)
    _check("a sealed file's content does not appear",
           "A draft about" not in out, out)
    _check("nor anything under memory/private",
           "food service management" not in out, out)
    _check("and the filenames are not leaked either",
           "resume.md" not in out and "drafts.md" not in out, out)
    _check("it reports no matches rather than pretending to search",
           "no match" in out.lower(), out)


def test_noise_is_skipped() -> None:
    _tree()
    out = _run("_lowest_price")
    _check(".git is not searched", "COMMIT_EDITMSG" not in out, out)
    _check("__pycache__ is not searched", ".pyc" not in out, out)
    _check("the venv is not searched", "venv" not in out, out)


def test_it_is_a_regex_and_survives_a_bad_one() -> None:
    _tree()
    out = _run(r"def _lowest_\w+")
    _check("a regex matches", "prices.py" in out, out)

    out = _run("[unclosed")
    _check("an invalid regex is reported, not raised",
           "invalid" in out.lower() or "could not" in out.lower(), out)
    _check("and it says what was wrong with it", "[unclosed" in out, out)


def test_the_glob_narrows_it() -> None:
    _tree()
    out = _run("price", glob="*.md")
    _check("only markdown is searched", "ARCHITECTURE.md" in out, out)
    _check("python is excluded by the glob", "prices.py" not in out, out)


def test_context_lines() -> None:
    """2026-10-12: a live delegated worker found _watcher_status at monitor.py:890,
    then ran out of iterations trying to read its docstring - read_file pages by
    character offset and the worker had a line number. Context lines answer that in
    one call instead of several guesses at an offset."""
    _tree()

    plain = _run("the cheapest figure")
    _check("without context only the matching line comes back",
           "def _lowest_price" not in plain, plain)

    out = _run("the cheapest figure", context=2)
    _check("with context the line above is included",
           "def _lowest_price" in out, out)
    _check("and the line below", "return min(found)" in out, out)
    _check("every line is still labelled with its own number",
           out.count("prices.py:") >= 3, out)

    _check("context is clamped rather than trusted",
           "prices.py" in _run("the cheapest figure", context=9999))
    _check("a negative value behaves like none",
           "def _lowest_price" not in _run("the cheapest figure", context=-3))

    # Context must not become a way to page a private file one window at a time.
    private = _run("Kitchen Manager", context=6)
    _check("a private file stays unreadable even with context",
           "Example Pizzeria" not in private and "food service" not in private, private)


def test_output_is_capped() -> None:
    """A tool result over the cap gets chopped by the generic truncation with advice
    this tool cannot honour. Cap it here, and say so."""
    root = _tree()
    big = "\n".join(f"line {i} needle" for i in range(500))
    (root / "agents" / "big.py").write_text(big, encoding="utf-8")

    out = _run("needle")
    _check("the output is bounded", len(out) < 4000, f"{len(out)} chars")
    _check("and says it was truncated",
           "more match" in out.lower() or "truncated" in out.lower(), out[-300:])


def test_nothing_found_is_not_an_error() -> None:
    _tree()
    out = _run("thisstringappearsnowhere")
    _check("a miss is reported plainly", "no match" in out.lower(), out)
    _check("and echoes the pattern so the model can see what it asked",
           "thisstringappearsnowhere" in out, out)


def test_the_tool_is_registered() -> None:
    from agents import react

    names = [t["function"]["name"] for t in react._openai_tools()]
    _check("search_files is offered to the model", "search_files" in names, str(names))

    spec = next((t for t in react._openai_tools()
                 if t["function"]["name"] == "search_files"), None)
    _check("it takes a pattern",
           spec and "pattern" in spec["function"]["parameters"]["properties"], str(spec))
    _check("pattern is required",
           spec and "pattern" in spec["function"]["parameters"].get("required", []), str(spec))


if __name__ == "__main__":
    print("finding a symbol")
    test_it_finds_a_symbol()
    print("\nprivate files")
    test_private_files_are_never_searched()
    print("\nnoise")
    test_noise_is_skipped()
    print("\nregex handling")
    test_it_is_a_regex_and_survives_a_bad_one()
    print("\nthe glob")
    test_the_glob_narrows_it()
    print("\ncontext lines")
    test_context_lines()
    print("\noutput cap")
    test_output_is_capped()
    print("\nno matches")
    test_nothing_found_is_not_an_error()
    print("\nregistration")
    test_the_tool_is_registered()

    if _failures:
        print(f"\n{len(_failures)} FAILED: {', '.join(_failures)}")
        sys.exit(1)
    print("\nall pass")
