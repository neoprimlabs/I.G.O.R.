"""Approving and rejecting proposals is done by code, not the model.

Run: python tests/test_proposal_commands.py   (repo root, no API calls)

Live 2026-10-17 the user typed "Reject proposal P1 Reject proposal P2". React called
reject with "P1" (ids were matched case-sensitively, so nothing happened), then
`list` (an untrusted read, which closed the tool), then replied that both were
rejected. Neither was. The user's typed decision is the trust boundary, so it is
parsed and carried out in code, and the reply is the tool's own words.
"""

import asyncio
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import config

config.MEMORY_DIR = Path(tempfile.mkdtemp())

_failures: list[str] = []


def _check(name: str, condition: bool, detail: str = "") -> None:
    print(f"  {'pass' if condition else 'FAIL'}  {name}" + ("" if condition else f"  {detail}"))
    if not condition:
        _failures.append(name)


def test_parsing() -> None:
    import orchestrator
    p = orchestrator._proposal_decisions
    _check("the exact 10-17 message", p("Reject proposal P1 Reject proposal P2") ==
           [("p1", False), ("p2", False)], str(p("Reject proposal P1 Reject proposal P2")))
    _check("one verb, several ids", p("reject p1 and p2") == [("p1", False), ("p2", False)])
    _check("mixed verbs", p("approve P3, reject p4") == [("p3", True), ("p4", False)],
           str(p("approve P3, reject p4")))
    _check("accept counts as approve", p("accept p7") == [("p7", True)])
    _check("a repeated id is decided once", p("reject p1 p1") == [("p1", False)])
    for text in ("should I approve p1?", "what is p1 about", "I decline to say",
                 "approve the plan", "reject the p2p idea", "x " * 150 + "reject p1"):
        _check(f"not a decision: {text[:40]!r}", p(text) == [], str(p(text)))


def test_ids_ignore_case() -> None:
    import goals
    import proposals
    proposals.add("code", "problem", "evidence", "change", "test")
    _check("decide accepts an upper-case id", proposals.decide("P1", False) == "Rejected p1.",
           proposals.decide("P1", False))
    goals.add("A goal", "research")
    _check("goal ids ignore case too", goals.set_status("G1", "paused").startswith("Paused")
           or "paused" in goals.set_status("G1", "paused").lower(), goals.set_status("G1", "paused"))


def test_routed_in_code_without_the_model() -> None:
    import orchestrator
    import proposals
    proposals.add("code", "problem two", "evidence", "change", "test")
    proposals.add("prompt", "problem three", "evidence", "change", "test")
    pending = [x["id"] for x in proposals.pending()]

    async def notify(_):
        pass

    o = orchestrator.Orchestrator(notify)
    asked = []

    async def no_model(*a, **k):
        asked.append(1)
        raise RuntimeError("a model was called")

    o._client.chat.completions.create = no_model
    upper = " ".join(f"Reject proposal {pid.upper()}" for pid in pending)
    destination = asyncio.run(o._classify(upper))
    _check("a typed decision skips the router", destination == "ProposalDecision" and not asked,
           f"{destination} router={bool(asked)}")
    reply = asyncio.run(o._route(destination, upper))
    _check("each proposal is rejected", proposals.pending() == [], str(proposals.pending()))
    _check("the reply is the tool's own words, one line per id",
           all(f"Rejected {pid}." in reply for pid in pending), reply)
    reply = asyncio.run(o._route("ProposalDecision", "reject p99"))
    _check("an unknown id is reported, not claimed", "no pending proposal p99" in reply, reply)


def test_listing_in_code() -> None:
    """2026-10-27 "show me the proposals please" went MONITOR, then Direct, which has
    no tools and listed two proactive drafts as the proposals."""
    import orchestrator
    import proposals
    asks = orchestrator._asks_for_proposals
    for text in ("show me the proposals please", "any proposals?", "proposals",
                 "What proposals are waiting?", "list proposals"):
        _check(f"asks for the list: {text!r}", asks(text))
    for text in ("write a proposal for the n8n gig", "reject p3", "I like that proposal idea"):
        _check(f"not a request for the list: {text!r}", not asks(text))

    async def notify(_):
        pass

    o = orchestrator.Orchestrator(notify)
    asked = []

    async def no_model(*a, **k):
        asked.append(1)
        raise RuntimeError("a model was called")

    o._client.chat.completions.create = no_model
    pid = proposals.add("prompt", "Remote jobs were missed", "evidence", "List remote ones.", "t")
    destination = asyncio.run(o._classify("show me the proposals please"))
    reply = asyncio.run(o._route(destination, "show me the proposals please"))
    _check("listed in code, no model", destination == "ProposalList" and not asked, destination)
    _check("short: problem, change and how to answer",
           f"{pid} (prompt): Remote jobs were missed" in reply and "List remote ones." in reply
           and f'"approve {pid}" or "reject {pid}"' in reply and "evidence" not in reply, reply)
    message = f"show me proposals again and reject {pid}"
    reply = asyncio.run(o._route(asyncio.run(o._classify(message)), message))
    _check("asking for both decides, then shows what is left",
           reply.startswith(f"Rejected {pid}.") and "No proposals are waiting for you." in reply, reply)


if __name__ == "__main__":
    test_parsing()
    test_ids_ignore_case()
    test_routed_in_code_without_the_model()
    test_listing_in_code()
    if _failures:
        print(f"\n{len(_failures)} FAILED: {', '.join(_failures)}")
        sys.exit(1)
    print("\nall pass")
