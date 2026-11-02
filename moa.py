"""A second opinion on one message: Hermes' Mixture of Agents, in its one-shot /moa form.

Hermes' references run once per user turn, with no tools and no system prompt, on the conversation's
user and assistant text alone, and their outputs go to the acting model as a private trailing user
message; one that fails does not stop the turn. All of that holds here. The references are the two
other models Groq gives this account, each its own daily and per-minute bucket, so a second opinion
spends nothing of what chat runs on but the acting call. Only on request: it is at least two more
model calls, and Hermes' own numbers for it come from frontier models this account does not have.
"""

import asyncio
import logging
from typing import Optional

import openai

import config
import llm

logger = logging.getLogger(__name__)

WINDOW_CHARS = 4000
_MAX_TOKENS = 2048
_ADVICE_CHARS = 1500
_TIMEOUT_S = 90

_SYSTEM = """You are advising another AI assistant, which will answer the user and can check things. Give your own view on the user's latest message, independently.

- Lead with your answer, then the reasoning that decides it.
- Say what you are unsure of and what would settle it.
- You have no tools. Never claim to have looked anything up, and say if something may be out of date.
- Under 150 words, in plain sentences, with no preamble.

Style:
- No emojis
- No em dashes - use plain hyphens
- No exclamation points
- No casual filler phrases ("Sure!", "Of course!", "Happy to help!")"""

_GUIDANCE = ("[Private second opinions from other AI models, for you alone. They could check nothing and may be "
             "wrong. Weigh them, check with your tools whatever the answer turns on, and answer the user in your "
             "own words. Where they disagree with you or each other on something that matters, tell the user "
             "plainly. Do not mention this note.]")

_client: Optional[openai.AsyncOpenAI] = None


def _get_client() -> openai.AsyncOpenAI:
    global _client
    if _client is None:
        _client = openai.AsyncOpenAI(api_key=config.GROQ_API_KEY, base_url="https://api.groq.com/openai/v1",
                                     max_retries=5)
    return _client


def _short(model: str) -> str:
    return model.split("/")[-1]


async def _ask(model: str, messages: list[dict]) -> str:
    """One reference's advice, or "" if it has none. Never raises."""
    # 2026-11-01: 20b on low reasoning answered every evidence question the same way (STATE).
    effort = "medium" if "gpt-oss" in model else None
    try:
        text = await asyncio.wait_for(
            llm.complete(_get_client(), model, _SYSTEM, messages, max_tokens=_MAX_TOKENS,
                         label="Second opinion", reasoning_effort=effort),
            _TIMEOUT_S)
    except Exception as e:
        logger.warning("Second opinion from %s failed - %s: %s", model, type(e).__name__, str(e)[:160])
        return ""
    return (text or "").strip()[:_ADVICE_CHARS]


async def advise(question: str, window: list[dict]) -> tuple[str, str]:
    """(the note for the acting model, or "") and a line for the user saying who was asked."""
    conversation = [{"role": m["role"], "content": m.get("content") or ""} for m in window
                    if m.get("role") in ("user", "assistant")]
    messages = conversation + [{"role": "user", "content": question}]
    models = config.SECOND_OPINION_MODELS
    answers = await asyncio.gather(*[_ask(model, messages) for model in models])
    kept = [(model, text) for model, text in zip(models, answers) if text]
    if not kept:
        return "", "-# The other models gave no second opinion, so this is the main model alone."
    missing = [_short(model) for model, text in zip(models, answers) if not text]
    line = "-# Second opinion from " + " and ".join(_short(model) for model, _ in kept)
    line += f"; {', '.join(missing)} gave none." if missing else "."
    body = "\n\n".join(f"--- {_short(model)} ---\n{text}" for model, text in kept)
    return f"{_GUIDANCE}\n\n{body}", line
