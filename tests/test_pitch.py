"""Gig pitches: drafted on request, assembled by code, claims about the user only from approved lines.

Run: python tests/test_pitch.py   (repo root, stdlib only, no API calls)
"""

import asyncio
import json
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import config

_failures: list[str] = []


def _check(name: str, condition: bool, detail: str = "") -> None:
    print(f"  {'pass' if condition else 'FAIL'}  {name}" + ("" if condition else f"  {detail}"))
    if not condition:
        _failures.append(name)


NOW = datetime(2026, 11, 7, 17, 0, tzinfo=timezone.utc)
GIG = {"title": "[Hiring] Kettle shop needs an order-alert automation, $300 fixed", "source": "reddit",
       "url": "https://www.reddit.com/r/forhire/comments/k1/kettle/",
       "body": "We sell kettles online. When an order comes in we want a message in our team chat with the "
               "items and the address, and a weekly summary every Monday. We use a common shop platform. "
               "Budget $300 fixed."}
FACTS = "# Approved lines\n\n- I built and run a kettle-tracking assistant in Python.\n- I test automations with scored checks.\n"
GOOD = ("NEED: You need a message in your team chat for each new order and a summary every Monday.\n"
        "STEP: Connect the shop platform's order events to the team chat.\n"
        "STEP: Format each message with the items and the address.\n"
        "STEP: Schedule the Monday summary.\n"
        "FACT: 1\n"
        "QUESTION: Which team chat do you use?")


def _fresh(facts=FACTS, gigs=(GIG,)):
    config.MEMORY_DIR = Path(tempfile.mkdtemp())
    (config.MEMORY_DIR / "private").mkdir()
    if facts is not None:
        (config.MEMORY_DIR / "private" / "pitch_facts.md").write_text(facts, encoding="utf-8")
    (config.MEMORY_DIR / "private" / "profile.md").write_text("Builds kettle automations in Python.\n", encoding="utf-8")
    from agents import pitch
    if gigs:
        pitch.remember(list(gigs), NOW)
    return pitch


class _Ask:
    def __init__(self, *replies):
        self.replies, self.prompts = list(replies), []

    async def __call__(self, system, user):
        self.prompts.append(user)
        reply = self.replies[min(len(self.prompts), len(self.replies)) - 1]
        if isinstance(reply, Exception):
            raise reply
        return reply


def _run(pitch, text, ask):
    return asyncio.run(pitch.command(text, ask=ask, now=NOW))


def test_commands() -> None:
    from agents import pitch
    for text, want in (("pitch 1", ("pitch", 1)), ("Pitch 2.", ("pitch", 2)), ("draft a pitch for 3", ("pitch", 3)),
                       ("/pitch #1", ("pitch", 1)), ("sent 1", ("sent", 1)), ("I sent 2", ("sent", 2)),
                       ("pitched gig 1", ("sent", 1))):
        _check(f"{text!r} is {want}", pitch.parse(text) == want, str(pitch.parse(text)))
    for text in ("pitch me an idea for the kettle shop", "send 1 as a PDF", "sent it", "what is a pitch 1 deck"):
        _check(f"{text!r} is not a pitch command", pitch.parse(text) is None, str(pitch.parse(text)))


def test_a_pitch_is_assembled_from_its_parts() -> None:
    pitch = _fresh()
    ask = _Ask(GOOD)
    reply = _run(pitch, "pitch 1", ask)
    _check("one model call", len(ask.prompts) == 1, str(len(ask.prompts)))
    _check("it names the gig and its link", "Pitch for 1: [Hiring] Kettle shop" in reply and GIG["url"] in reply, reply)
    _check("the need comes first", "Hi, I read your post. You need a message" in reply, reply)
    _check("the steps are listed", "- Connect the shop platform's order events to the team chat." in reply, reply)
    _check("the approved line is copied word for word", "I built and run a kettle-tracking assistant in Python." in reply)
    _check("and only the one chosen", "scored checks" not in reply, reply)
    _check("it ends with the question and the next step", "Which team chat do you use?" in reply
           and "Say sent 1 after you send it." in reply, reply)
    _check("the model saw the post and the numbered lines",
           "Budget $300 fixed." in ask.prompts[0] and "1. I built and run" in ask.prompts[0], ask.prompts[0])


def test_the_model_never_describes_the_user() -> None:
    pitch = _fresh()
    bragging = GOOD.replace("STEP: Schedule the Monday summary.",
                            "STEP: I have built dozens of these with years of experience.")
    ask = _Ask(bragging, GOOD)
    reply = _run(pitch, "pitch 1", ask)
    _check("a claim in the model's words is refused and retried", len(ask.prompts) == 2, str(len(ask.prompts)))
    _check("the retry is told why", "describes the person" in ask.prompts[1] and "I have" in ask.prompts[1], ask.prompts[1])
    _check("and the clean draft is used", "dozens" not in reply and "Which team chat" in reply, reply)
    ask = _Ask(bragging, bragging)
    reply = _run(pitch, "pitch 1", ask)
    _check("refused twice, nothing is drafted", reply.startswith("I could not draft a pitch for 1") and "dozens" not in reply,
           reply)


def test_no_figures_the_post_does_not_give() -> None:
    pitch = _fresh()
    priced = GOOD.replace("STEP: Schedule the Monday summary.", "STEP: Deliver it in 3 days for $250.")
    ask = _Ask(priced, GOOD)
    _run(pitch, "pitch 1", ask)
    _check("an invented price and deadline are refused", len(ask.prompts) == 2 and "$250" in ask.prompts[1]
           and "3 days" in ask.prompts[1], ask.prompts[-1])
    stated = GOOD.replace("STEP: Schedule the Monday summary.", "STEP: Build it within the $300 fixed budget.")
    ask = _Ask(stated)
    reply = _run(pitch, "pitch 1", ask)
    _check("a figure the post states is allowed", len(ask.prompts) == 1 and "$300" in reply, reply)
    linked = GOOD.replace("STEP: Schedule the Monday summary.", "STEP: See https://kettle.example/demo for a demo.")
    ask = _Ask(linked, GOOD)
    _run(pitch, "pitch 1", ask)
    _check("an added link is refused", len(ask.prompts) == 2 and "link" in ask.prompts[1], ask.prompts[-1])


def test_shape_and_style() -> None:
    pitch = _fresh()
    loud = (GOOD.replace("Which team chat do you use?", "Which team chat do you use?")
            .replace("NEED: You need", "NEED: **You need").replace("summary every Monday.", "summary every Monday!")
            .replace("Format each", "Format each — cleanly —"))
    reply = _run(pitch, "pitch 1", _Ask(loud))
    _check("no exclamation points, long dashes or bold marks", "!" not in reply and "—" not in reply
           and "**" not in reply, reply)
    for broken, why in ((GOOD.replace("FACT: 1", "FACT: 7"), "FACT must be"),
                        (GOOD.replace("QUESTION: Which team chat do you use?", "QUESTION: Tell me more."), "question mark"),
                        ("\n".join(l for l in GOOD.splitlines() if not l.startswith("STEP: Format")
                                   and not l.startswith("STEP: Schedule")), "two or three STEP")):
        ask = _Ask(broken, GOOD)
        _run(pitch, "pitch 1", ask)
        _check(f"a reply where {why!r} is retried", len(ask.prompts) == 2 and why in ask.prompts[1], ask.prompts[-1])
    reply = _run(pitch, "pitch 1", _Ask(GOOD.replace("FACT: 1", "FACT: 0")))
    _check("no approved line fitting is said, and none is made up", "None of your approved lines fit" in reply
           and "kettle-tracking" not in reply, reply)


def test_what_it_refuses_to_do() -> None:
    pitch = _fresh(facts=None)
    ask = _Ask(GOOD)
    reply = _run(pitch, "pitch 1", ask)
    _check("with no approved lines it is off, with no model call", "not set up yet" in reply and not ask.prompts, reply)
    pitch = _fresh(gigs=())
    _check("with no gig message it says so", "no gig message" in _run(pitch, "pitch 1", _Ask(GOOD)))
    pitch = _fresh()
    _check("a number past the list is named", _run(pitch, "pitch 4", _Ask(GOOD)) == "The last gig message had 1 gig.")
    reply = _run(pitch, "pitch 1", _Ask(RuntimeError("429")))
    _check("a failed call says so and drafts nothing", "could not draft" in reply and "RuntimeError" in reply, reply)


def test_sent_is_logged() -> None:
    pitch = _fresh()
    reply = _run(pitch, "sent 1", _Ask(GOOD))
    log = (config.MEMORY_DIR / "pitch_log.md").read_text(encoding="utf-8")
    _check("sent marks it pitched", reply == f"Marked as pitched: {GIG['title']}.", reply)
    _check("in the pitch log, with its link", "2026-11-07 [Hiring] Kettle shop" in log and GIG["url"] in log, log)
    _run(pitch, "sent 1", _Ask(GOOD))
    _check("a second one adds a line, not a second header",
           log.count("# Pitches sent") == 1 and (config.MEMORY_DIR / "pitch_log.md").read_text(encoding="utf-8")
           .count("Kettle shop") == 2)


def test_the_gig_watch_saves_what_it_sent() -> None:
    from agents import gigs
    pitch = _fresh(gigs=())
    sent = []

    async def send(text):
        sent.append(text)
        return True

    found = [dict(GIG, created="2026-11-07T10:00:00Z")]
    real = gigs.collect
    try:
        gigs.collect = lambda now, fetchers, topic_fetch: (found, {"reddit": "1 posts"})

        async def ask(profile, listing):
            return "1."
        asyncio.run(gigs.run(send, now=NOW, ask=ask))
    finally:
        gigs.collect = real
    at, kept = pitch._gigs()
    _check("the gigs in the message are kept, in its order", [g["url"] for g in kept] == [GIG["url"]]
           and kept[0]["body"] == GIG["body"], json.dumps(kept)[:300])
    _check("and pitch 1 is the first of them", "Pitch for 1: [Hiring] Kettle shop" in _run(pitch, "pitch 1", _Ask(GOOD)))


def test_the_route() -> None:
    import orchestrator
    o = orchestrator.Orchestrator.__new__(orchestrator.Orchestrator)
    got = asyncio.run(o._classify("pitch 2"))
    _check("pitch 2 is handled in code", got == "Pitch", got)


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            print(name)
            fn()
    if _failures:
        print(f"\n{len(_failures)} FAILED: {', '.join(_failures)}")
        sys.exit(1)
    print("\nall pass")
