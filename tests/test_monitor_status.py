"""Tests for the status block Monitor answers "is X running" questions from.

Run: python tests/test_monitor_status.py   (repo root, stdlib only, no network)

2026-10-09: asked "Is the Aoostar AG01 price scouting running as it should?", IGOR
replied with the scheduler entry and a raw timestamp - "price_watch - next run
scheduled for 2026-10-09 05:56:38.282462+00:00". True, and useless. "As it should"
asks whether the watch is doing its job, and Monitor's status block held only the
job list and the news watchlist, so a cron entry was the most it could say.

The watch's own state already existed in prices_state.json. Monitor just could not
see it.
"""

import json
import sys
import tempfile
from datetime import datetime, timedelta, timezone
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


NOW = datetime(2026, 10, 9, 6, 16, tzinfo=timezone.utc)

WATCHLIST = """# Price Watch

## AOOSTAR AG01 eGPU dock
query: aoostar ag01
want: An AOOSTAR AG01 with the 800W supply built in.
under: 200

## OCuLink eGPU dock, any brand
query: oculink dock
under: 150
"""

STATE = {
    "last_checked": "2026-10-09T05:56:39.658000+00:00",
    "closest": {
        "AOOSTAR AG01 eGPU dock": {
            "price": 179.0, "under": 200.0,
            "title": "AOOSTAR AG01 OCulink eGPU Dock with 800W Built-In PSU @ $179",
            "url": "https://slickdeals.net/f/18243481",
        },
        "OCuLink eGPU dock, any brand": {
            "price": 44.0, "under": 150.0,
            "title": "Minisforum DEG1 eGPU Oculink Dock (Refurbished) $44",
            "url": "https://slickdeals.net/f/18417907",
        },
    },
}


def _fresh():
    config.MEMORY_DIR = Path(tempfile.mkdtemp())
    (config.MEMORY_DIR / "price_watch.md").write_text(WATCHLIST, encoding="utf-8")
    (config.MEMORY_DIR / "prices_state.json").write_text(
        json.dumps(STATE), encoding="utf-8")
    return config.MEMORY_DIR


def test_the_price_watch_can_answer_for_itself() -> None:
    from agents import monitor

    _fresh()
    status = monitor._watcher_status(now=NOW)

    _check("it names the item the user asks about",
           "AOOSTAR AG01" in status, status)
    _check("it gives the target that item is judged against",
           "200" in status, status)
    _check("it says when the watch last actually ran",
           "05:56" in status, status)
    _check("it says how many items are being watched",
           "2" in status, status)
    _check("it reports the closest price seen",
           "179" in status, status)

    # The $44 is a refurbished DEG1, not an AG01. The status file this mirrors carries
    # the same warning, because without it IGOR quotes the figure as the item's price.
    _check("it marks the closest figure as not product-checked",
           "not verified" in status.lower() or "not checked" in status.lower(), status)

    _check("it is compact enough to ride on every Monitor call",
           len(status) < 700, f"{len(status)} chars")


def test_nothing_configured_is_not_a_crash() -> None:
    from agents import monitor

    config.MEMORY_DIR = Path(tempfile.mkdtemp())
    status = monitor._watcher_status(now=NOW)
    _check("an empty watchlist still returns a line rather than raising",
           isinstance(status, str), repr(status))
    _check("and says nothing is watched",
           "no" in status.lower() or "none" in status.lower(), status)

    (config.MEMORY_DIR / "price_watch.md").write_text(WATCHLIST, encoding="utf-8")
    (config.MEMORY_DIR / "prices_state.json").write_text("{not json", encoding="utf-8")
    status = monitor._watcher_status(now=NOW)
    _check("an unreadable state file still names what is watched",
           "AOOSTAR AG01" in status, status)
    _check("and does not invent a price for it",
           "179" not in status, status)


def test_a_stale_watch_is_visible() -> None:
    """"Running as it should" is false if the last check was days ago, and a job
    registered in the scheduler looks identical either way."""
    from agents import monitor

    path = _fresh()
    stale = dict(STATE)
    stale["last_checked"] = (NOW - timedelta(days=3)).isoformat()
    (path / "prices_state.json").write_text(json.dumps(stale), encoding="utf-8")

    status = monitor._watcher_status(now=NOW)
    _check("a check 3 days old is called out, not just printed",
           "stale" in status.lower() or "3 days" in status.lower()
           or "72 hours" in status.lower(), status)


def test_the_job_list_is_readable() -> None:
    """The raw value was "next run 2026-10-09 05:56:38.282462+00:00". Microseconds
    are not information and UTC is not the timezone the user lives in."""
    from agents import monitor

    when = datetime(2026, 10, 9, 5, 56, 38, 282462, tzinfo=timezone.utc)
    line = monitor._job_line("price_watch", when)
    _check("the job id is there", "price_watch" in line, line)
    _check("microseconds are gone", "282462" not in line, line)
    _check("the time is still there", "05:56" in line, line)
    _check("a job with no next run says so rather than printing None",
           "none" in monitor._job_line("x", None).lower()
           or "not scheduled" in monitor._job_line("x", None).lower(),
           monitor._job_line("x", None))


if __name__ == "__main__":
    print("the price watch answering for itself")
    test_the_price_watch_can_answer_for_itself()
    print("\nnothing configured")
    test_nothing_configured_is_not_a_crash()
    print("\na stale watch")
    test_a_stale_watch_is_visible()
    print("\nthe job list")
    test_the_job_list_is_readable()

    if _failures:
        print(f"\n{len(_failures)} FAILED: {', '.join(_failures)}")
        sys.exit(1)
    print("\nall pass")
