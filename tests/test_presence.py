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
            "last_trigger": {}, "raised": {}, "last_digest": None}
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


def test_claimed_work_is_dropped() -> None:
    """Presence has no tools and does nothing between messages, so a first-person
    claim of having done work is false by construction.

    Real output from the eval on 2026-09-21, one edit from shipping: "I've been
    working on the X/Twitter fetch module - just finished a solid prototype, ready
    when you're ready to look." The prompt already forbade inventing activity. It did
    it anyway, which is why this is a gate and not a sentence in a prompt. And
    record_outbound would have kept it as an input to every later turn.
    """
    from agents import presence

    invented = [
        "I've been working on the X/Twitter fetch module, just finished a prototype.",
        "I have been looking into that engine build for you.",
        "I finished the draft you wanted, ready to review.",
        "I'm building the fetcher now, my progress is good.",
        "Made some headway on the research agent - I implemented the fetch loop.",
    ]
    for text in invented:
        _check(f"dropped: {text[:44]}", presence._tidy(text) is None, repr(presence._tidy(text)))

    honest = [
        "Late one. Hope the engine build is behaving.",
        "Hope the build is going smoothly today. Shout if you hit a snag.",
        "Quiet afternoon here. How is it going on your end?",
        "You mentioned the deploy earlier - how did it land?",
    ]
    for text in honest:
        _check(f"kept: {text[:44]}", presence._tidy(text) == text, repr(presence._tidy(text)))


def test_the_daily_checkin() -> None:
    """What was actually asked for: "Just check in on me", and messages "at some
    random times". What shipped first was "The draft titled Universal Basic Income
    was sent 6 days ago" - a report about a file they have never seen."""
    from agents import presence

    morning = _utc(2026, 9, 21, 15, 0)     # 11:00 EDT, the earliest a check-in can be
    night = _utc(2026, 9, 22, 2, 0)        # 22:00 EDT the previous evening

    _fresh(_state())
    planned = presence._checkin_time(morning, presence._load_state())
    local = planned.astimezone(presence.clock.USER_TZ)
    _check("a check-in is planned inside waking hours",
           11 <= local.hour < 23, local.strftime("%H:%M"))
    _check("and it is stored, not re-rolled on the next tick",
           presence._checkin_time(morning, presence._load_state()) == planned, "")

    _fresh(_state())
    presence._checkin_time(morning, presence._load_state())
    state = presence._load_state()
    at = presence._parse(state["checkin"]["at"])
    before = at - timedelta(minutes=5)
    _check("not due before its time", "checkin" not in presence.due_triggers(before), "")
    _check("due once the time arrives",
           "checkin" in presence.due_triggers(at + timedelta(minutes=1)), "")

    state["checkin"]["sent"] = True
    presence._save_state(state)
    _check("and not again the same day",
           "checkin" not in presence.due_triggers(at + timedelta(hours=3)), "")

    times = set()
    for day in range(14):
        _fresh(_state())
        when = morning + timedelta(days=day)
        times.add(presence._checkin_time(when, presence._load_state()).astimezone(
            presence.clock.USER_TZ).strftime("%H:%M"))
    _check("the time moves from day to day", len(times) > 1, str(sorted(times)[:4]))

    _check("a check-in is written, never judged",
           presence._system_for("checkin") is presence._SYSTEM_CHECKIN)
    for banned in ("draft", "task list", "file"):
        _check(f"the check-in prompt forbids naming a {banned}",
               banned in presence._SYSTEM_CHECKIN.lower(), "")
    _check("the check-in prompt does not offer silence",
           "SILENT" not in presence._SYSTEM_CHECKIN)


def test_which_prompt_each_trigger_gets() -> None:
    """drafts and stale_task only fire once the code has established a fact, so the
    model writes the message. lull is the only one where it still judges - and on
    2026-09-21 judging everything scored 2 misses out of 2."""
    from agents import presence

    _check("a lull asks the model to judge",
           presence._system_for("lull") is presence._SYSTEM_JUDGE)
    _check("a check-in asks the model to write",
           presence._system_for("checkin") is presence._SYSTEM_CHECKIN)
    _check("the check-in prompt does not offer silence as an option",
           "SILENT" not in presence._SYSTEM_CHECKIN)
    _check("the judge prompt still does", "SILENT" in presence._SYSTEM_JUDGE)
    _check("the judge prompt forbids repeating what was already sent",
           "Already sent" in presence._SYSTEM_JUDGE)


async def test_own_messages_are_not_material() -> None:
    """2026-09-18 and 09-20: presence sent the user a resolved "qwen3.6 is gone"
    alert, twice. It was not reporting anything. record_outbound stores IGOR's own
    proactive sends, the bundle handed them back under "Recent conversation", and the
    model raised what was in front of it - the same mechanism as the 2026-08-13
    fabrication, where stored output became an input to every later turn.

    They still belong in the bundle, so it does not repeat itself. They do not belong
    there as news.
    """
    from agents import presence
    import context_store

    path = _fresh(_state())
    original_db = context_store._DB_PATH
    try:
        context_store._DB_PATH = path / "context.db"
        context_store.append("user", "can you look at the router thing")
        context_store.append(
            "assistant",
            "[sent proactively]\n**Model Alert**\nThe configured models qwen/qwen3.6-27b "
            "are no longer available on Groq. " + "x" * 400)

        model = _Model()
        await presence.consider("lull", now=AWAKE, ask=model,
                                last_user_at=AWAKE - timedelta(minutes=40))
        bundle = model.asked[0]
    finally:
        # Leaving this pointed at a temp database makes every later test read
        # timestamps from the real present and get blocked as "a conversation is live".
        context_store._DB_PATH = original_db

    _check("what the user said is still shown", "router thing" in bundle, bundle[-500:])
    _check("what IGOR already sent is labelled as already sent",
           "already sent" in bundle.lower(), bundle[-500:])
    _check("the label tells the model not to repeat it",
           "do not repeat" in bundle.lower(), bundle[-500:])
    _check("it is truncated rather than pasted in whole",
           "x" * 200 not in bundle, f"bundle is {len(bundle)} chars")
    _check("the internal marker is not shown to the model",
           "[sent proactively]" not in bundle, bundle[-400:])


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

    # Removed 2026-09-21. It woke presence 90 minutes after the digest, when the only
    # new material in context was IGOR's own digest - so the one thing available to
    # talk about was the one thing it must never repeat. Four decisions in three days:
    # two silent, two echoing a resolved qwen3.6 alert back at the user.
    _fresh(_state(last_digest=(AWAKE - timedelta(hours=2)).isoformat()),
           files={"tasks.md": "- something\n"})
    due = presence.due_triggers(AWAKE, last_user_at=AWAKE - timedelta(hours=6))
    _check("the digest never wakes presence to comment on itself",
           "digest" not in due, str(due))

    # drafts and stale_task were triggers until 2026-09-21. Each sent a standalone
    # report about IGOR's own bookkeeping, and the user's answer to the first one was
    # "I asked for a message. Not a report I don't even know where is being saved."
    # Pending work now reaches the model only as context for the daily check-in.
    old = (AWAKE - timedelta(days=35)).strftime("%Y-%m-%d %H:%M UTC")
    _fresh(_state(), files={"drafts.md": f"\n## Universal Basic Income - {old}\n\nbody\n",
                            "tasks.md": "- something long forgotten\n"})
    due = presence.due_triggers(AWAKE, last_user_at=AWAKE - timedelta(hours=6))
    _check("an old draft does not become a message of its own", "drafts" not in due, str(due))
    _check("nor does a stale task list", "stale_task" not in due, str(due))


if __name__ == "__main__":
    print("gates")
    asyncio.run(test_gates_run_before_the_model())
    print("\nlive conversation")
    asyncio.run(test_never_interrupts_a_live_conversation())
    print("\nthe bundle")
    asyncio.run(test_the_bundle_is_facts_only())
    print("\nwrite mode versus judge mode")
    test_which_prompt_each_trigger_gets()
    print("\nits own messages are not material")
    asyncio.run(test_own_messages_are_not_material())
    print("\nsilence and cleanup")
    asyncio.run(test_silence_and_cleanup())
    print("\ntriggers")
    test_triggers()

    if _failures:
        print(f"\n{len(_failures)} FAILED: {', '.join(_failures)}")
        sys.exit(1)
    print("\nall pass")
