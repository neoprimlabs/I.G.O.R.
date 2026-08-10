"""Tests for llm.complete, the shared Groq text-completion wrapper.

Run: python tests/test_llm.py   (from the repo root, no pytest, no new dependency)

The first test in this repo. It covers llm.py specifically because that module
exists to stop a silent failure, and a silent failure is exactly what nobody
notices has regressed.

The import check at the bottom matters as much as the behaviour tests:
py_compile checks syntax, not names, so a missing or unresolvable import passes
compilation and fails at runtime when something first routes to that module.
That has bitten this repo before.
"""

import asyncio
import sys
import types
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import llm

_failures: list[str] = []


def _check(name: str, condition: bool, detail: str = "") -> None:
    if condition:
        print(f"  pass  {name}")
    else:
        print(f"  FAIL  {name}  {detail}")
        _failures.append(name)


def _response(content, finish_reason):
    return types.SimpleNamespace(choices=[types.SimpleNamespace(
        message=types.SimpleNamespace(content=content),
        finish_reason=finish_reason,
    )])


class _FakeClient:
    """Returns queued responses in order and records the budget of each request."""

    def __init__(self, responses):
        self._responses = list(responses)
        self.budgets: list[int] = []
        self.messages: list[list] = []
        self.kwargs: list[dict] = []
        outer = self

        class _Completions:
            @staticmethod
            async def create(**kwargs):
                outer.budgets.append(kwargs["max_tokens"])
                outer.messages.append(kwargs["messages"])
                outer.kwargs.append(kwargs)
                return outer._responses[len(outer.budgets) - 1]

        class _Chat:
            completions = _Completions()

        self.chat = _Chat()


async def _run(responses, **kwargs) -> tuple[str, _FakeClient]:
    client = _FakeClient(responses)
    text = await llm.complete(client, "model", "SYS", "USR", max_tokens=1536, **kwargs)
    return text, client


async def test_behaviour() -> None:
    text, c = await _run([_response("answer", "stop")])
    _check("clean response returns immediately, one call",
           text == "answer" and c.budgets == [1536], f"got {text!r} {c.budgets}")

    # The 2026-08-10 bug: non-empty content with finish_reason "length" was
    # returned as if complete, and two research findings reached memory cut
    # mid-sentence.
    text, c = await _run([_response("cut off at", "length"), _response("full answer", "stop")])
    _check("truncated response retries at double budget",
           text == "full answer" and c.budgets == [1536, 3072], f"got {text!r} {c.budgets}")

    text, c = await _run([_response("", "stop"), _response("recovered", "stop")])
    _check("empty response retries at double budget",
           text == "recovered" and c.budgets == [1536, 3072], f"got {text!r} {c.budgets}")

    text, c = await _run([_response("part A", "length"), _response("part B", "length")])
    _check("still truncated on retry keeps the partial",
           text == "part B" and c.budgets == [1536, 3072], f"got {text!r} {c.budgets}")

    text, c = await _run([_response("", "stop"), _response("", "stop")])
    _check("empty twice returns empty rather than raising",
           text == "" and c.budgets == [1536, 3072], f"got {text!r} {c.budgets}")

    # A caller already at the cap gains nothing from an identical second request
    # and pays full token price for it.
    text, c = await _run([_response("clipped", "length")], cap=1536)
    _check("a caller already at the cap makes one call, not two",
           text == "clipped" and c.budgets == [1536], f"got {text!r} {c.budgets}")

    client = _FakeClient([_response("ok", "stop")])
    await llm.complete(client, "model", "SYS",
                       [{"role": "user", "content": "A"}, {"role": "assistant", "content": "B"}],
                       max_tokens=100)
    shape = [(m["role"], m["content"]) for m in client.messages[0]]
    _check("a message list is prepended with system and kept in order",
           shape == [("system", "SYS"), ("user", "A"), ("assistant", "B")], f"got {shape}")

    # The llama models do not accept reasoning_effort, so it must be absent from
    # their requests rather than sent as None.
    client = _FakeClient([_response("ok", "stop")])
    await llm.complete(client, "llama", "SYS", "USR", max_tokens=100)
    _check("reasoning_effort is omitted when unset", "reasoning_effort" not in client.kwargs[0],
           f"got {client.kwargs[0]}")

    client = _FakeClient([_response("ok", "stop")])
    await llm.complete(client, "gpt-oss", "SYS", "USR", max_tokens=100, reasoning_effort="low")
    _check("reasoning_effort is passed through when set",
           client.kwargs[0].get("reasoning_effort") == "low", f"got {client.kwargs[0]}")

    # It must survive the retry too, or the second attempt silently runs at the
    # gpt-oss default of high - which is what the setting exists to avoid.
    client = _FakeClient([_response("", "stop"), _response("recovered", "stop")])
    await llm.complete(client, "gpt-oss", "SYS", "USR", max_tokens=100, reasoning_effort="low")
    _check("reasoning_effort survives the retry",
           all(k.get("reasoning_effort") == "low" for k in client.kwargs),
           f"got {client.kwargs}")

    # Callers handle rate limits differently on purpose. The wrapper must not
    # swallow anything: the orchestrator sleeps and notifies, the evaluator fails
    # open, monitor drops the digest section, the research loop stops and reports.
    class _Raising:
        class chat:
            class completions:
                @staticmethod
                async def create(**kwargs):
                    raise RuntimeError("rate limited")

    raised = False
    try:
        await llm.complete(_Raising(), "model", "SYS", "USR", max_tokens=100)
    except RuntimeError:
        raised = True
    _check("exceptions propagate to the caller", raised)


def test_imports() -> None:
    for module in ("llm", "orchestrator", "context_store", "agents.direct",
                   "agents.react", "agents.monitor", "agents.evaluator",
                   "agents.research_loop", "agents.research", "agents.prod_memory"):
        try:
            __import__(module)
            _check(f"{module} imports", True)
        except Exception as exc:
            _check(f"{module} imports", False, f"{type(exc).__name__}: {exc}")


if __name__ == "__main__":
    print("llm.complete behaviour")
    asyncio.run(test_behaviour())
    print("\nmodule imports resolve")
    test_imports()

    if _failures:
        print(f"\n{len(_failures)} FAILED: {', '.join(_failures)}")
        sys.exit(1)
    print("\nall pass")
