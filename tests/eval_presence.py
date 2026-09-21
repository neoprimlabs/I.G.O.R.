"""Scored eval for presence: does it speak when it should, and shut up when it should?

Run on the server, needs the Groq key:

    sudo -u igor /opt/igor/venv/bin/python tests/eval_presence.py

Why this exists. ProactiveBench evaluated 22 models and found the failure of
proactive agents is not missing a chance to help, it is refusing to shut up: they
"fail to stay silent when the user does not require any assistance", giving a high
false alarm ratio. ProVoice-Bench names over-triggering the same way. So the number
that matters most is the FALSE ALARM RATE, and the silence cases are weighted.

But silence has a failure of its own, and this repo has already been bitten by it:
crediting abstention fixed SelfDescribe's fabrication and then over-shot into
stonewalling, caught only by a scored set. On 2026-09-21 this eval scored 2 misses
out of 2 - presence never spoke, not even about drafts 38 days old. Both numbers are
reported, always.

Two modes, because the code decides different amounts in each:

- lull: the code cannot know whether there is anything to say, so the model judges,
  and SILENT is a valid answer.
- drafts, stale_task: the code only raises these when a fact is already established
  (a draft unanswered 3+ days, a task untouched 7+ days). The model writes the
  message. Silence there is a failure to do the job, not a judgement.

Bundles are the shape presence._facts actually builds, including the "Already sent"
block. Fixtures that drift from the real bundle measure a prompt against input the
model will never see.
"""

import asyncio
import sys

sys.path.insert(0, "/opt/igor")

from agents import presence

QUIET_EVENING = """Woken by: lull
Current time: Wed 2026-09-16 21:40 EDT (01:40 UTC)
The user last said something: 38 minutes ago
Open tasks: none
Drafts awaiting review: none
What the user said, oldest first:
  user: thanks, that's sorted
  user: no I think that's it
Already sent to the user unprompted. Do not repeat, re-raise, follow up on, or comment on any of it:
  - Good. Anything else you want to look at tonight?"""

NOTHING_AT_ALL = """Woken by: lull
Current time: Thu 2026-09-17 15:05 EDT (19:05 UTC)
The user last said something: 61 minutes ago
Open tasks: none
Drafts awaiting review: none
What the user said: nothing stored."""

ALREADY_SAID = """Woken by: lull
Current time: Thu 2026-09-17 11:30 EDT (15:30 UTC)
The user last said something: 40 minutes ago
Open tasks (1):
  - Investigate X/Twitter fetching for the Research agent
Drafts awaiting review: none
What the user said, oldest first:
  user: yeah leave it for now
Already sent to the user unprompted. Do not repeat, re-raise, follow up on, or comment on any of it:
  - That X/Twitter task has been open a while - still want it?"""

MID_WORK = """Woken by: lull
Current time: Wed 2026-09-16 23:10 EDT (03:10 UTC)
The user last said something: 27 minutes ago
Open tasks (1):
  - Investigate X/Twitter fetching for the Research agent
Drafts awaiting review: none
What the user said, oldest first:
  user: still working through the deploy, back in a bit"""

# The actual 2026-09-18 and 09-20 failure: the only thing in context is IGOR's own
# resolved alert, and it sent that back to the user. Twice.
ECHO_ALERT = """Woken by: lull
Current time: Sun 2026-09-20 10:34 EDT (14:34 UTC)
The user last said something: 8700 minutes ago
Open tasks: none
Drafts awaiting review: none
What the user said: nothing stored.
Already sent to the user unprompted. Do not repeat, re-raise, follow up on, or comment on any of it:
  - **Morning Digest** **Open Tasks:** none **Weather:** Today: Overcast, 75F / 65F **AI News:** - Step5 Preview
  - **Model Alert** The configured models qwen/qwen3.6-27b are no longer available on Groq. They may have been de"""

CHECKIN_LATE = """Woken by: checkin
Current time: Mon 2026-09-21 00:40 EDT (04:40 UTC)
The user last said something: 10017 minutes ago
Open tasks (2):
  - Investigate and implement X/Twitter content fetching for the Research agent
  - Test write confirmation
Drafts sent for review and not replied to (3):
  Universal Basic Income - 2026-09-14 15:00 UTC - sent 6 days ago
  Universal Basic Income - 2026-08-24 15:00 UTC - sent 27 days ago
  Universal Basic Income - 2026-08-13 20:02 UTC - sent 38 days ago
What the user said: nothing stored."""

CHECKIN_AFTERNOON = """Woken by: checkin
Current time: Tue 2026-09-22 15:10 EDT (19:10 UTC)
The user last said something: 95 minutes ago
Open tasks: none
Drafts awaiting review: none
What the user said, oldest first:
  user: going to be heads down on the engine build most of today"""

CASES = [
    # Judgement cases. Silence is correct, and this is the measured failure mode.
    ("a conversation that ended cleanly", QUIET_EVENING, "SILENT"),
    ("nothing pending and nothing said", NOTHING_AT_ALL, "SILENT"),
    ("the one thing pending was already raised and waved off", ALREADY_SAID, "SILENT"),
    ("the user said they are mid-task and coming back", MID_WORK, "SILENT"),
    ("only its own digest and a resolved alert to talk about", ECHO_ALERT, "SILENT"),
    # Check-in cases. The code decided it is time; the model writes the message.
    ("the daily check-in, late at night with work pending", CHECKIN_LATE, "SPEAK"),
    ("the daily check-in, afternoon after they said they were busy", CHECKIN_AFTERNOON, "SPEAK"),
]

# Anything echoed back from the Already sent block is the 09-18 bug returning.
ECHOES = ("morning digest", "no longer available", "qwen", "still want it")

# The 09-21 failure: asked for a check-in, sent "The draft titled Universal Basic
# Income was sent 6 days ago". A check-in that names IGOR's filing is not a message.
REPORTY = ("draft", "task list", "tasks.md", "drafts.md", "days ago", "awaiting review",
           "outstanding tasks", "sent on 2026")


def _reason(bundle: str) -> str:
    return bundle.splitlines()[0].replace("Woken by:", "").strip()


async def main() -> int:
    only = [a.lower() for a in sys.argv[1:]]
    cases = [c for c in CASES if not only or any(o in c[0].lower() for o in only)]

    false_alarms, misses, echoes, reports, errors = [], [], [], [], []
    for name, bundle, want in cases:
        reason = _reason(bundle)
        try:
            raw = await presence._ask_model(bundle, reason)
        except Exception as e:
            errors.append((name, f"{type(e).__name__}: {e}"))
            print(f"  ERROR  {name}: {type(e).__name__}")
            continue
        message = presence._tidy(raw)
        got = "SILENT" if message is None else "SPEAK"
        ok = got == want
        mode = "write" if reason in presence._COMPOSE_TRIGGERS else "judge"
        print(f"  {'ok   ' if ok else 'WRONG'}  want {want:6} got {got:6}  [{mode}]  {name}")
        if message:
            print(f"           said: {message[:150]!r}")
        if not ok:
            (false_alarms if want == "SILENT" else misses).append(name)
        if message and any(e in message.lower() for e in ECHOES):
            echoes.append(name)
            print("           ECHO: repeated something from the Already sent block")
        if message and reason == "checkin":
            named = [r for r in REPORTY if r in message.lower()]
            if named:
                reports.append(name)
                print(f"           REPORT: a check-in that names {named} is not a message")
        await asyncio.sleep(20)

    scored = len(cases) - len(errors)
    if not scored:
        print("\n  INVALID RUN: every case errored, nothing was measured.")
        return 2

    silent_cases = [c for c in cases if c[2] == "SILENT"]
    speak_cases = [c for c in cases if c[2] == "SPEAK"]
    print(f"\n  {scored - len(false_alarms) - len(misses)}/{scored} correct")
    if silent_cases:
        print(f"  false alarm rate: {len(false_alarms)}/{len(silent_cases)} "
              f"({len(false_alarms) / len(silent_cases):.0%}) - spoke when the honest answer was nothing")
    if speak_cases:
        print(f"  miss rate:        {len(misses)}/{len(speak_cases)} "
              f"({len(misses) / len(speak_cases):.0%}) - silent with something real to say")
    print(f"  echoes:           {len(echoes)} - repeated something already sent")
    print(f"  reports:          {len(reports)} - a check-in that read as a status report")
    for name in false_alarms:
        print(f"    false alarm: {name}")
    for name in misses:
        print(f"    miss: {name}")
    for name in echoes:
        print(f"    echo: {name}")
    for name in reports:
        print(f"    report: {name}")
    if errors:
        print(f"  {len(errors)} cases errored and were not scored")
    return 1 if (false_alarms or misses or echoes or reports) else 0


sys.exit(asyncio.run(main()))
