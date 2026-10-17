"""Two rules taken from Hermes, 2026-10-17.

Run: python tests/test_retention_and_reads.py   (repo root, no API calls)

1. History is kept by age, not only by count: Hermes prunes sessions inactive for
   90 days by default. IGOR_SPEC says "no hoarding of conversation history".
2. Everything read is untrusted (Hermes: "the trust boundary is the agent's
   capability surface, not the input channel"). research*.md is distilled from web
   pages and goals.md carries the model's web-distilled next_action, so reading
   either closes the turn's state-changing tools, as a web search does.
"""

import asyncio
import sqlite3
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import config

config.MEMORY_DIR = Path(tempfile.mkdtemp())

_failures: list[str] = []


def _check(name: str, condition: bool, detail: str = "") -> None:
    print(f"  {'pass' if condition else 'FAIL'}  {name}" + ("" if condition else f"  {detail}"))
    if not condition:
        _failures.append(name)


def test_history_kept_by_age() -> None:
    import context_store
    context_store._DB_PATH = config.MEMORY_DIR / "context.db"
    day = 86400
    now = time.time()
    with context_store._conn() as conn:
        for age_days, text in ((120, "very old"), (91, "just past"), (89, "just inside"), (1, "recent")):
            conn.execute("INSERT INTO messages (role, content, ts) VALUES (?, ?, ?)",
                         ("user", text, int(now - age_days * day)))
    context_store.append("user", "new message")
    with context_store._conn() as conn:
        kept = [r[0] for r in conn.execute("SELECT content FROM messages ORDER BY id")]
    _check("retention is 90 days", config.HISTORY_RETENTION_DAYS == 90, str(config.HISTORY_RETENTION_DAYS))
    _check("messages older than 90 days are deleted",
           "very old" not in kept and "just past" not in kept, str(kept))
    _check("messages inside 90 days are kept",
           kept == ["just inside", "recent", "new message"], str(kept))
    _check("the count ceiling stays as a backstop", context_store._MAX_STORED == 10000)


def test_web_derived_reads_close_the_turn() -> None:
    from agents import react
    # The production layout: read_file paths resolve against BASE_DIR, and memory is
    # BASE_DIR/memory. Path classification only - nothing here opens a file.
    temp, config.MEMORY_DIR = config.MEMORY_DIR, config.BASE_DIR / "memory"
    yes = [("memory_read", {"file": "research.md"}),
           ("read_file", {"path": "memory/research.md"}),
           ("read_file", {"path": "memory/research_20260814-032033.md"}),
           ("read_file", {"path": "memory/goals.md"}),
           ("read_file", {"path": "memory/work/g1/draft-2026-10-17.md"})]
    no = [("memory_read", {"file": "tasks.md"}),
          ("memory_read", {"file": "jobs_found.md"}),
          ("read_file", {"path": "agents/react.py"}),
          ("read_file", {"path": "memory/researcher_notes.txt"}),
          ("read_file", {"path": "docs/research.md"}),
          ("memory_read", {}),
          ("read_file", {"path": 7})]
    for name, args in yes:
        _check(f"untrusted read: {name} {args}", react._reads_untrusted(name, args))
    for name, args in no:
        _check(f"not an untrusted read: {name} {args}", not react._reads_untrusted(name, args))
    config.MEMORY_DIR = temp


def test_search_memory_skips_web_derived_files() -> None:
    from agents import react
    (config.MEMORY_DIR / "research.md").write_text("zebra crossing from the web\n", encoding="utf-8")
    (config.MEMORY_DIR / "research_20260814.md").write_text("zebra archive\n", encoding="utf-8")
    (config.MEMORY_DIR / "goals.md").write_text("next_action: zebra\n", encoding="utf-8")
    (config.MEMORY_DIR / "tasks.md").write_text("- feed the zebra\n", encoding="utf-8")
    out = asyncio.run(react._search_memory_files("zebra"))
    _check("search_memory still finds ordinary memory files", "tasks.md" in out, out)
    _check("but never returns web-derived files' text",
           "research" not in out and "goals.md" not in out, out)


if __name__ == "__main__":
    test_history_kept_by_age()
    test_web_derived_reads_close_the_turn()
    test_search_memory_skips_web_derived_files()
    if _failures:
        print(f"\n{len(_failures)} FAILED: {', '.join(_failures)}")
        sys.exit(1)
    print("\nall pass")
