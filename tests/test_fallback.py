"""Model fallback (llm.create) when Groq rate-limits the primary.

Run: python tests/test_fallback.py   (repo root, no API calls)

2026-10-16: the daily cap on gpt-oss-120b ran out and Direct and React went dark for
~25 minutes while gpt-oss-20b, a separate bucket, sat idle. The pattern is Hermes':
bench the primary until the reset Groq names, retry on the fallback, tell the user.
"""

import asyncio
import sys
import tempfile
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import httpx
import openai

import config

config.MEMORY_DIR = Path(tempfile.mkdtemp())

import llm

_failures: list[str] = []
BIG, SMALL = "openai/gpt-oss-120b", "openai/gpt-oss-20b"
DAILY = ("Rate limit reached for model `openai/gpt-oss-120b` on tokens per day (TPD): "
         "Limit 200000, Used 199672, Requested 3530. Please try again in 23m3.264s.")


def _check(name: str, condition: bool, detail: str = "") -> None:
    print(f"  {'pass' if condition else 'FAIL'}  {name}" + ("" if condition else f"  {detail}"))
    if not condition:
        _failures.append(name)


def _limit(text: str) -> openai.RateLimitError:
    response = httpx.Response(429, request=httpx.Request("POST", "https://api.groq.com/x"))
    return openai.RateLimitError(text, response=response, body=None)


def _reply(text: str):
    return SimpleNamespace(
        choices=[SimpleNamespace(finish_reason="stop",
                                 message=SimpleNamespace(content=text, tool_calls=None))],
        usage=SimpleNamespace(total_tokens=100, prompt_tokens=80, completion_tokens=20))


class _Groq:
    """Fails for the models in `limited`, answers for the rest, records what was asked."""

    def __init__(self, limited):
        self.limited, self.asked, self.kwargs = set(limited), [], []
        self.chat = SimpleNamespace(completions=SimpleNamespace(create=self._create))

    async def _create(self, model, **kwargs):
        self.asked.append(model)
        self.kwargs.append(kwargs)
        if model in self.limited:
            raise _limit(DAILY if model == BIG else "Rate limit on 20b. Please try again in 40s.")
        return _reply(f"answer from {model}")


async def test_complete_falls_back() -> None:
    llm._benched.clear()
    groq = _Groq({BIG})
    turn = llm.start_turn()
    text = await llm.complete(groq, BIG, "system", "hello", max_tokens=1024, label="Chat")
    notice = llm.end_turn(turn)
    _check("a daily-capped 120b call is answered by 20b", text == f"answer from {SMALL}", text)
    _check("120b was tried first, then 20b", groq.asked == [BIG, SMALL], str(groq.asked))
    _check("the fallback call gets 20b's own parameters",
           groq.kwargs[-1].get("reasoning_effort") == "low", str(groq.kwargs[-1]))
    _check("the user is told, with Groq's reset rounded up",
           bool(notice) and "backup model" in notice and "about 24 minutes" in notice
           and "daily allowance" in notice, str(notice))

    groq.asked.clear()
    turn = llm.start_turn()
    await llm.complete(groq, BIG, "system", "again", max_tokens=1024, label="Chat")
    _check("while benched, 120b is not tried again", groq.asked == [SMALL], str(groq.asked))
    _check("and the next turn still says it is on the backup", llm.end_turn(turn) is not None)

    llm._benched[BIG] = (0.0, True)
    groq = _Groq(set())
    turn = llm.start_turn()
    text = await llm.complete(groq, BIG, "system", "later", max_tokens=1024, label="Chat")
    _check("once the reset has passed, 120b is used again",
           groq.asked == [BIG] and text == f"answer from {BIG}", str(groq.asked))
    _check("and no notice is added", llm.end_turn(turn) is None)


async def test_both_limited_still_fails_clearly() -> None:
    llm._benched.clear()
    groq = _Groq({BIG, SMALL})
    raised = None
    try:
        await llm.complete(groq, BIG, "system", "hello", max_tokens=1024)
    except openai.RateLimitError as e:
        raised = e
    _check("when 20b is limited too, the rate limit reaches the caller", raised is not None)
    _check("after one try on each model, not a loop", groq.asked == [BIG, SMALL], str(groq.asked))

    llm._benched.clear()
    groq = _Groq({SMALL})
    raised = None
    try:
        await llm.complete(groq, SMALL, "system", "hello", max_tokens=1024)
    except openai.RateLimitError as e:
        raised = e
    _check("20b has no fallback of its own", raised is not None and groq.asked == [SMALL],
           str(groq.asked))


async def test_react_falls_back() -> None:
    from agents import react
    llm._benched.clear()
    groq = _Groq({BIG})
    real = react._get_client
    react._get_client = lambda: groq
    try:
        turn = llm.start_turn()
        text = await react.handle("hello", [], max_tokens=1024)
        notice = llm.end_turn(turn)
    finally:
        react._get_client = real
    _check("React's own loop falls back too", text == f"answer from {SMALL}", text)
    _check("tried on 120b, answered on 20b", groq.asked == [BIG, SMALL], str(groq.asked))
    _check("React leaves reasoning_effort unset on the fallback, as on the primary",
           all("reasoning_effort" not in k for k in groq.kwargs), str(groq.kwargs))
    _check("and the turn carries the notice", notice is not None)


if __name__ == "__main__":
    asyncio.run(test_complete_falls_back())
    asyncio.run(test_both_limited_still_fails_clearly())
    asyncio.run(test_react_falls_back())
    if _failures:
        print(f"\n{len(_failures)} FAILED: {', '.join(_failures)}")
        sys.exit(1)
    print("\nall pass")
