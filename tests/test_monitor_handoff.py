"""Monitor hands a message that is not about IGOR's status on to Direct.

2026-10-21: "what did I tell you about my work schedule this week?" routed MONITOR on
the word "schedule"; Monitor, which sees no conversation, said nothing was stored.

Run: python tests/test_monitor_handoff.py   (repo root, stdlib only, no API calls)
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


def test_handoff() -> None:
    import orchestrator
    from agents import direct, monitor
    tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
    real = (config.MEMORY_DIR, context_store._DB_PATH, monitor.handle, direct.handle,
            config.AUTHORIZED_USER_ID)
    asked = {}

    async def declining(message, context, call):
        asked["monitor_context"] = context
        return "NOT_STATUS"

    async def answering(message, context, call):
        return "Scheduler: running"

    async def chat(message, context, call):
        asked["direct_context"] = context
        return "You said you would work on it Wednesday before your shift."

    async def notify(_):
        pass

    try:
        config.MEMORY_DIR = Path(tmp.name)
        context_store._DB_PATH = config.MEMORY_DIR / "context.db"
        config.AUTHORIZED_USER_ID = 42
        monitor.handle, direct.handle = declining, chat
        o = orchestrator.Orchestrator(notify)
        o._context = [{"role": "user", "content": "I work Wednesday", "ts": 1761000000}]

        async def as_monitor(text):
            return "Monitor"

        o._classify = as_monitor
        reply, _ = asyncio.run(o.process(42, "what did I tell you about my work schedule this week?"))
        _check("a declined message is answered by Direct", reply.startswith("You said"), reply)
        _check("without the [Monitor] tag", "[Monitor]" not in reply, reply)
        _check("Monitor itself still sees no conversation", asked.get("monitor_context") == [])
        _check("Direct gets the conversation", any("I work Wednesday" in m["content"]
                                                   for m in asked.get("direct_context", [])),
               str(asked.get("direct_context")))

        monitor.handle = answering
        reply, _ = asyncio.run(o.process(42, "is the scheduler running?"))
        _check("a real status answer passes through with its tag",
               reply.startswith("Scheduler: running") and "[Monitor]" in reply, reply)
    finally:
        (config.MEMORY_DIR, context_store._DB_PATH, monitor.handle, direct.handle,
         config.AUTHORIZED_USER_ID) = real
        tmp.cleanup()


def test_prompts() -> None:
    import orchestrator
    from agents import monitor
    _check("the router knows the user's own schedule is conversation",
           '"What did I tell you about my work schedule" is CHAT' in orchestrator._ROUTER_PROMPT)
    _check("and that scheduled messages are still Monitor's",
           "Asking which messages IGOR has scheduled for the user is MONITOR" in orchestrator._ROUTER_PROMPT)
    _check("Monitor can decline", "NOT_STATUS" in monitor._DEFAULT_SYSTEM_PROMPT)


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            print(name)
            fn()
    if _failures:
        print(f"\n{len(_failures)} FAILED: {', '.join(_failures)}")
        sys.exit(1)
    print("\nall pass")
