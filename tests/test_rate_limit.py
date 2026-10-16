"""What the user is told when Groq rate-limits IGOR.

Run: python tests/test_rate_limit.py   (repo root, no API calls)

2026-10-16: the daily cap on gpt-oss-120b ran out. Every reply said "Wait a minute
and try again" while the 429 said 16 to 24 minutes, and the chat path retried twice,
30s then 60s, before giving the same wrong answer. The 429 strings below are copied
from that day's journal.
"""

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import httpx
import openai

import orchestrator

_failures: list[str] = []


def _check(name: str, condition: bool, detail: str = "") -> None:
    if condition:
        print(f"  pass  {name}")
    else:
        print(f"  FAIL  {name}  {detail}")
        _failures.append(name)


DAILY = ("Error code: 429 - {'error': {'message': 'Rate limit reached for model `openai/gpt-oss-120b` "
         "in organization `org_x` service tier `on_demand` on tokens per day (TPD): Limit 200000, "
         "Used 198460, Requested 3767. Please try again in 16m2.064s. Need more tokens?', "
         "'type': 'tokens', 'code': 'rate_limit_exceeded'}}")
DAILY_SHORT = DAILY.replace("16m2.064s", "5m42.144s")
PER_MINUTE = ("Error code: 429 - {'error': {'message': 'Rate limit reached for model `openai/gpt-oss-120b` "
              "on tokens per minute (TPM): Limit 8000, Used 7200, Requested 3000. "
              "Please try again in 16.5s.'}}")


def _rate_limit_error(text: str) -> openai.RateLimitError:
    response = httpx.Response(429, request=httpx.Request("POST", "https://api.groq.com/openai/v1/chat/completions"))
    return openai.RateLimitError(text, response=response, body=None)


def test_replies() -> None:
    reply = orchestrator._rate_limit_reply(Exception(DAILY))
    _check("the daily cap is named as the daily allowance", "daily token allowance" in reply, reply)
    _check("with Groq's wait, rounded up: 16m2s is about 17 minutes", "about 17 minutes" in reply, reply)
    _check("and not the old 'wait a minute'", "Wait a minute" not in reply, reply)
    _check("5m42s reads as about 6 minutes",
           "about 6 minutes" in orchestrator._rate_limit_reply(Exception(DAILY_SHORT)))

    reply = orchestrator._rate_limit_reply(Exception(PER_MINUTE))
    _check("a per-minute limit is not called daily", "daily" not in reply, reply)
    _check("and 16.5s reads as about a minute", "about a minute" in reply, reply)

    reply = orchestrator._rate_limit_reply(Exception("429 with no wait in it"))
    _check("a 429 with no wait still gets an answer", reply.startswith("Groq rate limit hit"), reply)
    reply = orchestrator._rate_limit_reply(Exception(DAILY.replace("Please try again in 16m2.064s. ", "")))
    _check("a daily cap with no wait says try again later", "try again later" in reply, reply)

    _check("hours read as hours", orchestrator._wait_in_words(Exception("try again in 1h2m30s")) ==
           "about 1 hour 3 minutes", str(orchestrator._wait_in_words(Exception("try again in 1h2m30s"))))


async def test_chat_does_not_retry_a_daily_cap() -> None:
    import llm
    calls, sleeps, notes = [], [], []

    async def complete(*a, **kw):
        calls.append(1)
        raise _rate_limit_error(DAILY)

    async def sleep(seconds):
        sleeps.append(seconds)

    async def notify(text):
        notes.append(text)

    real_complete, real_sleep = llm.complete, orchestrator.asyncio.sleep
    llm.complete, orchestrator.asyncio.sleep = complete, sleep
    try:
        reply = await orchestrator.call_claude(None, notify, "system", [])
    finally:
        llm.complete, orchestrator.asyncio.sleep = real_complete, real_sleep
    _check("a daily cap is tried once, not three times", len(calls) == 1, str(len(calls)))
    _check("with no sleeping", sleeps == [], str(sleeps))
    _check("and no 'retrying in 30 seconds' message", notes == [], str(notes))
    _check("the reply names the real wait", "about 17 minutes" in reply, reply)


if __name__ == "__main__":
    print("replies")
    test_replies()
    print("\nchat path")
    asyncio.run(test_chat_does_not_retry_a_daily_cap())
    if _failures:
        print(f"\n{len(_failures)} FAILED: {', '.join(_failures)}")
        sys.exit(1)
    print("\nall pass")
