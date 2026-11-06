"""Tests for agents/evaluator.py: what the evaluator model is shown, and how its verdict is read.

Run: python tests/test_evaluator.py   (from the repo root, no pytest, no network)

Until 2026-11-06 a response over 6000 characters reached the model as its first 4000 and
last 2000, cut mid-sentence. Every FAIL in the month before was a complete overnight paper
rejected as "cut off mid-sentence" or "missing its shortlist", which sat in the cut middle.
"""

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import llm
from agents import evaluator

_failures: list[str] = []


def _check(name: str, condition: bool, detail: str = "") -> None:
    if condition:
        print(f"  pass  {name}")
    else:
        print(f"  FAIL  {name}  {detail}")
        _failures.append(name)


def _paper(sections: int) -> str:
    parts = ["# Ways a kettle shop could earn online\n"]
    for i in range(sections):
        parts.append(f"## Idea {i + 1}: kettle descaling subscriptions, region {i + 1}\n"
                     + "".join(f"Shops in region {i + 1} report {j + 3} orders a week at 12 dollars each "
                               f"<https://example.com/kettles/{i}/{j}>. " for j in range(6)) + "\n")
    parts.append("## Ranked shortlist\n1. Idea 1 - most orders\n2. Idea 2 - steady\n")
    parts.append("## What I am unsure about\n- Whether the order counts are typical.\n")
    return "\n".join(parts)


def _run(response: str, verdict: str = "PASS", error: Exception = None):
    seen = {}

    async def fake(client, model, system, messages, **kwargs):
        seen.update(model=model, system=system, body=messages[0]["content"], kwargs=kwargs)
        if error:
            raise error
        return verdict

    real_complete, real_client = llm.complete, evaluator._get_client
    llm.complete, evaluator._get_client = fake, lambda: None
    try:
        result = asyncio.run(evaluator.evaluate("Write a research paper on kettles.", response))
    finally:
        llm.complete, evaluator._get_client = real_complete, real_client
    return result, seen


def test_what_the_model_is_shown() -> None:
    paper = _paper(11)
    _check("the fixture is the size of a real overnight paper", 6000 < len(paper) < 8000, str(len(paper)))
    _, seen = _run(paper)
    _check("a paper of that size is shown whole", paper in seen["body"] and "left out" not in seen["body"])

    long = _paper(40)
    _check("the long fixture is over the limit", len(long) > evaluator._WHOLE_CHARS, str(len(long)))
    shown = evaluator._shown(long)
    head, _, rest = shown.partition("\n\n[The evaluation harness left out ")
    count, _, tail = rest.partition(" characters here.")
    tail = tail.split("]\n\n", 1)[1]
    _check("the beginning stops at the end of a line", long.startswith(head) and long[len(head)] == "\n",
           repr(head[-40:]))
    _check("the ending starts at the start of a line", long.endswith(tail) and long[-len(tail) - 1] == "\n",
           repr(tail[:40]))
    _check("the note counts what was left out", int(count) == len(long) - len(head) - len(tail), count)
    _check("the ending keeps the shortlist and the unsure section",
           "## Ranked shortlist" in tail and "## What I am unsure about" in tail)
    _check("what is shown stays near the limit", len(shown) < evaluator._HEAD_CHARS + evaluator._TAIL_CHARS + 200,
           str(len(shown)))

    one_line = "kettle " * 4000
    shown = evaluator._shown(one_line)
    _check("a response with no line breaks is still shown, cut where it must be",
           len(shown) > evaluator._HEAD_CHARS and "left out" in shown, str(len(shown)))

    system = evaluator._DEFAULT_SYSTEM_PROMPT
    tokens = (len(system) + 1500 + evaluator._WHOLE_CHARS + 200) / 3 + seen["kwargs"]["max_tokens"]
    _check("the largest call fits the evaluator model's 8000 a minute at 3 characters a token",
           tokens < 8000, f"{tokens:.0f}")


def test_verdicts() -> None:
    (passed, feedback), _ = _run("A short answer.", "PASS")
    _check("PASS passes", passed and feedback == "")
    (passed, feedback), _ = _run("A short answer.", "FAIL: the shortlist is missing\nmore text")
    _check("FAIL fails with its reason", not passed and feedback == "the shortlist is missing", feedback)
    (passed, _), _ = _run("A short answer.", "Looks fine to me")
    _check("an unreadable verdict fails open", passed)
    (passed, _), _ = _run("A short answer.", error=RuntimeError("429"))
    _check("an error fails open", passed)


if __name__ == "__main__":
    print("what the evaluator model is shown")
    test_what_the_model_is_shown()
    print("\nhow its verdict is read")
    test_verdicts()

    if _failures:
        print(f"\n{len(_failures)} FAILED: {', '.join(_failures)}")
        sys.exit(1)
    print("\nall pass")
