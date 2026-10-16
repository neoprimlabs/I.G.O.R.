"""Tests for the standing goals file: goals.py and React's goal tool.

Run: python tests/test_goals.py   (repo root, stdlib only, no API calls)
"""

import sys
import tempfile
from datetime import datetime, timezone
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


def _utc(*args) -> datetime:
    return datetime(*args, tzinfo=timezone.utc)


def _fresh():
    import goals
    tmp = tempfile.TemporaryDirectory()
    config.MEMORY_DIR = Path(tmp.name)
    return goals, tmp


def test_add_and_load() -> None:
    goals, tmp = _fresh()
    with tmp:
        reply = goals.add("Research autonomous systems", "research", now=_utc(2026, 10, 16))
        _check("add returns the id", "g1" in reply, reply)
        goals.add("Improve the router", "self_improve", now=_utc(2026, 10, 17))
        loaded = goals.load()
        _check("two goals load", [g["id"] for g in loaded] == ["g1", "g2"], str(loaded))
        g = loaded[0]
        _check("fields round-trip",
               g["statement"] == "Research autonomous systems" and g["kind"] == "research"
               and g["status"] == "active" and g["created"] == "2026-10-16"
               and g["next_action"] == "start"
               and g["last_reviewed"] == "2026-10-16T00:00:00+00:00", str(g))
        _check("bad kind refused", "Not added" in goals.add("x", "bogus"), "")
        _check("empty statement refused", "Not added" in goals.add("  ", "research"), "")
        _check("no tmp file left", not (config.MEMORY_DIR / "goals.md.tmp").exists(), "")


def test_next_to_work_picks_oldest_active() -> None:
    goals, tmp = _fresh()
    with tmp:
        _check("none when empty", goals.next_to_work() is None, "")
        goals.add("A", "research", now=_utc(2026, 10, 16))
        goals.add("B", "research", now=_utc(2026, 10, 14))
        goals.add("C", "self_improve", now=_utc(2026, 10, 15))
        _check("oldest last_reviewed wins", goals.next_to_work()["id"] == "g2", "")
        goals.note_progress("g2", "read the paper", now=_utc(2026, 10, 20))
        nxt = goals.next_to_work()
        _check("progress moves it to the back", nxt["id"] == "g3", str(nxt))
        g2 = next(g for g in goals.load() if g["id"] == "g2")
        _check("note_progress stores next_action and time",
               g2["next_action"] == "read the paper"
               and g2["last_reviewed"] == "2026-10-20T00:00:00+00:00", str(g2))


def test_pause_skips() -> None:
    goals, tmp = _fresh()
    with tmp:
        goals.add("A", "research", now=_utc(2026, 10, 14))
        goals.add("B", "research", now=_utc(2026, 10, 15))
        goals.set_status("g1", "paused")
        _check("paused is skipped", goals.next_to_work()["id"] == "g2", "")
        goals.set_status("g2", "paused")
        _check("none when all paused", goals.next_to_work() is None, "")
        goals.set_status("g1", "active")
        _check("resumed is picked", goals.next_to_work()["id"] == "g1", "")
        _check("unknown id reported", "No goal" in goals.set_status("g9", "paused"), "")
        _check("bad status refused", "Not changed" in goals.set_status("g1", "bogus"), "")


def test_retire_archives() -> None:
    goals, tmp = _fresh()
    with tmp:
        goals.add("A", "research")
        goals.add("B", "research")
        goals.set_status("g1", "retired")
        _check("gone from goals", [g["id"] for g in goals.load()] == ["g2"], "")
        archive = (config.MEMORY_DIR / "goals_archive.md").read_text(encoding="utf-8")
        _check("block in archive", "## g1 A" in archive and "status: retired" in archive, archive)
        goals.add("C", "research")
        _check("ids are not reused", goals.load()[-1]["id"] == "g3", str(goals.load()))


def test_malformed_block_skipped() -> None:
    goals, tmp = _fresh()
    with tmp:
        (config.MEMORY_DIR / "goals.md").write_text(
            "## g1 No kind here\nstatus: active\ncreated: 2026-10-16\n"
            "last_reviewed: 2026-10-16T00:00:00+00:00\nnext_action: start\n\n"
            "## g2 Fine goal\nkind: research\nstatus: active\ncreated: 2026-10-16\n"
            "last_reviewed: 2026-10-16T00:00:00+00:00\nnext_action: start\n",
            encoding="utf-8")
        loaded = goals.load()
        _check("only the good goal loads", [g["id"] for g in loaded] == ["g2"], str(loaded))
        goals.add("New", "research")
        _check("next id clears the skipped one", goals.load()[-1]["id"] == "g3", "")


def test_run_tool_actions() -> None:
    goals, tmp = _fresh()
    with tmp:
        _check("empty list", "No goals" in goals.run_tool({"action": "list"}), "")
        reply = goals.run_tool({"action": "add", "statement": "Study X", "kind": "research",
                                "id": ""})
        _check("add via tool", "g1" in reply, reply)
        _check("list shows it", "Study X" in goals.run_tool({"action": "list"}), "")
        _check("pause", "g1" in goals.run_tool({"action": "pause", "id": "g1"}), "")
        _check("paused state stored", goals.load()[0]["status"] == "paused", "")
        goals.run_tool({"action": "resume", "id": "g1"})
        _check("resume", goals.load()[0]["status"] == "active", "")
        goals.run_tool({"action": "retire", "id": "g1"})
        _check("retire", goals.load() == [], "")
        _check("unknown action", "Unknown action" in goals.run_tool({"action": "zap"}), "")
        _check("add without kind refused", "Not added" in goals.run_tool(
            {"action": "add", "statement": "S", "kind": ""}), "")


def test_react_wiring() -> None:
    import asyncio
    from agents import react
    names = [t["name"] for t in react._TOOLS]
    _check("React offers goal", "goal" in names, str(names))
    _check("goal quarantined after web", "goal" in react._QUARANTINED_AFTER_WEB, "")
    _check("goal forbidden to workers", "goal" in react._WORKER_FORBIDDEN, "")
    goals, tmp = _fresh()
    with tmp:
        reply = asyncio.run(react._execute_tool(
            "goal", {"action": "add", "statement": "S", "kind": "research"}))
        _check("dispatch reaches goals", "g1" in reply, reply)


if __name__ == "__main__":
    test_add_and_load()
    test_next_to_work_picks_oldest_active()
    test_pause_skips()
    test_retire_archives()
    test_malformed_block_skipped()
    test_run_tool_actions()
    test_react_wiring()

    if _failures:
        print(f"\n{len(_failures)} FAILED: {', '.join(_failures)}")
        sys.exit(1)
    print("\nall pass")
