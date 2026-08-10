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
from typing import Union

import openai

logger = logging.getLogger(__name__)

# Doubling has to stay inside the model's TPM bucket, since prompt + max_tokens is
# what counts against it at request time. 4096 clears every current call site: the
# tightest is the AI news synthesis, 1536 -> 3072 against a ~2150 token prompt on
# the 6000 bucket. Pass a lower cap if a new site sits closer to its ceiling.
_CAP = 4096


async def complete(
    client: openai.AsyncOpenAI,
    model: str,
    system: str,
    user: Union[str, list[dict]],
    max_tokens: int,
    cap: int = _CAP,
    label: str = "",
) -> str:
    messages = [{"role": "system", "content": system}]
    if isinstance(user, str):
        messages.append({"role": "user", "content": user})
    else:
        messages.extend(user)

    who = label or model
    budget = max_tokens

    for attempt in range(2):
        response = await client.chat.completions.create(
            model=model,
            messages=messages,
            max_tokens=budget,
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
