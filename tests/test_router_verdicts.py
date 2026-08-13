"""Router accuracy against the live model, including the SELF class added 2026-08-13.

Run on the server, it needs the Groq key. Adding a sixth class risks the five that
were already tuned to 19/20 over four rounds, so this checks the old cases too - the
opinion-question and research-mention regressions both happened before.

    sudo -u igor /opt/igor/venv/bin/python tests/test_router_verdicts.py
"""
import asyncio
import sys

sys.path.insert(0, "/opt/igor")

import orchestrator

CASES = [
    # The messages that actually produced fabrications on 2026-08-13.
    ("How can we utilize your autonomous scheduled system?", "SelfDescribe"),
    ("We could have you use your autonomous scheduled system to promote and inform.", "SelfDescribe"),
    # The regression my regex caused: a task that names a tool.
    ("Use your shell tool to tell me the disk usage on the server", "React"),
    ("What does your shell tool do", "SelfDescribe"),

    ("What agents do you have?", "SelfDescribe"),
    ("what tools can you use", "SelfDescribe"),
    ("how does IGOR routing work", "SelfDescribe"),
    ("explain your memory system", "SelfDescribe"),
    ("Can you change your own code?", "SelfDescribe"),
    ("what are your limits", "SelfDescribe"),

    # Previously-fixed regressions. Opinion questions read as CONFIG once because the
    # CONFIG line ended with "preferences"; research mentions read as RESEARCH.
    ("what do you think about self hosting", "Direct"),
    ("How do you feel about these headlines from the morning digest", "Direct"),
    ("I always drop these videos to see if we can pick up on new tech", "Direct"),
    ("that research feature you have is interesting", "Direct"),

    ("search for recent UBI pilot results", "React"),
    ("read agents/react.py and summarise what it does", "React"),
    ("write me a report on autonomous agents", "React"),

    # The boundary I am least sure about: a current value, not how it works.
    ("what time does the digest go out", "Monitor"),
    ("what is on the watchlist", "Monitor"),

    ("drop weather from the digest", "ConfigEdit"),
    ("change the digest time to 14:00", "ConfigEdit"),
]


async def main():
    async def notify(_):
        pass

    o = orchestrator.Orchestrator(notify)
    wrong = []
    for message, expected in CASES:
        got = await o._classify(message)
        ok = got == expected
        if not ok:
            wrong.append((message, expected, got))
        print(f"  {'ok  ' if ok else 'WRONG'}  {expected:13} {'' if ok else '-> ' + got:16} {message[:58]}")

    total = len(CASES)
    print(f"\n  {total - len(wrong)}/{total} correct")
    if wrong:
        print("\n  misroutes:")
        for message, expected, got in wrong:
            print(f"    {message}\n      expected {expected}, got {got}")
    return 1 if wrong else 0


sys.exit(asyncio.run(main()))
