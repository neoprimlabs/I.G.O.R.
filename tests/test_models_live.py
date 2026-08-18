"""Make one real call per configured role and check what comes back.

Not part of the deploy gate and not free. The gate imports modules without
calling the API, so it passes on a router that answers with a think tag and on a
reasoning budget that returns empty content - both of which are 200 OK responses
that no import can see. This is the only check that would have caught either.

Run after any change to config.MODELS or llm.model_params:

    venv/bin/python tests/test_models_live.py

Costs roughly 5000 tokens across three buckets, so it is safe to run daily but
not in a loop.
"""
import asyncio
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import openai

import config
import llm
import sanitize
from orchestrator import _ROUTER_PROMPT, _VERDICT_MAP

_ROUTER_CASES = [
    "what do you think about the new oracle pricing",
    "what agents do you have",
    "search the web for groq model deprecations and summarise",
    "change your digest time to 8am",
]

_failures: list[str] = []


def _check(condition: bool, message: str) -> bool:
    if not condition:
        _failures.append(message)
    return condition


async def _check_router(client: openai.AsyncOpenAI) -> None:
    model = config.MODELS["router"]
    print(f"=== router: {model}")
    for text in _ROUTER_CASES:
        response = await client.chat.completions.create(
            model=model,
            messages=[
                {"role": "system", "content": _ROUTER_PROMPT},
                {"role": "user", "content": text[:1000]},
            ],
            max_tokens=10,
            temperature=0,
            **llm.model_params(model, reasoning_effort="none"),
        )
        raw = (response.choices[0].message.content or "").strip().upper()
        verdict = raw.split()[0].strip(".,:;\"'") if raw.split() else ""
        ok = _check(
            verdict in _VERDICT_MAP,
            f"router returned {raw!r} for {text!r}, which _VERDICT_MAP misses - "
            f"every message would silently route to React",
        )
        print(f"  {'pass' if ok else 'FAIL'}  {raw!r:12} -> {_VERDICT_MAP.get(verdict, 'React')}")


async def _check_role(client: openai.AsyncOpenAI, role: str, max_tokens: int, system: str, user: str) -> None:
    model = config.MODELS[role]
    print(f"=== {role}: {model} (max_tokens={max_tokens})")
    out = await llm.complete(client, model, system, user, max_tokens=max_tokens, label=role)
    ok = _check(bool(out), f"{role} returned empty content at max_tokens={max_tokens} - reasoning ate the budget")
    ok &= _check("<think" not in out.lower(), f"{role} leaked reasoning into content - reasoning_format is missing")
    ok &= _check(
        all(ord(c) < 128 for c in sanitize.clean(out)),
        f"{role} emitted punctuation sanitize._PUNCT_MAP does not cover: "
        f"{sorted({hex(ord(c)) for c in sanitize.clean(out) if ord(c) > 127})}",
    )
    print(f"  {'pass' if ok else 'FAIL'}  len={len(out)}")


async def main() -> None:
    client = openai.AsyncOpenAI(api_key=config.GROQ_API_KEY, base_url="https://api.groq.com/openai/v1")
    await _check_router(client)
    await _check_role(
        client, "chat", 1024,
        "You are IGOR, a personal assistant. Answer in two sentences.",
        "What is the difference between a tokens-per-minute and a tokens-per-day limit?",
    )
    await _check_role(
        client, "summary", 1536,
        "Summarize the following into exactly 3 bullet points.",
        "[1] Groq removed several Llama models.\n[2] Qwen3.6 27B is available.\n[3] gpt-oss exposes reasoning separately.",
    )
    await _check_role(
        client, "evaluator", 1024,
        "You grade an assistant response. Reply PASS or FAIL and one sentence of reason.",
        "Task: what is 2+2\nResponse: 4",
    )

    print()
    if _failures:
        for failure in _failures:
            print(f"FAIL: {failure}")
        sys.exit(1)
    print("all pass")


asyncio.run(main())
