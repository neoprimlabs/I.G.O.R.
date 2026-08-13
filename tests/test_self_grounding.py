"""Questions about IGOR get ground truth in the prompt, not a suggestion to go find it.

Regression cover for 2026-08-13. React described a scheduler.yaml, a run_agent()
helper and a content filter, none of which exist. Fixing read_file was not enough:
the next day it made zero tool calls and simply elaborated on its own fabricated
answer, which was sitting in the conversation window. A wrong answer in context is
an undetected input to the next one.
"""
import sys

sys.path.insert(0, r"c:\Dev\IGOR")

from agents.react import _ground_if_self_referential, _SELF_QUESTION_RE

_passed = 0
_failed = 0


def check(name, condition, detail=""):
    global _passed, _failed
    if condition:
        _passed += 1
        print(f"  PASS  {name}")
    else:
        _failed += 1
        print(f"  FAIL  {name}  {detail}")


BASE = "You are IGOR."

print("=== self-referential detection ===")

# The two messages that actually produced fabrications, verbatim from Discord.
for msg in [
    "We could have you use your autonomous scheduled system to promote and inform.",
    "How can we ustilize your autonomous scheduled system?",
]:
    check(f"grounds the real failing message: {msg[:42]}...",
          _ground_if_self_referential(msg, BASE) != BASE)

for msg in [
    "How can we utilize your abilities to push for UBI?",
    "What agents do you have?",
    "how does IGOR routing work",
    "what tools can you use",
    "Can you change your own code?",
    "explain your memory system",
]:
    check(f"grounds: {msg[:40]}", _ground_if_self_referential(msg, BASE) != BASE, msg)

for msg in [
    "The world is in need of a major change.",
    "How do you feel about these headlines?",
    "search for recent UBI pilot results",
    "what is the weather tomorrow",
]:
    check(f"leaves alone: {msg[:40]}", _ground_if_self_referential(msg, BASE) == BASE, msg)

print("\n=== what the grounded prompt contains ===")

grounded = _ground_if_self_referential("what does your scheduler do?", BASE)

check("the original system prompt survives", BASE in grounded)
check("the block is marked authoritative", "VERIFIED SYSTEM FACTS" in grounded)
check("it says this block beats the conversation",
      "this block is correct and the conversation is not" in grounded)

# The specific fabrications must be contradicted by text now in the prompt.
for absent in ["No content filter", "No `scheduler.yaml`", "run_agent()", "No connection to any external"]:
    check(f"prompt now denies: {absent}", absent in grounded, grounded[-500:])

for present in ["read_file", "memory_write", "monitor.setup()", "digest_config.md"]:
    check(f"prompt now states: {present}", present in grounded)

check("grounding stays within a sane token cost", len(grounded) - len(BASE) < 4200,
      f"added {len(grounded) - len(BASE)} chars")

print(f"\n  {_passed} passed, {_failed} failed")
sys.exit(1 if _failed else 0)
