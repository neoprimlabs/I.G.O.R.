"""Some memory files must be reachable by no agent. Until now, two of them were.

Run: python tests/test_private_files.py   (repo root, stdlib only, no API calls)

ARCHITECTURE.md has said this for weeks:

    corrections.md and drafts.md are readable by no agent: both hold text derived
    from untrusted input, so pulling them into a tool-bearing context would be a
    stored injection path.

It was not true. memory_read has an allowlist, but React reaches files two other
ways: search_memory globbed MEMORY_DIR/*.md with no exclusions, and read_file
accepted any path inside the IGOR root, which memory/ is. Both files were readable.

This matters more now that a resume lives there. The combination is the one
react.py was written to prevent - private data, untrusted web content, and an
outward call - and fetch_url is deliberately NOT quarantined after a web read,
because reading a search result is the ordinary research flow. So the private data
has to be unreachable rather than the outward call blocked.
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


def _fresh():
    """A memory dir inside a fake IGOR root, since read_file resolves against it."""
    root = Path(tempfile.mkdtemp())
    (root / "memory").mkdir()
    (root / "memory" / "private").mkdir()
    config.BASE_DIR = root
    config.MEMORY_DIR = root / "memory"

    (root / "ARCHITECTURE.md").write_text("# Architecture\nPublic document.\n", encoding="utf-8")
    (root / "memory" / "tasks.md").write_text("- Investigate X/Twitter fetching\n", encoding="utf-8")
    (root / "memory" / "corrections.md").write_text(
        "## correction\nUser corrected: the digest goes out at 9\n", encoding="utf-8")
    (root / "memory" / "drafts.md").write_text(
        "## Universal Basic Income\nA draft nobody has read.\n", encoding="utf-8")
    (root / "memory" / "resume.md").write_text(
        "Brian Barnhart\n(412) 912-6399\nKitchen Manager, Pizzeria Davide\n", encoding="utf-8")
    (root / "memory" / "private" / "notes.md").write_text(
        "phone 412 912 6399 and home address\n", encoding="utf-8")
    return root


async def test_read_file() -> None:
    from agents import react

    _fresh()
    for private in ("memory/resume.md", "memory/corrections.md", "memory/drafts.md",
                    "memory/private/notes.md"):
        out = await react._read_server_file(private)
        _check(f"read_file refuses {private}",
               "private file" in out.lower() and "912" not in out and "digest goes out" not in out,
               out[:100])

    # 2026-09-28: asked "resume.md?", IGOR said "I don't see a resume.md file in the
    # current workspace" and offered to store one if the user pasted it. The file
    # exists. Denying it invites a paste into the chat, which is the exposure the
    # guard exists to prevent, so the refusal has to say which of the two it is.
    out = await react._read_server_file("memory/resume.md")
    low = out.lower()
    _check("the refusal says the file exists", "exists" in low, out[:120])
    _check("and does not read as missing",
           "not found" not in low and "no such" not in low, out[:120])
    _check("and tells it not to ask for a paste", "paste" in low, out[:120])

    out = await react._read_server_file("memory/nope.md")
    _check("a genuinely absent file still reads as absent",
           "not found" in out.lower() and "private" not in out.lower(), out[:100])

    # The traversal version of the same request.
    out = await react._read_server_file("memory/../memory/resume.md")
    _check("and refuses it by a roundabout path",
           "private file" in out.lower() and "912" not in out, out[:100])

    out = await react._read_server_file("memory/tasks.md")
    _check("an ordinary memory file still opens", "X/Twitter" in out, out[:80])
    out = await react._read_server_file("ARCHITECTURE.md")
    _check("and so does a repo file", "Public document" in out, out[:80])


async def test_search_memory() -> None:
    from agents import react

    _fresh()
    # Assert on leaked content and filenames, not the search term: a miss echoes the
    # query back ("No matches for '912'"), which would fail on its own input.
    out = await react._search_memory_files("912")
    _check("search_memory does not surface the resume",
           "resume.md" not in out and "Kitchen Manager" not in out, out[:120])

    out = await react._search_memory_files("digest goes out")
    _check("nor corrections",
           "corrections.md" not in out and "User corrected" not in out, out[:120])

    out = await react._search_memory_files("Universal Basic Income")
    _check("nor drafts", "draft nobody has read" not in out.lower(), out[:120])

    out = await react._search_memory_files("X/Twitter")
    _check("but it still searches ordinary files", "X/Twitter" in out, out[:120])


async def test_writes() -> None:
    """Read is the leak, but a private file should not be silently rewritten either."""
    from agents import react

    root = _fresh()
    out = await react._write_server_file("memory/resume.md", "overwritten")
    _check("write_file refuses a private file", "private file" in out.lower(), out[:90])
    _check("and the file is untouched",
           "912" in (root / "memory" / "resume.md").read_text(encoding="utf-8"))

    out = await react._patch_server_file("memory/corrections.md", "digest", "nonsense")
    _check("patch_file refuses one too", "private file" in out.lower(), out[:90])


if __name__ == "__main__":
    print("read_file")
    asyncio.run(test_read_file())
    print("\nsearch_memory")
    asyncio.run(test_search_memory())
    print("\nwrites")
    asyncio.run(test_writes())

    if _failures:
        print(f"\n{len(_failures)} FAILED: {', '.join(_failures)}")
        sys.exit(1)
    print("\nall pass")
