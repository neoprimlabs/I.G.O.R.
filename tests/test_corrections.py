"""A.2 capture: does anything actually reach corrections.md?

Run: python tests/test_corrections.py   (repo root, stdlib only, no API calls)

Measured 2026-09-16 against the real context.db: 73 user messages between
2026-08-08 and 2026-09-14, and **zero** matched `_looks_like_correction`. The
detection is not broken - it scores 7/7 on explicit corrections with no false
positives - but this user does not correct IGOR in those words. The corrections
that did happen were soft:

    "Do it later.. around 11:40"        (IGOR had picked 09:15)
    "Can I get a message around 11:40am?"  (IGOR had just edited a config file)

Both are reformulations: the user restating a request straight after IGOR acted.
Reformulation is a recognised implicit dissatisfaction signal in the IR and
conversational-AI literature, and it is also known to be noisy, so it is captured
under its own label and never mixed with explicit corrections. A.2 is capture-only -
nothing reads this file - so noise costs review time, while an empty corpus costs
V.1 entirely.
"""

import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import config
import orchestrator

_failures: list[str] = []


def _check(name: str, condition: bool, detail: str = "") -> None:
    if condition:
        print(f"  pass  {name}")
    else:
        print(f"  FAIL  {name}  {detail}")
        _failures.append(name)


async def _noop(_: str) -> None:
    return None


def _orch(context):
    config.MEMORY_DIR = Path(tempfile.mkdtemp())
    orch = orchestrator.Orchestrator(notify=_noop)
    orch._context = list(context)
    return orch, config.MEMORY_DIR / "corrections.md"


ANSWERED = [
    {"role": "user", "content": "Will you message me at some random times tomorrow?"},
    {"role": "assistant", "content": "Your random check-in for tomorrow is set. I'll message you at 09:15 EDT."},
]


def test_reformulation_detection() -> None:
    reformulations = [
        ("Do it later.. around 11:40", 18),
        ("Can I get a message around 11:40am?", 25),
        ("make it the evening instead", 60),
    ]
    for message, gap in reformulations:
        _check(f"restated {gap}s later: {message[:34]}",
               orchestrator._looks_like_reformulation(message, gap), f"gap={gap}")

    ignored = [
        ("Do it later.. around 11:40", 4000),   # hours later, a fresh request
        ("thanks", 20),                          # acknowledgement, not dissatisfaction
        ("ok", 10),
        ("Test confirmed", 30),                  # too short to carry a restated request
    ]
    for message, gap in ignored:
        _check(f"not a reformulation: {message[:30]} at {gap}s",
               not orchestrator._looks_like_reformulation(message, gap), f"gap={gap}")


def test_explicit_corrections_are_labelled_separately() -> None:
    orch, path = _orch(ANSWERED)
    orch._log_correction("no, that's wrong - it goes out at 9", "Direct", signal="explicit")
    text = path.read_text(encoding="utf-8")
    _check("an explicit correction is written", "User corrected:" in text, text[:120])
    _check("and labelled explicit", "Signal: explicit" in text, text[:200])

    orch, path = _orch(ANSWERED)
    orch._log_correction("Do it later.. around 11:40", "ConfigEdit", signal="reformulation")
    text = path.read_text(encoding="utf-8")
    _check("a reformulation is written", "Do it later" in text, text[:200])
    _check("and labelled as the weaker signal", "Signal: reformulation" in text, text[:200])
    _check("with what IGOR had said before it",
           "09:15" in text, text[:300])


def test_nothing_is_logged_without_something_to_correct() -> None:
    orch, path = _orch([{"role": "user", "content": "Do it later.. around 11:40"}])
    orch._log_correction("Do it later.. around 11:40", "ConfigEdit", signal="reformulation")
    _check("no assistant turn means nothing is captured", not path.exists())


def test_the_september_14_sequence() -> None:
    """The exchange that produced the schedule_config incident, in order."""
    orch, path = _orch(ANSWERED)
    captured = []
    for message, gap in [("Do it later.. around 11:40", 18),
                         ("Can I get a message around 11:40am?", 25)]:
        if orchestrator._looks_like_correction(message):
            captured.append(("explicit", message))
        elif orchestrator._looks_like_reformulation(message, gap):
            captured.append(("reformulation", message))
    _check("both messages that night are now captured", len(captured) == 2, str(captured))
    _check("and neither is mistaken for an explicit correction",
           all(kind == "reformulation" for kind, _ in captured), str(captured))


if __name__ == "__main__":
    print("reformulation detection")
    test_reformulation_detection()
    print("\nlabelling")
    test_explicit_corrections_are_labelled_separately()
    print("\nnothing to correct")
    test_nothing_is_logged_without_something_to_correct()
    print("\nthe 2026-09-14 sequence")
    test_the_september_14_sequence()

    if _failures:
        print(f"\n{len(_failures)} FAILED: {', '.join(_failures)}")
        sys.exit(1)
    print("\nall pass")
