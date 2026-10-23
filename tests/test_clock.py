"""The prompt's calendar names real dates, so no model counts weekdays.

2026-10-22 and 10-23: Direct and React both offered "Monday Oct 28", a Wednesday.
Run: python tests/test_clock.py
"""
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import clock

_failures: list[str] = []


def _check(name: str, condition: bool, detail: str = "") -> None:
    print(f"  {'pass' if condition else 'FAIL'}  {name}" + ("" if condition else f"  {detail}"))
    if not condition:
        _failures.append(name)


def test_week_line() -> None:
    # Fri 2026-10-23 13:21 in New York.
    line = clock.week_line(datetime(2026, 10, 23, 17, 21, tzinfo=timezone.utc))
    _check("Monday is the 26th", "Mon 2026-10-26" in line, line)
    _check("and the 28th is a Wednesday", "Wed 2026-10-28" in line, line)
    _check("it starts tomorrow and runs seven days",
           line.count(", ") == 6 and "Sat 2026-10-24" in line and "Fri 2026-10-30" in line, line)
    # 03:30 UTC on Saturday is still Friday evening in New York.
    late = clock.week_line(datetime(2026, 10, 24, 3, 30, tzinfo=timezone.utc))
    _check("days count from the user's date, not UTC's", "Sat 2026-10-24" in late, late)


if __name__ == "__main__":
    test_week_line()
    if _failures:
        print(f"\n{len(_failures)} FAILED: {', '.join(_failures)}")
        sys.exit(1)
    print("\nall pass")
