"""Placeholder threads must never reach the planner's do-not-repeat list.

Regression cover for the 51-iteration run of 2026-08-14. The distill prompt ended
with "Then a final line, exactly:" followed by an angle-bracket placeholder, and the
model copied it verbatim in 8 of 51 iterations. Those junk threads were fed straight
back into the planner as "already pursued, do not repeat".

Every input below is a real line from that run.
"""
import sys

sys.path.insert(0, r"c:\Dev\IGOR")

from agents.research_loop import _DISTILL_SYSTEM, _clean_thread, _extract_recent_threads

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


print("=== rejecting placeholder echoes (verbatim from the run) ===")

for junk in [
    "Next: <the single most promising thread to pursue next>",
    "Next: <the most promising thread to pursue next>",
    "Next: the most promising thread to pursue next",
    "Next: The Single Most Promising Thread To Pursue Next",
]:
    check(f"drops: {junk[6:46]}", _clean_thread(junk) == "", repr(_clean_thread(junk)))

print("\n=== keeping real threads ===")

real = "Next: research small-business accounting data entry automation in the boutique apparel vertical."
check("keeps a plain thread", _clean_thread(real).startswith("research small-business"))

bracketed = "Next: <explore inventory reconciliation automation in independent bakery businesses>"
check("strips angle brackets from a real thread",
      _clean_thread(bracketed) == "explore inventory reconciliation automation in independent bakery businesses",
      repr(_clean_thread(bracketed)))

# Iteration 9: the model echoed the placeholder AND then wrote a real thread.
salvage = ("Next: the most promising thread to pursue next: small business pet grooming "
           "salons manual client intake data entry hours per week")
check("salvages the real half after an echoed placeholder",
      _clean_thread(salvage).startswith("small business pet grooming salons"),
      repr(_clean_thread(salvage)))

check("an echoed placeholder with nothing after it is still dropped",
      _clean_thread("Next: the most promising thread to pursue next:") == "")

print("\n=== the block handed to the planner ===")

content = "\n".join([
    "Next: <the single most promising thread to pursue next>",
    "Next: real estate accounting data entry automation cost comparison.",
    "Next: <the single most promising thread to pursue next>",
    "Next: small business bar inventory reconciliation automation.",
    "Next: the most promising thread to pursue next",
])
block = _extract_recent_threads(content)
check("junk is filtered out of the do-not-repeat list",
      "most promising thread" not in block, repr(block))
check("both real threads survive", block.count("- ") == 2, repr(block))

check("an all-junk file yields an empty block, not a list of placeholders",
      _extract_recent_threads("Next: <the single most promising thread to pursue next>") == "")

print("\n=== the prompt that caused it ===")

check("the angle-bracket placeholder is gone", "<the single most promising" not in _DISTILL_SYSTEM)
check("no longer says the last line must be copied 'exactly'",
      "Then a final line, exactly:" not in _DISTILL_SYSTEM)
check("gives a concrete example instead", "Next: whether dental insurance" in _DISTILL_SYSTEM)
check("tells it not to copy the instruction back", "Do not copy this instruction back" in _DISTILL_SYSTEM)

print("\n=== the other two rules the run violated ===")

check("vendor figures must be labelled rather than silently dropped",
      "(vendor claim)" in _DISTILL_SYSTEM)
check("inferred numbers are banned by name",
      "is inferred from common market rates" in _DISTILL_SYSTEM,
      "the exact sentence it produced should appear as a prohibition")
check("saying you inferred it is explicitly not an excuse",
      "does not make it acceptable" in _DISTILL_SYSTEM)

print(f"\n  {_passed} passed, {_failed} failed")
sys.exit(1 if _failed else 0)
