"""Tests for watchers.py - the seen-store and the proof of life.

Run: python tests/test_watchers.py   (repo root, stdlib only, no network)

Three features had three copies of the same seen-store: AI news, job postings, price
drops. The third went in without noticing the second. This is the one copy.

The heartbeat is the half that matters. A watcher that only speaks when it has
something is indistinguishable from one that has died - the job watch sent nothing
for five days and nothing said so.
"""

import json
import sys
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import config
import watchers

_failures: list[str] = []


def _check(name: str, condition: bool, detail: str = "") -> None:
    if condition:
        print(f"  pass  {name}")
    else:
        print(f"  FAIL  {name}  {detail}")
        _failures.append(name)


NOW = datetime(2026, 10, 7, 12, 0, tzinfo=timezone.utc)
STORE = "test_seen.json"


def _fresh():
    config.MEMORY_DIR = Path(tempfile.mkdtemp())
    return config.MEMORY_DIR


def test_seen_store() -> None:
    _fresh()
    _check("an absent store reads empty", watchers.load_seen(STORE) == {})

    watchers.remember(STORE, ["https://a", "https://b"], now=NOW)
    seen = watchers.load_seen(STORE)
    _check("what was reported is remembered", set(seen) == {"https://a", "https://b"}, str(seen))

    watchers.remember(STORE, ["https://c"], now=NOW + timedelta(days=1))
    _check("later writes keep the earlier ones", len(watchers.load_seen(STORE)) == 3)

    watchers.remember(STORE, [], now=NOW + timedelta(days=40))
    _check("everything past the window is pruned", watchers.load_seen(STORE) == {},
           str(watchers.load_seen(STORE)))

    watchers.remember(STORE, ["https://d"], now=NOW, days=90)
    watchers.remember(STORE, [], now=NOW + timedelta(days=40), days=90)
    _check("a longer window keeps it", "https://d" in watchers.load_seen(STORE))

    _fresh()
    watchers.remember(STORE, ["https://a", "", None], now=NOW)
    _check("empty urls are not stored", list(watchers.load_seen(STORE)) == ["https://a"],
           str(watchers.load_seen(STORE)))


def test_corrupt_store() -> None:
    path = _fresh()
    (path / STORE).write_text("{not json", encoding="utf-8")
    _check("unreadable reads as empty", watchers.load_seen(STORE) == {})
    watchers.remember(STORE, ["https://a"], now=NOW)
    _check("and is rewritten cleanly", "https://a" in watchers.load_seen(STORE))

    (path / STORE).write_text(json.dumps({"https://a": "not-a-date"}), encoding="utf-8")
    watchers.remember(STORE, ["https://b"], now=NOW)
    _check("an unparseable timestamp is dropped, not fatal",
           list(watchers.load_seen(STORE)) == ["https://b"], str(watchers.load_seen(STORE)))


def test_heartbeat() -> None:
    _fresh()
    _check("the first one is always due", watchers.heartbeat_due(STORE, now=NOW))

    watchers.heartbeat_sent(STORE, now=NOW)
    _check("not due again the next day",
           not watchers.heartbeat_due(STORE, now=NOW + timedelta(days=1)))
    _check("due again a week later",
           watchers.heartbeat_due(STORE, now=NOW + timedelta(days=8)))

    _check("the window is adjustable",
           watchers.heartbeat_due(STORE, now=NOW + timedelta(days=2), days=1))

    path = _fresh()
    (path / STORE).write_text(json.dumps({"last_heartbeat": "nonsense"}), encoding="utf-8")
    _check("an unreadable timestamp means due rather than never",
           watchers.heartbeat_due(STORE, now=NOW))


def test_heartbeat_and_seen_share_a_file_safely() -> None:
    """Both live in one store per watcher, so one must not erase the other."""
    _fresh()
    watchers.remember(STORE, ["https://a"], now=NOW)
    watchers.heartbeat_sent(STORE, now=NOW)
    _check("the heartbeat did not drop the seen url", "https://a" in watchers.load_seen(STORE),
           str(watchers.load_seen(STORE)))
    _check("and the heartbeat survives a later remember",
           (watchers.remember(STORE, ["https://b"], now=NOW) or True)
           and not watchers.heartbeat_due(STORE, now=NOW + timedelta(days=1)),
           str(watchers.load_seen(STORE)))
    _check("the heartbeat never comes back as a seen url",
           "last_heartbeat" not in watchers.load_seen(STORE), str(watchers.load_seen(STORE)))


if __name__ == "__main__":
    print("the seen store")
    test_seen_store()
    print("\ncorrupt input")
    test_corrupt_store()
    print("\nthe heartbeat")
    test_heartbeat()
    print("\nsharing one file")
    test_heartbeat_and_seen_share_a_file_safely()

    if _failures:
        print(f"\n{len(_failures)} FAILED: {', '.join(_failures)}")
        sys.exit(1)
    print("\nall pass")
