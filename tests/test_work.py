"""Tests for the overnight work folder, night log and budget: work.py.

Run: python tests/test_work.py   (repo root, stdlib only, no API calls)
"""

import sys
import tempfile
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import clock
import config

_failures: list[str] = []


def _check(name: str, condition: bool, detail: str = "") -> None:
    if condition:
        print(f"  pass  {name}")
    else:
        print(f"  FAIL  {name}  {detail}")
        _failures.append(name)


def _local(*args) -> datetime:
    return datetime(*args, tzinfo=clock.USER_TZ)


def _fresh():
    import work
    tmp = tempfile.TemporaryDirectory()
    config.MEMORY_DIR = Path(tmp.name)
    return work, tmp


def _log(work, goal="g1", tokens=100, searches=1, stopped="", now=None):
    work.log_session(goal, "research", "s", tokens, searches, ["a.md"], [], stopped, now=now)


def test_folder_cannot_escape() -> None:
    work, tmp = _fresh()
    with tmp:
        root = (config.MEMORY_DIR / "work").resolve()
        for bad in ("../x", "a/b", "..\\x", "G1"):
            p = work.folder(bad).resolve()
            _check(f"{bad!r} stays inside work/", p.parent == root, str(p))
        _check("id is lowercased", work.folder("G1").name == "g1", "")
        for empty in ("", "...", "/"):
            try:
                work.folder(empty)
                _check(f"{empty!r} raises", False, "no error")
            except ValueError:
                _check(f"{empty!r} raises", True)


def test_night_of_and_window() -> None:
    work, _ = _fresh()
    sat_0 = _local(2026, 10, 17, 0, 0)
    sat_659 = _local(2026, 10, 17, 6, 59)
    _check("Sat 06:59 in window", work.in_window(sat_659), "")
    _check("same night as Sat 00:00",
           work.night_of(sat_659) == work.night_of(sat_0) == "2026-10-17", "")
    _check("Sat 07:00 out", not work.in_window(_local(2026, 10, 17, 7, 0)), "")
    _check("Fri 23:59 out", not work.in_window(_local(2026, 10, 16, 23, 59)), "")
    _check("Sat 00:00 in", work.in_window(sat_0), "")


def test_log_and_spent() -> None:
    work, tmp = _fresh()
    with tmp:
        _check("empty night is zero", work.spent("2026-10-17") == (0, 0), "")
        _log(work, tokens=100, searches=2, now=_local(2026, 10, 17, 1, 0))
        _log(work, tokens=50, searches=3, now=_local(2026, 10, 17, 3, 0))
        _log(work, tokens=999, searches=9, now=_local(2026, 10, 18, 1, 0))
        _check("sums that night only", work.spent("2026-10-17") == (150, 5),
               str(work.spent("2026-10-17")))
        _check("other night separate", work.spent("2026-10-18") == (999, 9), "")
        (config.MEMORY_DIR / "work" / "log.json").write_text("{not json", encoding="utf-8")
        _check("unreadable log is empty", work.spent("2026-10-17") == (0, 0), "")
        _log(work, tokens=7, now=_local(2026, 10, 17, 2, 0))
        _check("log recovers after corruption", work.spent("2026-10-17") == (7, 1), "")


def test_log_is_bounded() -> None:
    import json
    work, tmp = _fresh()
    with tmp:
        for i in range(301):
            _log(work, tokens=i, now=_local(2026, 10, 17, 1, 0))
        entries = json.loads((config.MEMORY_DIR / "work" / "log.json").read_text(encoding="utf-8"))
        _check("bounded to 300", len(entries) == 300, str(len(entries)))
        _check("oldest dropped", entries[0]["tokens"] == 1, str(entries[0]))


def test_stopped_reason() -> None:
    work, tmp = _fresh()
    with tmp:
        _check("no sessions, no reason", work.stopped("2026-10-17") == "", "")
        _log(work, now=_local(2026, 10, 17, 1, 0))
        _check("no reason set", work.stopped("2026-10-17") == "", "")
        _log(work, stopped="budget", now=_local(2026, 10, 17, 2, 0))
        _check("reason returned", work.stopped("2026-10-17") == "budget", "")
        _check("other night unaffected", work.stopped("2026-10-18") == "", "")


def test_overnight_tokens() -> None:
    import usage
    work, _ = _fresh()
    real = usage.last_24h
    try:
        usage.last_24h = lambda now=None: {"openai/gpt-oss-20b": {"Overnight": 42, "x": 1}}
        _check("reads Overnight label", work.overnight_tokens() == 42, "")
        usage.last_24h = lambda now=None: {}
        _check("missing is zero", work.overnight_tokens() == 0, "")
    finally:
        usage.last_24h = real


if __name__ == "__main__":
    test_folder_cannot_escape()
    test_night_of_and_window()
    test_log_and_spent()
    test_log_is_bounded()
    test_stopped_reason()
    test_overnight_tokens()

    if _failures:
        print(f"\n{len(_failures)} FAILED: {', '.join(_failures)}")
        sys.exit(1)
    print("\nall pass")
