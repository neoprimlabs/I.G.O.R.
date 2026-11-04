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

        seen.clear()
        direct.handle = answering
        reply, _ = asyncio.run(o.process(42, "How big was that kettle again?"))
        _check("an ordinary chat answer passes through", reply.startswith("The kettle"), reply)
        _check("without calling React", "react_message" not in seen)
        _check("and stays Direct's", o._last_route == "Direct", o._last_route)

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
