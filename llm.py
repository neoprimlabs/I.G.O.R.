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
import os
import re
import time
from contextvars import ContextVar
from typing import Optional, Union

import openai

import config
import usage

logger = logging.getLogger(__name__)

# Doubling has to stay inside the model's TPM bucket, since prompt + max_tokens is
# what counts against it at request time. 4096 clears every current call site: the
# tightest is the AI news synthesis, 1536 -> 3072 against a ~2150 token prompt on
# an 8000 bucket. Pass a lower cap if a new site sits closer to its ceiling.
_CAP = 4096

# Per-model request parameters that are wrong to omit, keyed here because every
# one of them fails as a 200 OK rather than an error.
#
# qwen writes its chain of thought into message.content unless reasoning_format
# is set. Measured on 3.6, 2026-08-18: an unset call returns an opening think tag
# followed by its reasoning as the answer, so that text reaches Discord, memory
# files and the router's verdict parser intact. Groq replaced 3.6 with 3.8 on
# 2026-09-15, and 3.8 did not leak in 5 of 5 unset calls at max_tokens=10 - the
# setting stays anyway, because it costs nothing and the version after this one
# is unmeasured until someone measures it.
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
    "qwen/qwen3.8-27b": {"reasoning_format": "hidden"},
    "openai/gpt-oss-120b": {"reasoning_effort": "low"},
    "openai/gpt-oss-20b": {"reasoning_effort": "low"},
    "cerebras:gpt-oss-120b": {"reasoning_effort": "low"},
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


# Fallback, on the pattern Hermes documents: a rate limit that survives the
# client's retries benches the model until the reset time Groq names, and the call
# is retried once on the fallback. Turn-scoped notice, so the user knows which
# model answered. 2026-10-16: the daily cap on 120b ran out and Direct and React
# went dark for ~25 minutes while 20b, a separate bucket, sat idle.
_TRY_AGAIN = re.compile(r"try again in ((?:\d+h)?(?:\d+m)?(?:[\d.]+s)?)")
_WAIT_PART = re.compile(r"([\d.]+)([hms])")
_BENCH_DEFAULT_S = 60

# Other failures that move a call down the chain, as Hermes' triggers do: a model
# Groq no longer serves (it deleted the Llama family on 2026-08-17), a key it refuses,
# its servers failing, the connection dropping. A 400 is the request's own fault -
# React handles tool_use_failed itself - so on the primary it is raised as before.
_GONE_S = 3600
_ERROR_S = 60

# model -> (bench until, why): why is True for a daily cap, False for a per-minute
# limit, "gone" for a model that is not found or a refused key, "error" otherwise.
_benched: dict[str, tuple[float, object]] = {}
_turn: ContextVar[Optional[dict]] = ContextVar("llm_turn", default=None)
_provider_clients: dict[str, openai.AsyncOpenAI] = {}


def is_daily_cap(e: Exception) -> bool:
    text = str(e)
    return "tokens per day" in text or "(TPD)" in text


def wait_seconds(e: Exception) -> Optional[float]:
    """The reset Groq names in a 429 body ("try again in 16m2.064s"), in seconds."""
    match = _TRY_AGAIN.search(str(e))
    if not match or not match.group(1):
        return None
    return sum(float(n) * {"h": 3600, "m": 60, "s": 1}[unit]
               for n, unit in _WAIT_PART.findall(match.group(1)))


def in_words(seconds: float) -> str:
    minutes = max(1, -(-int(seconds) // 60))
    if minutes < 60:
        return "about a minute" if minutes == 1 else f"about {minutes} minutes"
    hours, rest = divmod(minutes, 60)
    return f"about {hours} hour{'s' if hours > 1 else ''}" + (f" {rest} minutes" if rest else "")


def bench(model: str, e: Exception, now: Optional[float] = None) -> None:
    now = time.time() if now is None else now
    if isinstance(e, openai.RateLimitError):
        wait, why = wait_seconds(e) or _BENCH_DEFAULT_S, is_daily_cap(e)
    elif isinstance(e, (openai.NotFoundError, openai.AuthenticationError, openai.PermissionDeniedError)):
        wait, why = _GONE_S, "gone"
    else:
        wait, why = _ERROR_S, "error"
    _benched[model] = (now + wait, why)
    logger.error("%s benched for %ds (%s: %s)", model, int(wait), type(e).__name__, str(e)[:160])


def _chain(model: str) -> list[str]:
    """The fallbacks for a model, in order, leaving out providers with no key set."""
    out = []
    for entry in config.FALLBACK_MODELS.get(model) or []:
        provider = entry.split(":", 1)[0] if ":" in entry else ""
        if provider and not os.getenv(config.PROVIDERS.get(provider, ("", ""))[1] or "_NO_KEY_"):
            continue
        out.append(entry)
    return out


def _fails_over(e: Exception, primary: bool) -> bool:
    if isinstance(e, (openai.RateLimitError, openai.NotFoundError, openai.AuthenticationError,
                      openai.PermissionDeniedError, openai.InternalServerError, openai.APIConnectionError)):
        return True
    # A fallback provider that rejects the request moves on rather than ending the chain.
    return not primary and isinstance(e, openai.APIError)


def _client_for(entry: str, groq: openai.AsyncOpenAI) -> tuple[openai.AsyncOpenAI, str]:
    """The client and the provider's own model id for a chain entry."""
    if ":" not in entry:
        return groq, entry
    provider, model = entry.split(":", 1)
    if provider not in _provider_clients:
        base_url, key_env = config.PROVIDERS[provider]
        _provider_clients[provider] = openai.AsyncOpenAI(api_key=os.getenv(key_env, ""),
                                                         base_url=base_url, max_retries=5)
    return _provider_clients[provider], model


def _usable(entry: str, now: float) -> bool:
    return now >= _benched.get(entry, (0.0, False))[0]


def choose(model: str, now: Optional[float] = None) -> str:
    """The model to call: the first one in the chain that is not benched."""
    now = time.time() if now is None else now
    if _usable(model, now):
        return model
    for entry in _chain(model):
        if _usable(entry, now):
            _note(model, entry)
            return entry
    return model


def _note(primary: str, used: str) -> None:
    turn = _turn.get()
    if turn is not None:
        turn[primary] = (*_benched.get(primary, (0.0, False)), used)


def _name(entry: str) -> str:
    provider, _, model = entry.partition(":")
    return f"{provider.capitalize()} {model}" if model else entry


def start_turn():
    return _turn.set({})


def end_turn(token, now: Optional[float] = None) -> Optional[str]:
    """One line for the user if anything this turn ran on a fallback, else None."""
    turn = _turn.get() or {}
    _turn.reset(token)
    if not turn:
        return None
    now = time.time() if now is None else now
    primary, (until, kind, used) = next(iter(turn.items()))
    why = {True: "its daily allowance is used up", False: "it is rate-limited",
           "gone": "Groq says the model is not available", "error": "it is returning errors"}.get(kind, "it failed")
    return (f"`[Answered on the backup model ({_name(used)}): "
            f"the main model is back in {in_words(max(until - now, 1))}, {why}]`")


async def create(client: openai.AsyncOpenAI, model: str, label: str, *,
                 params: bool = True, reasoning_effort: Optional[str] = None, **kwargs):
    """chat.completions.create, recorded in the token ledger, with fallback.

    params=False skips model_params for the caller that deliberately leaves
    reasoning_effort unset (React). Raises what the last attempt raised.
    """
    order = [model] + _chain(model)
    start = order.index(choose(model))
    for i, use in enumerate(order[start:], start):
        extra = model_params(use, reasoning_effort=reasoning_effort) if params else {}
        target, model_id = _client_for(use, client)
        try:
            response = await target.chat.completions.create(model=model_id, **kwargs, **extra)
        except Exception as e:
            if not _fails_over(e, primary=(use == model)) or i == len(order) - 1:
                if _fails_over(e, primary=(use == model)):
                    bench(use, e)
                raise
            bench(use, e)
            _note(model, order[i + 1])
            logger.warning("%s falling back from %s to %s", label or model, use, order[i + 1])
            continue
        usage.record(use, response, label)
        return response
    raise RuntimeError("unreachable")


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

    who = label or model
    budget = max_tokens

    for attempt in range(2):
        response = await create(client, model, who, reasoning_effort=reasoning_effort,
                                messages=messages, max_tokens=budget)
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
