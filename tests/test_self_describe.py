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


print("=== routing detection ===")

# The three Discord messages that actually produced fabrications.
for msg in [
    "We could have you use your autonomous scheduled system to promote and inform.",
    "How can we ustilize your autonomous scheduled system?",
    "How can we utilize your autonomous scheduled system?",
]:
    check(f"routes the real failing message: {msg[:40]}...",
          self_describe.looks_self_referential(msg))

for msg in ["What agents do you have?", "what tools can you use", "how does IGOR routing work",
            "explain your memory system", "can you change your own code"]:
    check(f"routes: {msg[:38]}", self_describe.looks_self_referential(msg), msg)

for msg in ["The world is in need of a major change.", "search for recent UBI pilot results",
            "what is the weather tomorrow", "How do you feel about these headlines?"]:
    check(f"leaves alone: {msg[:38]}", not self_describe.looks_self_referential(msg), msg)

print("\n=== the prompt it builds ===")

doc = self_describe._load_document()
check("ARCHITECTURE.md loads", len(doc) > 20000, f"got {len(doc)} chars")

prompt = self_describe._SYSTEM_PROMPT.format(document=doc)

# The whole document must fit, unlike React's 4000-char tool result.
for deep in ["_trim_to_budget", "Prompt injection defence"]:
    check(f"the document reaches the end, not just the head: {deep}", deep in prompt)

for denial in ["No content filter", "No `scheduler.yaml`", "run_agent()"]:
    check(f"prompt denies the fabrication: {denial}", denial in prompt)

check("told to decline tasks", "NOT_ABOUT_IGOR" in prompt)
check("told the document beats the conversation", "The document wins." in prompt)
check("told never to invent a component", "NEVER describe a file, module" in prompt)

# The arithmetic that React failed. 12000 TPM bucket, 1024 max_tokens.
prompt_tokens = len(prompt) / 4
history = 8000 / 4
total = prompt_tokens + history + 1024
check("prompt + history + max_tokens fits the 12000 bucket", total < 11000,
      f"estimated {int(total)} tokens")
print(f"        estimated {int(prompt_tokens)} prompt + {int(history)} history + 1024 out = {int(total)} of 12000")

print("\n=== orchestrator wiring ===")

import orchestrator
src = open(config.BASE_DIR / "orchestrator.py", encoding="utf-8").read()
check("fast path routes to SelfDescribe", 'return "SelfDescribe"' in src)
check("declining falls through to React", "SelfDescribe declined the message, forwarding to React" in src)
check("uses its own context budget, not Direct's", "_CONTEXT_BUDGET_SELF" in src)

react_src = open(config.BASE_DIR / "agents" / "react.py", encoding="utf-8").read()
check("the harmful grounding injection is gone from react",
      "_ground_if_self_referential" not in react_src)

print(f"\n  {_passed} passed, {_failed} failed")
sys.exit(1 if _failed else 0)
