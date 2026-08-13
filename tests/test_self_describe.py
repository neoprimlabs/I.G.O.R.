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
full = (config.BASE_DIR / "ARCHITECTURE.md").read_text(encoding="utf-8")

check("the summary marker is present in ARCHITECTURE.md",
      self_describe._SUMMARY_MARKER in full,
      "without it _load_document silently falls back to a char count")
check("the summary loads and is a summary, not the whole file",
      2000 < len(doc) < 5000, f"got {len(doc)} of {len(full)} chars")

prompt = self_describe._SYSTEM_PROMPT.format(document=doc)

# Deep sections must NOT be here. Sending the whole 27KB file cost 8539 tokens a
# question against a 100000 token DAILY cap on llama-3.3-70b, shared with Direct,
# ConfigEdit and the evaluator - about eleven questions a day before the rest of
# IGOR was locked out. Detail is React's job, via read_file offsets.
for deep in ["_trim_to_budget", "Prompt injection defence"]:
    check(f"deep detail is left to React, not sent every call: {deep}", deep not in prompt)

for denial in ["No content filter", "No `scheduler.yaml`", "run_agent()"]:
    check(f"prompt denies the fabrication: {denial}", denial in prompt)

check("told to decline tool-requiring tasks", "NOT_ABOUT_IGOR" in prompt)
check("told the document beats the conversation", "The document wins." in prompt)
check("told never to invent a component", "NEVER name a file, module" in prompt)

# The proven mitigation for hallucination is a credited abstention path, not more
# grounding: under binary scoring a confident guess always beats "I don't know"
# (arXiv 2509.04664). An earlier version of this prompt said "when in doubt, answer",
# which instructed the model to fabricate.
check("grounding is scoped to claims, not to the question",
      "Grounding applies to your CLAIMS, not to the question" in prompt)
check("forbidden from replying that the doc does not mention the topic",
      "Do not reply that the document does not mention their topic" in prompt)
check("told to reason to a conclusion, including a clear no",
      "including a clear no and what it would actually take" in prompt)
check("guessing is named as the failure, not silence", "Guessing is the failure." in prompt)
check("not scored on answering", "You are not scored on answering" in prompt)
check("the 'when in doubt, answer' instruction is gone", "When in doubt, answer" not in prompt)
check("hands off rather than apologising when it needs the source",
      "hands the message straight to an agent that has the tools" in prompt)
check("does not tell the user to go read it themselves",
      "do NOT tell the user to go and read it themselves" in prompt)

# 3.47 chars/token is measured, not assumed: a 29616 char prompt was reported as
# 8539 tokens by the API on 2026-08-13. The old chars/4 estimate said 7404 and hid
# the problem entirely.
_CHARS_PER_TOKEN = 3.47
prompt_tokens = len(prompt) / _CHARS_PER_TOKEN
history = orchestrator._CONTEXT_BUDGET_SELF / _CHARS_PER_TOKEN
total = prompt_tokens + history + 1024

# The check that was missing. llama-3.3-70b allows 100000 tokens per DAY across
# Direct, ConfigEdit, the evaluator and this agent, so a fixed prompt sent on every
# call has to leave room for all of them.
per_day = 100000 / total
check("leaves a usable number of questions in the daily budget", per_day >= 25,
      f"only {per_day:.0f} questions/day at {total:.0f} tokens each")
print(f"        {total:.0f} tokens per question, about {per_day:.0f} per 100k day")
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
