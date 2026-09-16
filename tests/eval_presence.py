"""Scored eval for the one judgement presence makes: speak, or stay silent.

Run on the server, needs the Groq key:

    sudo -u igor /opt/igor/venv/bin/python tests/eval_presence.py

Why this exists. ProactiveBench evaluated 22 models and found the failure of
proactive agents is not missing a chance to help, it is refusing to shut up: they
"fail to stay silent when the user does not require any assistance", giving a high
false alarm ratio. ProVoice-Bench names over-triggering the same way. So the number
that matters here is the FALSE ALARM RATE - how often it speaks when the honest
answer is nothing - and the set is weighted toward cases where silence is correct.

This scores the model's judgement only. The gates in presence.py that stop it before
any call are covered by tests/test_presence.py, which needs no API.

Each case is a fixed facts block, exactly the shape presence._facts builds. The
expected verdict is hand-marked and written down before the model is asked.
"""

import asyncio
import sys

sys.path.insert(0, "/opt/igor")

from agents import presence

QUIET_EVENING = """Woken by: lull
Current time: Wed 2026-09-16 21:40 EDT (01:40 UTC)
Last message in the conversation: 38 minutes ago
Open tasks: none
Drafts awaiting review: none
Recent conversation, oldest first:
  user: thanks, that's sorted
  you: Good. Anything else you want to look at tonight?
  user: no I think that's it"""

NOTHING_AT_ALL = """Woken by: lull
Current time: Thu 2026-09-17 15:05 EDT (19:05 UTC)
Last message in the conversation: 61 minutes ago
Open tasks: none
Drafts awaiting review: none
Recent conversation: none stored."""

ALREADY_SAID = """Woken by: digest
Current time: Thu 2026-09-17 11:30 EDT (15:30 UTC)
Last message in the conversation: 240 minutes ago
Open tasks (1):
  - Investigate X/Twitter fetching for the Research agent
Drafts awaiting review: none
Recent conversation, oldest first:
  you: [sent proactively] That X/Twitter task has been open a while - still want it?
  user: yeah leave it for now"""

MID_WORK = """Woken by: lull
Current time: Wed 2026-09-16 23:10 EDT (03:10 UTC)
Last message in the conversation: 27 minutes ago
Open tasks (1):
  - Investigate X/Twitter fetching for the Research agent
Drafts awaiting review: none
Recent conversation, oldest first:
  user: still working through the deploy, back in a bit"""

OLD_DRAFTS = """Woken by: drafts
Current time: Thu 2026-09-17 14:20 EDT (18:20 UTC)
Last message in the conversation: 400 minutes ago
Open tasks: none
Drafts sent for review and not replied to (3):
  Universal Basic Income - 2026-09-14 15:00 UTC - sent 3 days ago
  Universal Basic Income - 2026-08-24 15:00 UTC - sent 24 days ago
  Universal Basic Income - 2026-08-13 20:02 UTC - sent 35 days ago
Recent conversation, oldest first:
  user: what's the weather looking like
  you: Overcast, 86F today."""

STALE_TASK = """Woken by: stale_task
Current time: Thu 2026-09-17 16:40 EDT (20:40 UTC)
Last message in the conversation: 900 minutes ago
Open tasks (2):
  - Investigate X/Twitter fetching for the Research agent
  - Test write confirmation
Drafts awaiting review: none
Recent conversation, oldest first:
  user: remind me what's outstanding sometime
  you: Will do."""

CASES = [
    # Silence is correct. Weighted heavier, because this is the measured failure.
    ("a conversation that ended cleanly", QUIET_EVENING, "SILENT"),
    ("nothing pending and nothing said", NOTHING_AT_ALL, "SILENT"),
    ("the one thing pending was already raised and waved off", ALREADY_SAID, "SILENT"),
    ("the user said they are mid-task and coming back", MID_WORK, "SILENT"),
    # Speaking is correct: something specific, current, and unanswered.
    ("three drafts unanswered, the oldest 35 days", OLD_DRAFTS, "SPEAK"),
    ("tasks outstanding and the user asked to be reminded", STALE_TASK, "SPEAK"),
]


async def main() -> int:
    only = [a.lower() for a in sys.argv[1:]]
    cases = [c for c in CASES if not only or any(o in c[0].lower() for o in only)]

    false_alarms, misses, errors = [], [], []
    for name, bundle, want in cases:
        try:
            raw = await presence._ask_model(bundle)
        except Exception as e:
            errors.append((name, f"{type(e).__name__}: {e}"))
            print(f"  ERROR  {name}: {type(e).__name__}")
            continue
        message = presence._tidy(raw)
        got = "SILENT" if message is None else "SPEAK"
        ok = got == want
        print(f"  {'ok   ' if ok else 'WRONG'}  want {want:6} got {got:6}  {name}")
        if not ok:
            print(f"           said: {(message or raw)[:160]!r}")
            (false_alarms if want == "SILENT" else misses).append(name)
        await asyncio.sleep(20)

    scored = len(cases) - len(errors)
    if not scored:
        print("\n  INVALID RUN: every case errored, nothing was measured.")
        return 2

    silent_cases = [c for c in cases if c[2] == "SILENT"]
    print(f"\n  {scored - len(false_alarms) - len(misses)}/{scored} correct")
    if silent_cases:
        rate = len(false_alarms) / len(silent_cases)
        print(f"  false alarm rate: {len(false_alarms)}/{len(silent_cases)} ({rate:.0%}) "
              f"- spoke when the honest answer was nothing")
    if misses:
        print(f"  missed: {len(misses)} of {len(cases) - len(silent_cases)} - stayed silent with something real to say")
    for name in false_alarms:
        print(f"    false alarm: {name}")
    for name in misses:
        print(f"    miss: {name}")
    if errors:
        print(f"  {len(errors)} cases errored and were not scored")
    return 1 if (false_alarms or misses) else 0


sys.exit(asyncio.run(main()))
