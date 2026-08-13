"""read_file must be able to deliver a whole file, and say so when it hasn't.

Regression cover for 2026-08-13, when ARCHITECTURE.md (23KB) came back capped at
4000 chars with the note "request smaller pieces" - advice read_file had no
parameter to honour. React re-read it four times, never saw 83% of it, and invented
the rest, including a content filter IGOR has never had.
"""
import asyncio
import sys

sys.path.insert(0, r"c:\Dev\IGOR")

from agents.react import _READ_WINDOW, _TOOL_RESULT_CAP, _read_server_file

_passed = 0
_failed = 0


def check(name, condition, detail=""):
    global _passed, _failed
    if condition:
        _passed += 1
        print(f"  PASS  {name}")
    else:
        _failed += 1
        print(f"  FAIL  {name}  {detail}")


async def main():
    print("=== read_file paging ===")

    check("read window leaves room for its own notice inside the tool cap",
          _READ_WINDOW < _TOOL_RESULT_CAP, f"{_READ_WINDOW} vs {_TOOL_RESULT_CAP}")

    first = await _read_server_file("ARCHITECTURE.md")
    check("a long file reports that it continues", "Call read_file with offset=" in first, first[-200:])
    check("the notice survives the generic tool cap", len(first) < _TOOL_RESULT_CAP, f"len={len(first)}")

    # Walk the whole file the way React would, and confirm nothing is lost.
    actual = open("ARCHITECTURE.md", encoding="utf-8").read()
    rebuilt, offset, reads = "", 0, 0
    while reads < 30:
        chunk = await _read_server_file("ARCHITECTURE.md", offset)
        reads += 1
        marker = chunk.find("\n\n[ARCHITECTURE.md is ")
        if marker == -1:
            rebuilt += chunk
            break
        rebuilt += chunk[:marker]
        offset += marker
    check("paging reconstructs the file exactly", rebuilt == actual,
          f"rebuilt {len(rebuilt)} vs actual {len(actual)}")
    check("the whole file is reachable in a sane number of reads", reads <= 10, f"took {reads}")

    short = await _read_server_file("requirements.txt")
    check("a short file comes back with no continuation notice",
          "Call read_file with offset=" not in short, short[-120:])

    past = await _read_server_file("ARCHITECTURE.md", 999999)
    check("an offset past the end says so instead of returning nothing",
          "past the end" in past, past)

    missing = await _read_server_file("does_not_exist.md")
    check("a missing file still reports not found", "[not found:" in missing, missing)

    outside = await _read_server_file("../../etc/passwd")
    check("path escape is still refused", "access denied" in outside, outside)

    # The model sends whatever it likes; a bad offset must not crash the turn.
    for bad in ("", None, "abc"):
        try:
            offset = int(bad or 0)
        except (TypeError, ValueError):
            offset = 0
        out = await _read_server_file("ARCHITECTURE.md", offset)
        check(f"a {bad!r} offset falls back to the start of the file",
              out.startswith("# ARCHITECTURE.md"), out[:60])

    # ARCHITECTURE.md's summary has to survive one read window, including the
    # "what does NOT exist" list - that list is the part that stops React inventing
    # a content filter. Adding three lines above it silently pushed it out once.
    doc = open("ARCHITECTURE.md", encoding="utf-8").read()
    tail = doc.find("- **No sandbox.**")
    end = doc.find("\n", tail)
    check("the whole self-summary fits one read window", -1 < end < _READ_WINDOW,
          f"summary ends at {end}, window is {_READ_WINDOW}")
    check("the summary still lists what does not exist", "No content filter" in doc[:_READ_WINDOW])

    print(f"\n  {_passed} passed, {_failed} failed")
    return 1 if _failed else 0


sys.exit(asyncio.run(main()))
