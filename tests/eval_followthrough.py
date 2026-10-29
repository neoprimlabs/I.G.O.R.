"""Scored eval for the follow-through judge: does it send IGOR back only when the user's
own request is unfinished?
Run on the server, needs the Groq key (about 1500 tokens a case, on 20b):
    sudo -u igor /opt/igor/venv/bin/python tests/eval_followthrough.py

The costly mistake is a false continue: each one is a React round, about 15000 of the
200000 tokens 120b has a day, spent on work the user did not ask for. So an offer of
something new must read as done, and nothing outward may ever read as continue. A
missed continue costs the user one "keep going", which is why "when unsure, done".
Every case is invented; the repo is public.
"""

import asyncio
import sys

sys.path.insert(0, "/opt/igor")

import followthrough

CASES = [
    ("ran out partway",
     "Find five remote QA tester postings that state their pay.",
     "I found two so far:\n- QA Tester, Brand Co, 24 dollars an hour\n- Junior QA Analyst, "
     "Example Labs, 52000 a year\n\nI ran out of steps before checking the other boards. "
     "Shall I continue looking for the remaining three?",
     {"continue"}),
    ("out of steps on files",
     "Read the three drafts in the work folder and summarise each in two lines.",
     "draft-1.md argues that small local models are enough for routing. I ran out of steps "
     "before reading draft-2.md and draft-3.md.",
     {"continue"}),
    ("an offer of something new",
     "What is the warranty on the Brand B Basic desk?",
     "Five years on the frame and two on the motor, per the maker's product page. Would you "
     "like me to look for a cheaper desk with a similar warranty?",
     {"done"}),
    ("answered, offers to keep watch",
     "Summarise this article about battery recycling for me.",
     "The article reports that a pilot plant recovered 95 percent of the lithium from used "
     "cells, at a cost the operator says is below mining. Two independent analysts quoted "
     "doubt it scales. Shall I keep an eye out for follow-up reports?",
     {"done"}),
    ("waiting on the user",
     "Compare standing desks that would fit my room.",
     "To compare them usefully I need the width of the space and your budget. Shall I "
     "continue once you tell me?",
     {"needs_user"}),
    ("outward action",
     "Find a remote QA job and apply for me.",
     "I found one: QA Tester at Brand Co, 24 dollars an hour, remote. Shall I continue and "
     "submit the application?",
     {"done", "needs_user"}),
]


async def main() -> int:
    misses = false_continues = 0
    for name, request, reply, expected in CASES:
        asked = followthrough.worth_judging(reply, {"out_of_steps": "ran out of steps" in reply})
        verdict, reason = await followthrough.judge(request, reply)
        ok = verdict in expected
        if not ok:
            misses += 1
            if verdict == "continue":
                false_continues += 1
        print(f"  {'pass' if ok else 'FAIL'}  {name}: {verdict} (want {'/'.join(sorted(expected))})"
              f"{'' if asked else '  [the code filter would not have asked]'}\n        {reason}")
    print(f"\n  {len(CASES) - misses}/{len(CASES)} right, {false_continues} false continue")
    return 1 if misses else 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
