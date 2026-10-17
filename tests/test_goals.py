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


def test_rewrites_keep_malformed_blocks() -> None:
    goals, tmp = _fresh()
    with tmp:
        bad = "## g1 Hand edited\nkin: research\nstatus: active"
        good = ("## g2 Fine goal\nkind: research\nstatus: active\ncreated: 2026-10-16\n"
                "last_reviewed: 2026-10-16T00:00:00+00:00\nnext_action: start\n")
        path = config.MEMORY_DIR / "goals.md"
        path.write_text(bad + "\n\n" + good, encoding="utf-8")
        goals.set_status("g2", "paused")
        _check("kept after pause", bad in path.read_text(encoding="utf-8"), "")
        _check("g2 paused", goals.load()[0]["status"] == "paused", "")
        goals.note_progress("g2", "next thing", now=_utc(2026, 10, 20))
        _check("kept after progress", bad in path.read_text(encoding="utf-8"), "")
        _check("progress stored", goals.load()[0]["next_action"] == "next thing", "")
        goals.set_status("g2", "retired")
        _check("kept after retire", bad in path.read_text(encoding="utf-8"), "")
        _check("g2 left goals.md", "g2" not in path.read_text(encoding="utf-8"), "")


def test_invalid_values_skipped() -> None:
    goals, tmp = _fresh()
    with tmp:
        tail = "created: 2026-10-16\nlast_reviewed: 2026-10-16T00:00:00+00:00\nnext_action: start\n"
        (config.MEMORY_DIR / "goals.md").write_text(
            "## g1 Typo status\nkind: research\nstatus: actve\n" + tail + "\n"
            "## g2 Bad kind\nkind: bogus\nstatus: active\n" + tail + "\n"
            "## g3 Good\nkind: self_improve\nstatus: paused\n" + tail, encoding="utf-8")
        _check("only the valid goal loads", [g["id"] for g in goals.load()] == ["g3"], "")
        _check("nothing worked from invalid ones", goals.next_to_work() is None, "")


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


def test_tool_dispatch_survives_bad_input() -> None:
    import asyncio
    from agents import react
    goals, tmp = _fresh()
    with tmp:
        goals.add("S", "research")
        for label, name, args in (
                ("goal with an integer id", "goal", {"action": "retire", "id": 5}),
                ("goal with a list statement", "goal", {"action": "add", "statement": ["a"], "kind": "research"}),
                ("proposal with an integer id", "proposal", {"action": "approve", "id": 3})):
            try:
                out = asyncio.run(react._execute_tool(name, args))
            except Exception as e:
                out = None
                _check(f"{label} does not raise", False, repr(e))
            if out is not None:
                _check(f"{label} returns text", isinstance(out, str) and out.startswith("["), str(out))

        def boom(inputs):
            raise OSError("disk full")

        real = goals.run_tool
        goals.run_tool = boom
        try:
            out = asyncio.run(react._execute_tool("goal", {"action": "list"}))
        finally:
            goals.run_tool = real
        _check("an OSError becomes an error string", out.startswith("[goal failed: OSError"), str(out))


def test_next_action_is_capped() -> None:
    goals, tmp = _fresh()
    with tmp:
        goals.add("S", "research")
        goals.note_progress("g1", "x" * 500)
        got = goals.load()[0]["next_action"]
        _check("next_action capped at 200", len(got) == 200, str(len(got)))


if __name__ == "__main__":
    test_add_and_load()
    test_next_to_work_picks_oldest_active()
    test_pause_skips()
    test_retire_archives()
    test_malformed_block_skipped()
    test_run_tool_actions()
    test_rewrites_keep_malformed_blocks()
    test_invalid_values_skipped()
    test_react_wiring()
    test_tool_dispatch_survives_bad_input()
    test_next_action_is_capped()

    if _failures:
        print(f"\n{len(_failures)} FAILED: {', '.join(_failures)}")
        sys.exit(1)
    print("\nall pass")
