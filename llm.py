"""One place that knows how a Groq text completion fails.

Two failures arrive as a 200 OK and are invisible to the caller:

- empty content - the model spent the whole budget on hidden reasoning
- finish_reason "length" - the answer was cut mid-sentence and still reads as valid

The second is the dangerous one. On 2026-08-10 two research findings were written
to memory ending at "without" and "(https://news.usni.org/" because the call site
checked only for empty content. CLAUDE.md documented the pattern and react.py
already implemented it correctly; the knowledge lived as prose and every new call
site re-derived it. This module exists so it cannot be re-derived wrong again.

Both cases retry once at double budget, then return whatever came back - a partial
answer beats none, and the caller has no other way to tell.

Exceptions propagate. Callers handle rate limits differently on purpose: the
orchestrator notifies the user and sleeps, the evaluator fails open, monitor logs
and drops the section, the research loop stops and reports what it has.

react.py keeps its own loop. Its finish_reason handling is entangled with tool
dispatch and tool_use_failed retries, and it is already correct.
"""

import logging
from typing import Optional, Union

import openai

logger = logging.getLogger(__name__)

# Doubling has to stay inside the model's TPM bucket, since prompt + max_tokens is
# what counts against it at request time. 4096 clears every current call site: the
# tightest is the AI news synthesis, 1536 -> 3072 against a ~2150 token prompt on
# an 8000 bucket. Pass a lower cap if a new site sits closer to its ceiling.
_CAP = 4096

# Per-model request parameters that are wrong to omit, keyed here because every
# one of them fails as a 200 OK rather than an error.
#
# qwen3.6 writes its chain of thought into message.content unless
# reasoning_format is set. Measured 2026-08-18: an unset call returns an opening
# think tag followed by its reasoning as the answer, so that text reaches
# Discord, memory files and the router's verdict parser intact.
#
# gpt-oss keeps reasoning in a separate field, so it cannot leak, but unset it
# spends an unbounded share of max_tokens on hidden reasoning - which empties any
# of the 1024-token call sites. "low" is what research_loop measured at 4x to 7x
# fewer tokens with output quality holding. gpt-oss rejects "none" with a 400.
#
# react.py is deliberately not covered: it runs its own loop and leaves
# reasoning_effort unset, because choosing a tool is the case where the reasoning
# plausibly earns its tokens. See STATE.md.
_MODEL_DEFAULTS: dict[str, dict[str, str]] = {
    "qwen/qwen3.6-27b": {"reasoning_format": "hidden"},
    "openai/gpt-oss-120b": {"reasoning_effort": "low"},
    "openai/gpt-oss-20b": {"reasoning_effort": "low"},
}


# reasoning_format is a Groq extension the openai client has no parameter for, so
# passing it as a keyword raises TypeError before a request is ever made - which
# the deploy gate cannot see, because it imports modules and never makes a call.
# It has to travel in extra_body. reasoning_effort is a first-class client
# parameter and must not.
_EXTRA_BODY_KEYS = frozenset({"reasoning_format"})


def model_params(model: str, **overrides: Optional[str]) -> dict:
    """Request kwargs for a model, with any explicit caller value winning.

    Callers that measured their own setting keep it; callers that never thought
    about it still get a request that cannot fail silently.
    """
    merged = dict(_MODEL_DEFAULTS.get(model, {}))
    merged.update({k: v for k, v in overrides.items() if v})

    params: dict = {k: v for k, v in merged.items() if k not in _EXTRA_BODY_KEYS}
    body = {k: v for k, v in merged.items() if k in _EXTRA_BODY_KEYS}
    if body:
        params["extra_body"] = body
    return params


async def complete(
    client: openai.AsyncOpenAI,
    model: str,
    system: str,
    user: Union[str, list[dict]],
    max_tokens: int,
    cap: int = _CAP,
    label: str = "",
    reasoning_effort: Optional[str] = None,
) -> str:
    messages = [{"role": "system", "content": system}]
    if isinstance(user, str):
        messages.append({"role": "user", "content": user})
    else:
        messages.extend(user)

    extra = model_params(model, reasoning_effort=reasoning_effort)

    who = label or model
    budget = max_tokens

    for attempt in range(2):
        response = await client.chat.completions.create(
            model=model,
            messages=messages,
            max_tokens=budget,
            **extra,
        )
        choice = response.choices[0]
        content = (choice.message.content or "").strip()
        truncated = choice.finish_reason == "length"

        if content and not truncated:
            return content

        # A caller already at the cap gains nothing from a second identical request
        # and pays full price for it, so stop rather than retry.
        doubled = min(budget * 2, cap)
        if attempt == 1 or doubled == budget:
            if truncated:
                logger.warning("%s still truncated at %d tokens, keeping the partial", who, budget)
            else:
                logger.warning("%s returned empty content at %d tokens, giving up", who, budget)
            return content

        logger.warning(
            "%s %s at %d tokens, retrying at %d",
            who, "was truncated" if truncated else "returned empty content", budget, doubled,
        )
        budget = doubled

    return ""
