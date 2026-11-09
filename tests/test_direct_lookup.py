"""Direct hands a question about the world on to React when it may not have the facts.

2026-11-04, from Discord: "What are people using Hermes for?" routed CHAT, and Direct, which
has no search, answered from the model's memory of Nous' Hermes language models rather than
Hermes Agent, the one the user meant. Hermes' own agent would have searched. Direct's whole
reply is now LOOKUP when the answer turns on a product, company or event it may not know,
and the orchestrator sends the message to React, which records it as React's turn.

Run: python tests/test_direct_lookup.py   (repo root, stdlib only, no API calls)
"""

import asyncio
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import config
import context_store

_failures: list[str] = []


def _check(name: str, condition: bool, detail: str = "") -> None:
    if condition:
        print(f"  pass  {name}")
    else:
        print(f"  FAIL  {name}  {detail}")
        _failures.append(name)


def test_the_word() -> None:
    from agents import direct
    _check("the bare word hands on", direct.wants_lookup("LOOKUP"))
    _check("with stray punctuation too", direct.wants_lookup(" LOOKUP.\n"))
    _check("a sentence that uses the word does not",
           not direct.wants_lookup("LOOKUP is what I would do, but the kettle is in the conversation."))
    _check("nor does an empty reply", not direct.wants_lookup(""))
    _check("the prompt teaches it", "your entire reply is the single word LOOKUP" in direct._DEFAULT_SYSTEM_PROMPT)


def test_igor_records_are_handed_on() -> None:
    """2026-11-09: asked how the watches were doing, Direct offered a report the user would have to ask
    for, then made one up - run times, "no price data has been retrieved" - from an earlier reply that
    said when the next run would be. A question about IGOR's own records is now a LOOKUP, and a reply
    that states them anyway is handed on like one."""
    from agents import direct
    prompt = direct._DEFAULT_SYSTEM_PROMPT
    _check("the prompt hands IGOR's records on", "what its watches and routines have run or found" in prompt
           and "a plan, not a record of what ran" in prompt)
    _check("and no longer offers to have a check run", "offer to have it run" not in prompt)
    _check("a sentence to send is for an action only", "That sentence is for an action only" in prompt)
    for made_up in (
        "Here is the status. The kettle watch last executed at 08:10 on Monday. No price data has been "
        "retrieved yet, so the result set is empty.",
        "Both watches are scheduled to run again every 12 hours.",
        "It checked at 9 this morning and found nothing.",
        "The next run is around noon.",
        "I understand it is hard to judge when no results have been returned yet.",
        "The kettle watch last ran an hour ago.",
        "Request a status snapshot after each execution; if the result set remains empty, the sources "
        "have no match. If you need a command, send “show watch status now”.",
        "The most direct way to gauge it is to request a brief status report.",
        "You could ask me for the logs from that run.",
    ):
        _check(f"handed on: {made_up[:50]!r}", direct.wants_lookup(made_up))
    for ordinary in (
        "The kettle you mentioned holds a litre.",
        "You said you ran at 6 this morning, which is early for a Sunday.",
        "I will raise it at the next check-in if you like.",
        "Nothing in this conversation says when the kettle was bought.",
        "The digest I sent this morning led with the kettle recall.",
        "This reply cannot set it, so send \"remind me at 9pm to descale the kettle\" as its own message.",
        "Your status at the kettle club is the thing to protect.",
    ):
        _check(f"kept: {ordinary[:50]!r}", not direct.wants_lookup(ordinary))


def test_handoff() -> None:
    import orchestrator
    from agents import direct, react
    tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
    real = (config.MEMORY_DIR, context_store._DB_PATH, direct.handle, react.handle,
            config.AUTHORIZED_USER_ID)
    seen = {}

    async def handing_on(message, context, call):
        return "LOOKUP"

    async def answering(message, context, call):
        return "The kettle you mentioned holds a litre."

    async def searching(message, context, max_tokens=1024, carry=None, **kw):
        seen["react_message"] = message
        seen["force_tool"] = kw.get("force_tool")
        return "People use it for scheduled research and coding help."

    async def notify(_):
        pass

    async def as_chat(text):
        return "Direct"

    try:
        config.MEMORY_DIR = Path(tmp.name)
        context_store._DB_PATH = config.MEMORY_DIR / "context.db"
        config.AUTHORIZED_USER_ID = 42
        direct.handle, react.handle = handing_on, searching
        o = orchestrator.Orchestrator(notify)
        o._classify = as_chat

        reply, _ = asyncio.run(o.process(42, "What are people using Hermes for?"))
        _check("a handed-on question is answered by React", reply.startswith("People use it"), reply)
        _check("React gets the user's own words", seen.get("react_message") == "What are people using Hermes for?",
               repr(seen.get("react_message")))
        _check("the turn is recorded as React's", o._last_route == "React", o._last_route)
        _check("so follow-through can continue it", o._pending_follow is not None)
        _check("with its tools free", seen.get("force_tool") is None, repr(seen.get("force_tool")))

        seen.clear()
        direct.handle = answering
        reply, _ = asyncio.run(o.process(42, "How big was that kettle again?"))
        _check("an ordinary chat answer passes through", reply.startswith("The kettle"), reply)
        _check("without calling React", "react_message" not in seen)
        _check("and stays Direct's", o._last_route == "Direct", o._last_route)

        async def making_it_up(message, context, call):
            return "The kettle watch last executed at 08:10. No price data has been retrieved yet."

        seen.clear()
        direct.handle = making_it_up
        reply, _ = asyncio.run(o.process(42, "Why not just give me the status?"))
        _check("a made-up run record never reaches the user", "08:10" not in reply, reply)
        _check("React answers it instead", seen.get("react_message") == "Why not just give me the status?",
               repr(seen.get("react_message")))

        # 2026-11-09: the user's words named no watch; the check-in they answered did.
        async def about_the_watch(message, context, call):
            return "The kettle price watch has been quiet this week."

        direct.handle = about_the_watch
        asyncio.run(o.process(42, "How is the kettle doing?"))
        seen.clear()
        direct.handle = handing_on
        asyncio.run(o.process(42, "You haven't found anything yet, how could I judge that?"))
        _check("a hand-off answering a watch message reads the watches first",
               seen.get("force_tool") == "watch_history", repr(seen.get("force_tool")))
        _check("and is React's turn", o._last_route == "React", o._last_route)

        direct.handle = handing_on
        side = asyncio.run(o._aside("what are people using Hermes for"))
        _check("a side question that needs a search says it waits", "needs a search" in side, side)
    finally:
        (config.MEMORY_DIR, context_store._DB_PATH, direct.handle, react.handle,
         config.AUTHORIZED_USER_ID) = real
        tmp.cleanup()


def test_the_router() -> None:
    import orchestrator
    _check("comparing IGOR with another agent is SELF",
           '"How do you stack up against Hermes?" is SELF' in orchestrator._ROUTER_PROMPT)
    _check("the rule count still matches", "Eight rules that override the above" in orchestrator._ROUTER_PROMPT)


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            print(name)
            fn()
    if _failures:
        print(f"\n{len(_failures)} FAILED: {', '.join(_failures)}")
        sys.exit(1)
    print("\nall pass")
