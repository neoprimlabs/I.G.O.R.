"""The token ledger (usage.py) and run-together clock times (orchestrator).

Run: python tests/test_usage.py   (repo root, no API calls)

2026-10-16: the daily cap on gpt-oss-120b ran out and nothing in IGOR could have
said it was close - Groq shows the cap only in a 429 body. The same day, "100am"
was read as 10:00 in three live runs out of three.
"""

import sys
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import config

_failures: list[str] = []


def _check(name: str, condition: bool, detail: str = "") -> None:
    if condition:
        print(f"  pass  {name}")
    else:
        print(f"  FAIL  {name}  {detail}")
        _failures.append(name)


def _response(total: int, prompt: int = 0, completion: int = 0):
    return SimpleNamespace(usage=SimpleNamespace(total_tokens=total, prompt_tokens=prompt,
                                                 completion_tokens=completion))


BIG = "openai/gpt-oss-120b"
SMALL = "openai/gpt-oss-20b"
NOW = datetime(2026, 10, 16, 15, 30, tzinfo=timezone.utc)


def test_ledger() -> None:
    import usage

    config.MEMORY_DIR = Path(tempfile.mkdtemp())
    _check("an empty ledger says so", usage.summary(NOW).endswith("none recorded."), usage.summary(NOW))
    _check("full headroom on a measured model", usage.headroom(BIG, NOW) == 200000)
    _check("no headroom figure for an unmeasured model", usage.headroom(SMALL, NOW) is None)

    usage.record(BIG, _response(3500, 3000, 500), "React", now=NOW)
    usage.record(BIG, _response(1500), "Chat", now=NOW - timedelta(hours=3))
    usage.record(SMALL, _response(800), "Compose", now=NOW)
    usage.record(BIG, _response(9999), "React", now=NOW - timedelta(hours=26))

    spent = usage.last_24h(NOW)
    _check("use is summed per model and per agent",
           spent.get(BIG) == {"React": 3500, "Chat": 1500}, str(spent))
    _check("use older than 24 hours no longer counts", sum(spent[BIG].values()) == 5000, str(spent))
    _check("headroom counts this hour's spend whole, and what was spent 3 hours ago has refilled",
           usage.headroom(BIG, NOW) == 196500, str(usage.headroom(BIG, NOW)))

    text = usage.summary(NOW)
    _check("the summary gives what was spent and what is free", "5,000, about 196,000 of the 200,000 daily cap free" in text, text)
    _check("and says so where it is not", "800 (daily cap not measured)" in text, text)
    _check("and never invents a cap", "200,000" not in text.split(SMALL)[1], text)

    later = NOW + timedelta(hours=30)
    usage.record(BIG, _response(10), "React", now=later)
    stored = (config.MEMORY_DIR / "token_ledger.json").read_text(encoding="utf-8")
    _check("old hours are pruned from the file, so it stays small", "2026-10-16T15" not in stored, stored[:200])


def test_the_cap_refills() -> None:
    """Hand worked, at 200000/24 = 8333.33 an hour."""
    import usage
    # The real 429 bodies this rests on, from the server's log: over by this much, waits this long.
    _check("a daily 429 waits exactly what a steady refill says",
           abs((195831 + 4937 - 200000) / 331.776 - 200000 / 86400) < 0.001)
    _check("and so does a per-minute one", abs((4905 + 6843 - 8000) / 28.11 - 8000 / 60) < 0.01)

    config.MEMORY_DIR = Path(tempfile.mkdtemp())
    usage.record(BIG, _response(100000), "React", now=NOW - timedelta(hours=6))
    _check("six hours give back 50000 of 100000", usage.headroom(BIG, NOW) == 150000, str(usage.headroom(BIG, NOW)))
    _check("a day later it has all come back", usage.headroom(BIG, NOW + timedelta(hours=18)) == 200000)

    config.MEMORY_DIR = Path(tempfile.mkdtemp())
    usage.record(BIG, _response(120000), "Isolated run", now=NOW - timedelta(hours=6))
    usage.record(BIG, _response(120000), "React", now=NOW - timedelta(hours=1))
    # 120000 less 5 hours is 78333, plus 120000 is 198333, less an hour is 190000.
    _check("more than the cap in a day can still leave room, as Groq allowed live",
           sum(usage.last_24h(NOW)[BIG].values()) == 240000 and usage.headroom(BIG, NOW) == 10000,
           str(usage.headroom(BIG, NOW)))

    config.MEMORY_DIR = Path(tempfile.mkdtemp())
    usage.record(BIG, _response(500000), "React", now=NOW)
    _check("it never reads below nothing", usage.headroom(BIG, NOW) == 0, str(usage.headroom(BIG, NOW)))
    (config.MEMORY_DIR / "token_ledger.json").write_text('{"2026-10-16T15": "not a row"}', encoding="utf-8")
    _check("a bad row reads as no spend", usage.headroom(BIG, NOW) == 200000, str(usage.headroom(BIG, NOW)))


def test_ledger_never_breaks_a_reply() -> None:
    import usage

    config.MEMORY_DIR = Path(tempfile.mkdtemp())
    raised = False
    try:
        usage.record(BIG, SimpleNamespace(), "React", now=NOW)
        usage.record(BIG, None, "React", now=NOW)
        (config.MEMORY_DIR / "token_ledger.json").write_text("{not json", encoding="utf-8")
        usage.record(BIG, _response(100), "React", now=NOW)
        config.MEMORY_DIR = Path(tempfile.mkdtemp()) / "missing" / "dir"
        usage.record(BIG, _response(100), "React", now=NOW)
    except Exception as e:
        raised = True
        print("   raised:", type(e).__name__, e)
    _check("no usage, no response, a corrupt file or a missing folder never raises", not raised)


def test_a_week_per_agent() -> None:
    """The dashboard's agent cards: calls and tokens per label on each of the user's last seven days."""
    import usage

    config.MEMORY_DIR = Path(tempfile.mkdtemp())
    usage.record(BIG, _response(50), "Chat", now=NOW - timedelta(days=9))
    usage.record(BIG, _response(900), "Chat", now=NOW - timedelta(days=2))
    usage.record(BIG, _response(200), "Router", now=NOW - timedelta(minutes=5))
    usage.record(BIG, _response(300), "Router", now=NOW)
    usage.record(BIG, _response(0), "Chat", now=NOW)
    week = usage.week(NOW)
    _check("seven days, today last", len(week["days"]) == 7 and week["days"][-1] == "2026-10-16", str(week["days"]))
    router = week["labels"].get("Router", {})
    _check("two calls today, with their tokens", router.get("calls") == [0] * 6 + [2] and router["tokens"][-1] == 500, str(router))
    _check("and when the newest was", router.get("last") == NOW.isoformat(timespec="seconds"), str(router.get("last")))
    chat = week["labels"].get("Chat", {})
    _check("a call two days back is on its day, and one with no usage is no call",
           chat.get("calls") == [0, 0, 0, 0, 1, 0, 0] and chat["tokens"][4] == 900, str(chat))
    stored = (config.MEMORY_DIR / "agent_activity.json").read_text(encoding="utf-8")
    _check("a day past the eighth is pruned", "2026-10-07" not in stored and "2026-10-14" in stored, stored[:200])
    (config.MEMORY_DIR / "agent_activity.json").write_text("{not json", encoding="utf-8")
    usage.record(BIG, _response(10), "Router", now=NOW)
    _check("a corrupt file starts again rather than failing", usage.week(NOW)["labels"]["Router"]["calls"][-1] == 1)


def test_spelled_out_times() -> None:
    import orchestrator

    cases = [
        ("could you message me around 100am with something", "could you message me around 1:00am with something"),
        ("at 1030pm please", "at 10:30pm please"),
        ("the 930 AM call", "the 9:30 AM call"),
        ("1230a.m. works", "12:30a.m. works"),
        ("make it 1145 p.m.", "make it 11:45 p.m."),
    ]
    for given, wanted in cases:
        got = orchestrator._spell_out_times(given)
        _check(f"{given!r} becomes {wanted!r}", got == wanted, got)

    untouched = [
        "I said 1am",
        "at 10:30pm",
        "2100am is not a time",
        "1260pm is not a time",
        "100 amps on that circuit",
        "send 100 messages",
        "room 101 at noon",
    ]
    for text in untouched:
        _check(f"{text!r} is left exactly as typed", orchestrator._spell_out_times(text) == text,
               orchestrator._spell_out_times(text))


if __name__ == "__main__":
    print("ledger")
    test_ledger()
    test_the_cap_refills()
    test_ledger_never_breaks_a_reply()
    test_a_week_per_agent()
    print("\nrun-together times")
    test_spelled_out_times()
    if _failures:
        print(f"\n{len(_failures)} FAILED: {', '.join(_failures)}")
        sys.exit(1)
    print("\nall pass")
