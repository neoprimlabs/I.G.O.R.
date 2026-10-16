"""Live check of scheduling corrections through the real router and React.

Run on the server, as igor (spends real tokens from the daily cap):

    sudo -u igor /opt/igor/venv/bin/python tests/live_scheduling.py

Not named test_*: it calls Groq, so the offline loop must never pick it up.

Isolated: history in memory only, scheduled store in a temp file, no corrections
log, no critic. Nothing reaches context.db, scheduled.json or Discord. Token use
does reach the ledger, which is the point - it is real spend.

Seven user turns: four that failed live on 2026-10-14 or -16, and three for
search_history and repeating messages, which had never run live:
  - "around 100am" was scheduled for 10:00
  - "draft something new at the scheduled time" added a second entry
  - "actually make it 11:30" cancelled and re-added rather than using update
The first full version of this ran 15 turns and helped exhaust the 200000-token
daily cap on gpt-oss-120b. Keep it this small.
"""

import asyncio
import json
import logging
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import config
import context_store
import orchestrator
import usage
from agents import react, scheduled

_MODEL = config.MODELS["react"]
_NEEDS = 40000  # four React turns with headroom; measured cost is printed at the end

logging.basicConfig(level=logging.INFO, format="      log: %(name)s: %(message)s")
for noisy in ("httpx", "openai", "httpcore", "apscheduler", "usage"):
    logging.getLogger(noisy).setLevel(logging.WARNING)

store = Path(tempfile.mkdtemp()) / "scheduled.json"
scheduled._path = lambda: store
context_store.append = lambda *a, **k: None
context_store.load = lambda *a, **k: []
context_store.last_timestamp = lambda: None
orchestrator.Orchestrator._log_correction = lambda *a, **k: None
config.ENABLE_CRITIC = False

_failures: list[str] = []


def _check(name: str, condition: bool, detail: str = "") -> None:
    print(f"   {'pass' if condition else 'FAIL'}  {name}" + ("" if condition else f"  {detail}"))
    if not condition:
        _failures.append(name)


def _entries() -> list[dict]:
    return json.loads(store.read_text()) if store.exists() else []


def _local_hm(entry: dict) -> str:
    return scheduled._local(entry["due"]).split()[2]


async def _turns(o, messages: list[str]) -> None:
    for msg in messages:
        print(f"\n>> USER: {msg}")
        reply, _ = await o.process(config.AUTHORIZED_USER_ID, msg)
        print(f"<< IGOR: {reply}")
        for e in _entries():
            print(f"   stored {e['id']}  {scheduled._local(e['due'])}  {(e.get('brief') or '')[:70]!r}")


async def main() -> int:
    left = usage.headroom(_MODEL)
    if left is not None and left < _NEEDS:
        print(f"Not running: the ledger shows {left:,} tokens left today on {_MODEL}, "
              f"under the {_NEEDS:,} this needs. It only counts spend since the ledger "
              "existed, so the true figure may be lower still.")
        return 2
    before = sum(usage.last_24h().get(_MODEL, {}).values())

    async def notify(_):
        pass

    print("===== the 10-14 request, then 'draft something new' =====")
    o = orchestrator.Orchestrator(notify)
    o._context = []
    await _turns(o, [
        "Ok thanks could you message me around 100am with some thought provoking material?",
        "Not too bad. You could go deeper than that. Just be sure to draft something new at the scheduled time.",
    ])
    entries = _entries()
    _check("one message, not two", len(entries) == 1, str(len(entries)))
    _check("at 1:00, not 10:00", bool(entries) and _local_hm(entries[0]) == "01:00",
           entries and _local_hm(entries[0]))

    print("\n===== a correction =====")
    store.unlink(missing_ok=True)
    o = orchestrator.Orchestrator(notify)
    o._context = []
    await _turns(o, ["Message me at 11pm with something thought-provoking"])
    first = [e["id"] for e in _entries()]
    await _turns(o, ["actually make it 11:30"])
    entries = _entries()
    _check("one message after the correction", len(entries) == 1, str(len(entries)))
    _check("now at 23:30", bool(entries) and _local_hm(entries[0]) == "23:30",
           entries and _local_hm(entries[0]))
    _check("same entry, so update was used rather than cancel-and-add",
           [e["id"] for e in entries] == first, f"{first} -> {[e['id'] for e in entries]}")

    print("\n===== search_history and a repeating message =====")
    searched = []
    real_search = react._search_history
    react._search_history = lambda q: searched.append(q) or real_search(q)
    store.unlink(missing_ok=True)
    o = orchestrator.Orchestrator(notify)
    o._context = []
    try:
        await _turns(o, ["When did I ask you to message me around 100am? Check our past conversations."])
        _check("React searched the stored conversation", bool(searched), str(searched))
        await _turns(o, ["Every weekday at 9am send me one short, practical idea for improving IGOR",
                         "pause that one for now"])
    finally:
        react._search_history = real_search
    entries = _entries()
    _check("one repeating entry", len(entries) == 1 and entries[0].get("repeat") == "weekdays",
           str(entries))
    _check("at 09:00", bool(entries) and _local_hm(entries[0]) == "09:00", entries and _local_hm(entries[0]))
    _check("and paused, not cancelled", bool(entries) and entries[0].get("paused") is True, str(entries))

    import llm
    benched = {m: round(until - __import__("time").time()) for m, (until, _) in llm._benched.items()}
    print(f"\nFallback during this run: {'yes, benched ' + str(benched) if benched else 'no'}")
    spent = sum(usage.last_24h().get(_MODEL, {}).values()) - before
    print(f"\nSpent {spent:,} tokens on {_MODEL}. Headroom now: {usage.headroom(_MODEL)}")
    print("\nall pass" if not _failures else f"\n{len(_failures)} FAILED: {', '.join(_failures)}")
    return 1 if _failures else 0


sys.exit(asyncio.run(main()))
