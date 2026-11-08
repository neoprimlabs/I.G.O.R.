"""Rehearse what IGOR sends unprompted: today's check-in, or the next run of a repeating scheduled
message, with today's code and the real models on a throwaway copy of memory.

2026-11-06: reading two weeks of unprompted posts found a check-in asking after the same watch three
times in four days and weekday ideas returning eight days apart. Each had passed its tests on fakes.

Spends real tokens: a check-in is one or two presence calls on 120b, about 2200 each; a series run is
one writer run on 120b, about 5500, more if it uses tools. The spend is added to the real token ledger.
Nothing is sent and the real memory is not touched.

On the server, as igor:
    cd /opt/igor && venv/bin/python scripts/rehearse_posts.py checkin [--dry-run]
    cd /opt/igor && venv/bin/python scripts/rehearse_posts.py series <id> [YYYY-MM-DD] [--dry-run]
A series run is dated its next due day unless a date is given, which picks the subject an IGOR series
rotates to. --dry-run shows what the run would be given and stops before any model call.
"""
import asyncio
import json
import logging
import shutil
import sys
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

SOURCE = Path(__file__).resolve().parent.parent


def _copy_memory(real: Path, mem: Path) -> None:
    """The top-level files: notes, state, the conversation store and the ledger. Not private/ or work/."""
    mem.mkdir()
    for p in real.iterdir():
        if p.is_file() and not p.name.endswith((".tmp", ".bak")) and ".bak-" not in p.name:
            shutil.copy2(p, mem / p.name)


def _checkin(dry: bool) -> None:
    import clock
    import context_store
    from agents import presence
    now = datetime.now(timezone.utc)
    last_user_at = context_store.last_user_timestamp()
    if last_user_at is not None and now - last_user_at < timedelta(hours=2):
        # The gates would hold it back, which is not what is being rehearsed.
        last_user_at = now - timedelta(hours=2)
        print("(the user spoke within two hours; rehearsed as if two hours ago)")
    state = presence._today(presence._load_state(), now)
    state.update({"sent": 0, "calls": 0, "last_sent": None})
    state["checkin"] = {"day": now.astimezone(clock.USER_TZ).strftime("%Y-%m-%d"),
                        "at": now.isoformat(), "sent": False}
    presence._save_state(state)
    print("last check-ins:")
    for text in state.get("checkins", []):
        print(f"  - {text}")
    if dry:
        print("\n" + presence._facts("checkin", now, last_user_at, state.get("checkins")))
        return
    message = asyncio.run(presence.consider("checkin", now=now, last_user_at=last_user_at))
    saved = presence._load_state()
    print(f"\ncheck-in: {message!r}")
    print(f"calls: {saved.get('calls')}, day closed: {saved.get('checkin', {}).get('skipped', 'no')}")


def _series(entry_id: str, day: str, dry: bool) -> None:
    import clock
    from agents import compose, scheduled
    entry = next((e for e in scheduled._load() if e["id"] == entry_id), None)
    if entry is None or not entry.get("repeat") or not entry.get("brief"):
        sys.exit(f"no repeating message with a brief has id {entry_id}")
    due = datetime.fromisoformat(entry["due"])
    if day:
        local = due.astimezone(clock.USER_TZ)
        due = local.replace(year=int(day[:4]), month=int(day[5:7]), day=int(day[8:10])).astimezone(timezone.utc)
    recent = (entry.get("recent") or []) + (entry.get("earlier") or [])
    print(f"brief: {entry['brief']}\ndated: {due.astimezone(clock.USER_TZ):%a %Y-%m-%d %H:%M}")
    if compose._ABOUT_IGOR.search(entry["brief"]):
        print(f"subject: {compose._IGOR_AREAS[due.toordinal() % len(compose._IGOR_AREAS)]}")
    print(f"shown {len(entry.get('recent') or [])} in full and {len(entry.get('earlier') or [])} as one line:")
    for text in recent:
        print(f"  - {' '.join(text.split())[:150]}")
    if dry:
        return
    text = asyncio.run(compose.write(entry["brief"], due, recent=recent)) or ""
    print(f"\nwritten, {len(text.split())} words:\n\n{text}")


def main(argv: list[str]) -> None:
    sys.path.insert(0, str(SOURCE))
    sys.path.insert(0, str(SOURCE / "scripts"))
    logging.basicConfig(level=logging.INFO, format="  log: %(name)s: %(message)s")
    for noisy in ("httpx", "openai", "httpcore", "apscheduler"):
        logging.getLogger(noisy).setLevel(logging.WARNING)
    args = [a for a in argv if not a.startswith("--")]
    dry = "--dry-run" in argv
    if not args or args[0] not in ("checkin", "series") or (args[0] == "series" and len(args) < 2):
        sys.exit(__doc__)
    import config
    import context_store

    real = config.MEMORY_DIR
    tmp = Path(tempfile.mkdtemp(prefix="igor-rehearse-"))
    try:
        mem = tmp / "memory"
        _copy_memory(real, mem)
        config.MEMORY_DIR = mem
        context_store._DB_PATH = mem / "context.db"
        import e2e_isolated
        before = json.loads((mem / "token_ledger.json").read_text(encoding="utf-8"))
        try:
            if args[0] == "checkin":
                _checkin(dry)
            else:
                _series(args[1], args[2] if len(args) > 2 else "", dry)
        finally:
            e2e_isolated._count_in_the_real_ledger(
                before, json.loads((mem / "token_ledger.json").read_text(encoding="utf-8")))
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    main(sys.argv[1:])
