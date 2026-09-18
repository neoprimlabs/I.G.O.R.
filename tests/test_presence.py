"""Tests for agents/presence.py - IGOR deciding on its own whether to speak.

Run: python tests/test_presence.py   (repo root, stdlib only, no API calls)

The gates get the most coverage here on purpose. ProactiveBench evaluated 22 models
and found the measured failure of proactive agents is not staying silent when nothing
is needed, and that prompting for restraint "yields only marginal gains". So every
condition that can be decided without a model is decided without one, and these tests
assert that no model call happens when a gate should stop it.

Every expected value is worked out by hand. The fake model records what it was asked.
"""

import asyncio
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


def _utc(*args) -> datetime:
    return datetime(*args, tzinfo=timezone.utc)


# 18:00 UTC is 14:00 EDT: mid afternoon, well inside waking hours.
AWAKE = _utc(2026, 9, 16, 18, 0)


class _Model:
    """Stands in for the one model call. Records the bundle it was given."""

    def __init__(self, reply="Something worth saying."):
        self.asked: list[str] = []
        self._reply = reply

    async def __call__(self, bundle: str) -> str:
        self.asked.append(bundle)
        if isinstance(self._reply, Exception):
            raise self._reply
        return self._reply


def _fresh(state=None, on=True, files=None):
    """A clean memory dir. Returns its path."""
    config.MEMORY_DIR = Path(tempfile.mkdtemp())
    (config.MEMORY_DIR / "presence_config.md").write_text(
        f"# Presence\n\nstate: {'on' if on else 'off'}\n", encoding="utf-8")
    if state is not None:
        (config.MEMORY_DIR / "presence_state.json").write_text(json.dumps(state), encoding="utf-8")
    for name, body in (files or {}).items():
        (config.MEMORY_DIR / name).write_text(body, encoding="utf-8")
    return config.MEMORY_DIR


def _state(**kw):
    base = {"day": "2026-09-16", "sent": 0, "calls": 0, "last_sent": None,
            "last_trigger": {}, "last_digest": None}
    base.update(kw)
    return base


async def test_gates_run_before_the_model() -> None:
    from agents import presence

    quiet_cases = [
        ("3am local is inside the quiet window", _utc(2026, 9, 16, 7, 0)),
        ("just before 10am local is still quiet", _utc(2026, 9, 16, 13, 59)),
        ("9:30am in November is quiet too, EST not EDT", _utc(2026, 11, 16, 14, 30)),
    ]
    for name, when in quiet_cases:
        _fresh(_state(day=when.strftime("%Y-%m-%d")))
        model = _Model()
        out = await presence.consider("lull", now=when, ask=model)
        _check(name, out is None and model.asked == [], f"{out!r} asked={len(model.asked)}")

    _fresh(_state())
    model = _Model()
    out = await presence.consider("lull", now=_utc(2026, 9, 16, 14, 0), ask=model)
    _check("10am local exactly is allowed", out is not None and len(model.asked) == 1,
           f"{out!r} asked={len(model.asked)}")

    _fresh(_state(), on=False)
    model = _Model()
    out = await presence.consider("lull", now=AWAKE, ask=model)
    _check("switched off means no message and no model call",
           out is None and model.asked == [], f"{out!r} asked={len(model.asked)}")

    # Fail closed. An unproven thing that messages the user on its own should not
    # start talking because a config file went missing.
    config.MEMORY_DIR = Path(tempfile.mkdtemp())
    model = _Model()
    out = await presence.consider("lull", now=AWAKE, ask=model)
    _check("no config file means off, not on", out is None and model.asked == [],
           f"{out!r} asked={len(model.asked)}")

    _fresh(_state(sent=2, last_sent=(AWAKE - timedelta(hours=9)).isoformat()))
    model = _Model()
    out = await presence.consider("lull", now=AWAKE, ask=model)
    _check("the daily message cap stops it before the model",
           out is None and model.asked == [], f"{out!r} asked={len(model.asked)}")

    _fresh(_state(last_sent=(AWAKE - timedelta(hours=2)).isoformat()))
    model = _Model()
    out = await presence.consider("lull", now=AWAKE, ask=model)
    _check("two hours after the last one is too soon",
           out is None and model.asked == [], f"{out!r} asked={len(model.asked)}")

    _fresh(_state(last_sent=(AWAKE - timedelta(hours=5)).isoformat()))
    model = _Model()
    out = await presence.consider("lull", now=AWAKE, ask=model)
    _check("five hours after the last one is fine", out is not None and len(model.asked) == 1,
           f"{out!r} asked={len(model.asked)}")

    _fresh(_state(calls=6))
    model = _Model()
    out = await presence.consider("lull", now=AWAKE, ask=model)
    _check("the daily call budget stops it before the model",
           out is None and model.asked == [], f"{out!r} asked={len(model.asked)}")

    # Yesterday's counters must not silence it today.
    _fresh(_state(day="2026-09-15", sent=2, calls=6))
    model = _Model()
    out = await presence.consider("lull", now=AWAKE, ask=model)
    _check("counters reset on a new local day", out is not None and len(model.asked) == 1,
           f"{out!r} asked={len(model.asked)}")


async def test_never_interrupts_a_live_conversation() -> None:
    from agents import presence

    _fresh(_state())
    model = _Model()
    out = await presence.consider("lull", now=AWAKE, ask=model,
                                  last_user_at=AWAKE - timedelta(minutes=8))
    _check("a conversation eight minutes old is still live",
           out is None and model.asked == [], f"{out!r} asked={len(model.asked)}")

    _fresh(_state())
    model = _Model()
    out = await presence.consider("lull", now=AWAKE, ask=model,
                                  last_user_at=AWAKE - timedelta(minutes=40))
    _check("forty minutes of quiet is a break, not an interruption",
           out is not None and len(model.asked) == 1, f"{out!r} asked={len(model.asked)}")


async def test_the_bundle_is_facts_only() -> None:
    from agents import presence

    _fresh(_state(), files={
        "tasks.md": "# Tasks\n\n- Investigate X/Twitter fetching\n- [x] done thing\n",
        "drafts.md": "\n## Universal Basic Income - 2026-09-14 15:00 UTC\n\nA draft body.\n",
    })
    model = _Model()
    await presence.consider("drafts", now=AWAKE, ask=model)
    bundle = model.asked[0]
    _check("the bundle carries the local time", "EDT" in bundle, bundle[:200])
    _check("the bundle names the reason it was woken", "drafts" in bundle, bundle[:200])
    _check("the bundle lists the open task", "X/Twitter" in bundle, bundle[:400])
    _check("the bundle excludes finished tasks", "done thing" not in bundle, bundle[:400])
    _check("the bundle dates the unreviewed draft", "2026-09-14" in bundle, bundle[:600])


async def test_silence_and_cleanup() -> None:
    from agents import presence

    _fresh(_state())
    model = _Model(reply="SILENT")
    out = await presence.consider("lull", now=AWAKE, ask=model)
    state = json.loads((config.MEMORY_DIR / "presence_state.json").read_text(encoding="utf-8"))
    _check("SILENT means nothing is sent", out is None, repr(out))
    _check("but the call is still counted", state["calls"] == 1, str(state))
    _check("and it does not count as a message sent", state["sent"] == 0, str(state))

    _fresh(_state())
    model = _Model(reply="  Still up? The deploy finished clean!  ")
    out = await presence.consider("lull", now=AWAKE, ask=model)
    state = json.loads((config.MEMORY_DIR / "presence_state.json").read_text(encoding="utf-8"))
    _check("the message comes back trimmed", out == "Still up? The deploy finished clean.", repr(out))
    _check("exclamation points are removed, per the style rules", "!" not in (out or ""), repr(out))
    _check("a sent message is counted", state["sent"] == 1 and state["last_sent"], str(state))

    _fresh(_state())
    model = _Model(reply="x" * 900)
    out = await presence.consider("lull", now=AWAKE, ask=model)
    _check("an overlong reply is dropped rather than sent half-formed", out is None, repr(out)[:80])

    _fresh(_state())
    model = _Model(reply="")
    out = await presence.consider("lull", now=AWAKE, ask=model)
    _check("an empty reply is silence", out is None, repr(out))

    _fresh(_state())
    model = _Model(reply=RuntimeError("model down"))
    raised = False
    try:
        out = await presence.consider("lull", now=AWAKE, ask=model)
    except Exception:
        raised = True
        out = "raised"
    _check("a model failure is silence, not a crash", not raised and out is None, repr(out))


def test_triggers() -> None:
    from agents import presence

    _fresh(_state(), files={"tasks.md": "- something\n"})
    due = presence.due_triggers(AWAKE, last_user_at=AWAKE - timedelta(minutes=40))
    _check("a conversation that ended 40 minutes ago is a lull", "lull" in due, str(due))

    due = presence.due_triggers(AWAKE, last_user_at=AWAKE - timedelta(minutes=5))
    _check("five minutes is not a lull", "lull" not in due, str(due))

    due = presence.due_triggers(AWAKE, last_user_at=AWAKE - timedelta(hours=30))
    _check("a day-old conversation is not a lull either", "lull" not in due, str(due))

    # 2026-09-17, live: the digest is stored by record_outbound, so "last message"
    # was IGOR talking to itself at 13:00. lull then fired on every tick from 13:25
    # to 14:30 and spent three model calls on a conversation that never happened.
    _fresh(_state(last_trigger={"lull": (AWAKE - timedelta(minutes=20)).isoformat()}),
           files={"tasks.md": "- something\n"})
    due = presence.due_triggers(AWAKE, last_user_at=AWAKE - timedelta(minutes=40))
    _check("a lull already considered does not fire again on the next tick",
           "lull" not in due, str(due))

    _fresh(_state(last_trigger={"lull": (AWAKE - timedelta(hours=3)).isoformat()}),
           files={"tasks.md": "- something\n"})
    due = presence.due_triggers(AWAKE, last_user_at=AWAKE - timedelta(minutes=40))
    _check("but a lull after the user speaks again does fire", "lull" in due, str(due))

    _fresh(_state(last_digest=(AWAKE - timedelta(hours=2)).isoformat()),
           files={"tasks.md": "- something\n"})
    due = presence.due_triggers(AWAKE, last_user_at=AWAKE - timedelta(hours=6))
    _check("two hours after the digest is a digest trigger", "digest" in due, str(due))

    _fresh(_state(last_digest=(AWAKE - timedelta(minutes=20)).isoformat()))
    due = presence.due_triggers(AWAKE, last_user_at=AWAKE - timedelta(hours=6))
    _check("twenty minutes after the digest is too soon", "digest" not in due, str(due))

    old = (AWAKE - timedelta(days=5)).strftime("%Y-%m-%d %H:%M UTC")
    _fresh(_state(), files={"drafts.md": f"\n## Universal Basic Income - {old}\n\nbody\n"})
    due = presence.due_triggers(AWAKE, last_user_at=AWAKE - timedelta(hours=6))
    _check("a five day old draft is worth raising", "drafts" in due, str(due))

    recent = (AWAKE - timedelta(days=1)).strftime("%Y-%m-%d %H:%M UTC")
    _fresh(_state(), files={"drafts.md": f"\n## Universal Basic Income - {recent}\n\nbody\n"})
    due = presence.due_triggers(AWAKE, last_user_at=AWAKE - timedelta(hours=6))
    _check("a draft from yesterday is not", "drafts" not in due, str(due))

    # The real drafts.md: three unanswered, oldest 35 days, newest 2 days. Keying on
    # the newest meant the weekly advocacy draft reset the trigger forever and the
    # 35-day-old one was never raised.
    ancient = (AWAKE - timedelta(days=35)).strftime("%Y-%m-%d %H:%M UTC")
    _fresh(_state(), files={"drafts.md": (
        f"\n## Universal Basic Income - {recent}\n\nbody\n"
        f"\n## Universal Basic Income - {ancient}\n\nbody\n")})
    due = presence.due_triggers(AWAKE, last_user_at=AWAKE - timedelta(hours=6))
    _check("a fresh draft does not hide an old unanswered one", "drafts" in due, str(due))

    _fresh(_state(last_trigger={"drafts": (AWAKE - timedelta(days=2)).isoformat()}),
           files={"drafts.md": f"\n## Universal Basic Income - {old}\n\nbody\n"})
    due = presence.due_triggers(AWAKE, last_user_at=AWAKE - timedelta(hours=6))
    _check("the same trigger does not fire twice in a week", "drafts" not in due, str(due))


if __name__ == "__main__":
    print("gates")
    asyncio.run(test_gates_run_before_the_model())
    print("\nlive conversation")
    asyncio.run(test_never_interrupts_a_live_conversation())
    print("\nthe bundle")
    asyncio.run(test_the_bundle_is_facts_only())
    print("\nsilence and cleanup")
    asyncio.run(test_silence_and_cleanup())
    print("\ntriggers")
    test_triggers()

    if _failures:
        print(f"\n{len(_failures)} FAILED: {', '.join(_failures)}")
        sys.exit(1)
    print("\nall pass")
