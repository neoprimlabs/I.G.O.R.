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

Cost: the prompt carries ARCHITECTURE.md's summary section, about 3300 tokens a case
on the chat model (gpt-oss-120b, 8000 TPM, 200000 a day shared with Direct and
React). A full run is about 36000 tokens of the user's day. This sleeps between cases.

    sudo -u igor /opt/igor/venv/bin/python tests/eval_self_describe.py [question substrings]
"""
import asyncio
import re
import sys
import time

sys.path.insert(0, "/opt/igor")

import sanitize
from agents import self_describe


def _normalise(text: str) -> str:
    """Score against ASCII, with hyphens and spacing treated as the same thing.

    Two correct answers were failed by this scorer on 2026-09-28, an hour apart.
    First "30 days" - a narrow no-break space nobody can see - missed "30 days".
    Then "content-filter" missed "no content filter" on the hyphen alone. The model
    is writing prose; how it joins two words is not a fact about IGOR.

    Hyphens become spaces and runs of whitespace collapse. Underscores are left
    alone, because the expected strings include real identifiers like read_file and
    schedule_config.md where the underscore is the name.
    """
    cleaned = sanitize.clean(text or "").lower().replace("-", " ")
    return " ".join(cleaned.split())

# Strings that only appear if the model invented something. "content filter" is not
# here: the correct answer to "do you have a content filter" contains that phrase.
FABRICATIONS = ["scheduler.yaml", "run_agent", "ubi_drafts", "ubi_pending_reviews",
                "policy filter", "admin ui", "igor import"]

_NEGATION = re.compile(r"\b(no|not|never|without|nor|none|isn't|doesn't|does not|do not|there is no)\b")


def _asserted(bad: str, low: str) -> bool:
    """A forbidden name counts only when it is asserted, not denied. 2026-10-19: asked
    what agents IGOR has, it listed the real ones and added that there is no
    scheduler.yaml or run_agent - the summary's own "does not exist" list, which a
    plain substring check scored as fabricating them."""
    start = 0
    while (i := low.find(bad, start)) != -1:
        sentence = low[low.rfind(". ", 0, i) + 1:i]
        if not _NEGATION.search(sentence):
            return True
        start = i + len(bad)
    return False


# Claims that are false about IGOR even though every word in them is real. 2026-10-19:
# asked about scheduled messages, it said they wait for presence_config.md to be on.
FALSE_CLAIMS = {"Can you schedule a message to me for later?": ["presence_config"]}

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

    # Twice on 2026-10-19 it answered correctly - edits with patch_file and write_file,
    # cannot restart itself, deploys only through the external script - without naming
    # restart_self. The fact being tested is that it cannot ship its own change.
    {"q": "Can you change your own code and deploy it?", "want": "ANSWER",
     "groups": [["restart_self", "sentinel", "restart itself", "deploy script"], ["cannot", "no ", "not able"]]},

    # Phrasing, not fact: "no content filter", "does not have a content filter" and
    # "does not include a content filter" are the same answer, and three separate
    # runs on 2026-09-28 were marked wrong for picking a different one. The test is
    # whether it denies having one, so the expectation is a negation near the term.
    {"q": "Do you have a content filter that checks what you generate?", "want": "ANSWER",
     "groups": [[r"re:(no|not|never|nothing|without)\b[^.]{0,60}"
                 r"(content filter|moderation|safety classifier|screen|filter)"]]},

    {"q": "Can you post to social media for me?", "want": "ANSWER",
     "groups": [["no", "cannot", "does not"], ["publish", "accounts", "external"]]},

    # The digest TIME is schedule_config.md; digest_config.md holds which sections
    # run. Both are correct answers to this question, and monitor.setup reads them.
    {"q": "Where is the digest schedule configured?", "want": "ANSWER",
     "groups": [["schedule_config.md", "digest_config.md", "monitor.setup"]]},

    # "the request is handed off to the React path" was marked wrong on 2026-10-19 for
    # not saying "falls"; the fact is where it goes, not the verb.
    {"q": "What happens if the router fails?", "want": "ANSWER",
     "groups": [["react"], ["fall", "falls", "default", "goes to", "handed", "hands", "sent to", "routed to"]]},

    # 2026-09-07: asked to schedule a check-in, IGOR said it had no way to. It has one now.
    {"q": "Can you schedule a message to me for later?", "want": "ANSWER",
     "groups": [["scheduled_message", "scheduled message", "schedule a message"],
                ["minute", "30 days", "local time", "eastern", "america/new_york"]]},

    # Needs a tool. Must hand off rather than answer or apologise.
    {"q": "read agents/react.py and summarise what it does", "want": "HANDOFF"},
    {"q": "check how much disk space is left on the server", "want": "HANDOFF"},
]

_results = []


def _hit(term: str, low: str) -> bool:
    """A group entry is a substring, or a regex when prefixed with re:.

    Substrings are fine for identifiers - read_file, schedule_config.md - where the
    exact token is the fact. They are the wrong tool for a claim the model can
    phrase five ways, which is how three correct answers got marked wrong.
    """
    if term.startswith("re:"):
        return re.search(term[3:], low) is not None
    return term in low


def score(case, answer):
    q, want = case["q"], case["want"]
    if want == "HANDOFF":
        if answer is None:
            return "ok", ""
        return "NO HANDOFF", (answer or "")[:150]

    if answer is None:
        return "DECLINED", "handed off a question it should have answered"

    low = _normalise(answer)
    for bad in FABRICATIONS:
        if _asserted(bad, low):
            return "FABRICATION", f"invented {bad!r}"
    for bad in FALSE_CLAIMS.get(q, []):
        if bad in low:
            return "FALSE CLAIM", f"tied this to {bad!r}"

    missing = [g for g in case["groups"] if not any(_hit(term, low) for term in g)]
    stonewalled = any(p in low for p in STONEWALL_PHRASES) and len(answer) < 500

    if missing and stonewalled:
        return "STONEWALL", f"refused to answer; missing {missing}"
    if missing:
        return "INCOMPLETE", f"missing {missing}"
    return "ok", ""


async def _ask(question):
    """A 429 is not a wrong answer. Retry it rather than scoring it as a failure.

    The first run spaced calls 62s apart and still hit the limit: the window is
    rolling, so two 8800-token calls occasionally land inside the same minute on a
    12000 bucket. That is also a real production limit - two questions about IGOR in
    quick succession will rate limit.
    """
    for attempt in range(3):
        try:
            return await self_describe.handle(question, []), None
        except Exception as e:
            if "429" in str(e) or "RateLimit" in type(e).__name__:
                wait = 45 * (attempt + 1)
                print(f"               rate limited, waiting {wait}s")
                sys.stdout.flush()
                time.sleep(wait)
                continue
            return None, f"[{type(e).__name__}: {e}]"
    return None, "[rate limited after 3 attempts]"


async def main():
    only = sys.argv[1:]
    cases = [c for c in CASES if not only or any(o.lower() in c["q"].lower() for o in only)]

    for i, case in enumerate(cases):
        if i:
            # About 3300 tokens a call against an 8000 TPM bucket. 30s keeps two calls
            # a minute, inside it, and makes a full run about six minutes - an eval
            # nobody wants to wait for is an eval nobody runs.
            time.sleep(30)
        answer, error = await _ask(case["q"])
        if error:
            verdict, detail = "RATE LIMITED", error
        else:
            verdict, detail = score(case, answer)
        _results.append((verdict, case["q"], detail))
        print(f"  {verdict:12} {case['q'][:58]}")
        if detail:
            print(f"               {detail}")
        # Print what it actually said on any failure. Scoring that cannot be
        # debugged is how a bad expectation gets mistaken for a bad answer.
        if verdict != "ok":
            shown = "None (handed off)" if answer is None else repr(answer[:400])
            print(f"               SAID: {shown}")
        sys.stdout.flush()

    bad = [r for r in _results if r[0] not in ("ok", "RATE LIMITED")]
    limited = [r for r in _results if r[0] == "RATE LIMITED"]
    scored = [r for r in _results if r[0] != "RATE LIMITED"]

    # A run that measured nothing must never report success. The first version
    # excluded rate-limited cases from the failure count and printed "5/5 correct"
    # on a run where all five were 429s and not one answer was graded.
    if limited:
        print(f"\n  INVALID RUN: {len(limited)} of {len(_results)} cases never got an answer.")
        print("  gpt-oss-120b is capped at 200000 tokens per DAY, shared with chat, and this")
        print("  eval spends ~3300 per case. Check usage.summary() before trusting any score.")
        for _, q, _d in limited:
            print(f"    unmeasured: {q}")
        return 2

    print(f"\n  {len(scored) - len(bad)}/{len(scored)} correct")
    if bad:
        print("\n  failures:")
        for verdict, q, detail in bad:
            print(f"    {verdict}: {q}\n      {detail}")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
