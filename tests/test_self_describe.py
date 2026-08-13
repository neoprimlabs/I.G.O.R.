"""SelfDescribe: questions about IGOR go somewhere with room to hold the answer.

Regression cover for 2026-08-13, three failed fixes in one day. React fabricated a
scheduler.yaml, a run_agent() helper and a content filter. Paging read_file did not
fix it. Injecting ARCHITECTURE.md into React's prompt made it worse - it blew the
7000 ceiling, so React forced a final answer at iteration 1 with no tools, which
guaranteed the fabrication. The budget was the bug.
"""
import sys

sys.path.insert(0, r"c:\Dev\IGOR")

import config
import orchestrator
from agents import self_describe

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


print("=== the prompt it builds ===")

doc = self_describe._load_document()
check("ARCHITECTURE.md loads", len(doc) > 20000, f"got {len(doc)} chars")

prompt = self_describe._SYSTEM_PROMPT.format(document=doc)

# The whole document must fit, unlike React's 4000-char tool result.
for deep in ["_trim_to_budget", "Prompt injection defence"]:
    check(f"the document reaches the end, not just the head: {deep}", deep in prompt)

for denial in ["No content filter", "No `scheduler.yaml`", "run_agent()"]:
    check(f"prompt denies the fabrication: {denial}", denial in prompt)

check("told to decline tool-requiring tasks", "NOT_ABOUT_IGOR" in prompt)
check("told the document beats the conversation", "The document wins." in prompt)
check("told never to invent a component", "NEVER name a file, module" in prompt)

# The proven mitigation for hallucination is a credited abstention path, not more
# grounding: under binary scoring a confident guess always beats "I don't know"
# (arXiv 2509.04664). An earlier version of this prompt said "when in doubt, answer",
# which instructed the model to fabricate.
check("abstention is stated to be a correct answer",
      "is a CORRECT and complete answer" in prompt)
check("guessing is named as the failure, not silence", "Guessing is the failure." in prompt)
check("not scored on answering", "You are not scored on answering" in prompt)
check("the 'when in doubt, answer' instruction is gone", "When in doubt, answer" not in prompt)
check("hands off rather than apologising when it needs the source",
      "hands the message to an agent that has the tools" in prompt)
check("does not tell the user to go read it themselves",
      "do NOT tell the user to go and read it themselves" in prompt)

# The arithmetic that React failed. 12000 TPM bucket, 1024 max_tokens.
prompt_tokens = len(prompt) / 4
history = orchestrator._CONTEXT_BUDGET_SELF / 4
total = prompt_tokens + history + 1024
check("prompt + history + max_tokens fits the 12000 bucket", total < 11000,
      f"estimated {int(total)} tokens")
print(f"        estimated {int(prompt_tokens)} prompt + {int(history)} history + 1024 out = {int(total)} of 12000")

print("\n=== orchestrator wiring ===")

import orchestrator
src = open(config.BASE_DIR / "orchestrator.py", encoding="utf-8").read()
check("the router owns the decision, via a SELF verdict", '"SELF": "SelfDescribe"' in src)
check("the router prompt teaches the tool-naming distinction",
      'Use your shell tool to check disk space" is TASK' in src)
check("no regex fast path survives", "looks_self_referential" not in src)
check("declining falls through to React", "SelfDescribe declined the message, forwarding to React" in src)
check("uses its own context budget, not Direct's", "_CONTEXT_BUDGET_SELF" in src)
check("history is cut to near nothing", "_CONTEXT_BUDGET_SELF = 1500" in src)

react_src = open(config.BASE_DIR / "agents" / "react.py", encoding="utf-8").read()
check("the harmful grounding injection is gone from react",
      "_ground_if_self_referential" not in react_src)

print(f"\n  {_passed} passed, {_failed} failed")
sys.exit(1 if _failed else 0)
