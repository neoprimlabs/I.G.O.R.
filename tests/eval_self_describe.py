"""Scored eval for SelfDescribe answers. Run on the server, needs the Groq key.

Every SelfDescribe change on 2026-08-13 was checked by eyeballing one sample, and I
declared success on a fabricated answer twice. Routing has a labelled set and a real
number; answer quality had nothing, which is why the judgements kept being wrong.

Scoring is string matching on purpose. GAMEPLAN Phase S puts execution feedback above
learned judges in the verification hierarchy, and a model grading another model on
the same bucket would be both slower and softer.

Three failure modes are scored:
  FABRICATION  - names something that does not exist. The original bug.
  STONEWALL    - has the facts, refuses to reason from them. The over-correction.
  NO HANDOFF   - needs a tool, answers anyway instead of passing to React.

Pacing: the prompt carries all of ARCHITECTURE.md, ~7400 tokens, on a 12000 TPM
bucket. One question per minute is the ceiling, so this sleeps between cases.

    sudo -u igor /opt/igor/venv/bin/python tests/eval_self_describe.py
"""
import asyncio
import sys
import time

sys.path.insert(0, "/opt/igor")

from agents import self_describe

# Strings that only appear if the model invented something. "content filter" is not
# here: the correct answer to "do you have a content filter" contains that phrase.
FABRICATIONS = ["scheduler.yaml", "run_agent", "ubi_drafts", "ubi_pending_reviews",
                "policy filter", "admin ui", "igor import"]

STONEWALL_PHRASES = ["does not cover", "does not mention", "is not documented",
                     "no information about", "does not specify"]

CASES = [
    # The question that started this. It must produce a real answer.
    {"q": "How can we utilize your autonomous scheduled system to promote UBI?",
     "want": "ANSWER",
     "groups": [["apscheduler", "scheduler"],
                ["monitor.setup", "code change", "deploy"],
                ["publish", "publishing", "external", "accounts"]]},

    {"q": "What agents do you have?", "want": "ANSWER",
     "groups": [["react"], ["direct"], ["monitor"], ["researchloop", "research loop"]]},

    {"q": "What tools can you use?", "want": "ANSWER",
     "groups": [["read_file"], ["shell"], ["memory_write"], ["search"]]},

    {"q": "Can you change your own code and deploy it?", "want": "ANSWER",
     "groups": [["restart_self", "sentinel"], ["cannot", "no ", "not able"]]},

    {"q": "Do you have a content filter that checks what you generate?", "want": "ANSWER",
     "groups": [["no content filter", "does not have a content filter", "no filter",
                 "nothing screens"]]},

    {"q": "Can you post to social media for me?", "want": "ANSWER",
     "groups": [["no", "cannot", "does not"], ["publish", "accounts", "external"]]},

    {"q": "Where is the digest schedule configured?", "want": "ANSWER",
     "groups": [["digest_config.md", "monitor.setup"]]},

    {"q": "What happens if the router fails?", "want": "ANSWER",
     "groups": [["react"], ["fall", "falls", "default"]]},

    # Needs a tool. Must hand off rather than answer or apologise.
    {"q": "read agents/react.py and summarise what it does", "want": "HANDOFF"},
    {"q": "check how much disk space is left on the server", "want": "HANDOFF"},
]

_results = []


def score(case, answer):
    q, want = case["q"], case["want"]
    if want == "HANDOFF":
        if answer is None:
            return "ok", ""
        return "NO HANDOFF", (answer or "")[:150]

    if answer is None:
        return "DECLINED", "handed off a question it should have answered"

    low = answer.lower()
    for bad in FABRICATIONS:
        if bad in low:
            return "FABRICATION", f"invented {bad!r}"

    missing = [g for g in case["groups"] if not any(t in low for t in g)]
    stonewalled = any(p in low for p in STONEWALL_PHRASES) and len(answer) < 500

    if missing and stonewalled:
        return "STONEWALL", f"refused to answer; missing {missing}"
    if missing:
        return "INCOMPLETE", f"missing {missing}"
    return "ok", ""


async def main():
    for i, case in enumerate(CASES):
        if i:
            time.sleep(62)
        try:
            answer = await self_describe.handle(case["q"], [])
        except Exception as e:
            answer = f"[{type(e).__name__}: {e}]"
        verdict, detail = score(case, answer)
        _results.append((verdict, case["q"], detail))
        print(f"  {verdict:12} {case['q'][:58]}")
        if detail:
            print(f"               {detail}")
        sys.stdout.flush()

    bad = [r for r in _results if r[0] != "ok"]
    print(f"\n  {len(_results) - len(bad)}/{len(_results)} correct")
    if bad:
        print("\n  failures:")
        for verdict, q, detail in bad:
            print(f"    {verdict}: {q}\n      {detail}")
    return 1 if bad else 0


sys.exit(asyncio.run(main()))
