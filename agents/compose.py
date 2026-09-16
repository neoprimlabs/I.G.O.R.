"""Writes a short message to the user, in IGOR's voice, at the moment it is sent.

A check-in scheduled on Monday and delivered on Thursday used to be Monday's words
replayed. It could not mention anything that happened in between, which is what made
it read like a ticket ("Here's your scheduled check-in at 11:40 am").

The grounding rule is the whole design: the model may use what is in the block it is
given and nothing else. Anything it sends is stored by record_outbound and becomes an
input to every later turn, so an invention here does not fade - it is the exact
mechanism behind the 2026-08-13 fabrication (STATE.md).

Runs on the summary model, not React's. ProactiveBench measured 22 models and found
proactiveness does not track model capacity, so the larger model buys nothing here.
"""

import logging
from datetime import datetime
from typing import Optional

import clock
import config
import llm

logger = logging.getLogger(__name__)

_MAX_TOKENS = 400

_SYSTEM = """You write one short Discord message from I.G.O.R. to the one person it talks to.

You are given a brief saying what the message is for, and a block of facts. Write the message that fits the moment it is arriving in.

Rules:
- One to three sentences. Shorter is better.
- Plain prose, like a person typing. No headers, no lists, no sign-off.
- Use ONLY the facts in the block. If you have no detail, keep it simple rather than inventing one.
- Never invent activity, progress, events, or anything the user said or did. Do not guess how something went.
- Do not restate the brief back. Do not mention scheduling, reminders, timestamps or ids.
- Refer to the time of day naturally if it matters, not as a clock reading.

Style:
- No emojis
- No em dashes - use plain hyphens
- No exclamation points
- No casual filler phrases ("Sure!", "Of course!", "Happy to help!")"""


def _facts(now: Optional[datetime] = None) -> str:
    """Only what can be read from a file or the database. Nothing inferred."""
    lines = [f"Current time: {clock.time_line(now)}"]
    try:
        import context_store
        recent = context_store.load(4)
    except Exception:
        recent = []
    if recent:
        lines.append("Recent conversation, oldest first:")
        for turn in recent:
            who = "user" if turn.get("role") == "user" else "you"
            lines.append(f"  {who}: {(turn.get('content') or '')[:300]}")
    else:
        lines.append("Recent conversation: none stored.")
    return "\n".join(lines)


async def compose(brief: str, now: Optional[datetime] = None) -> Optional[str]:
    """The message, or None. Callers must have a fallback: this is a model call."""
    import openai

    client = openai.AsyncOpenAI(
        api_key=config.GROQ_API_KEY,
        base_url="https://api.groq.com/openai/v1",
    )
    text = await llm.complete(
        client,
        config.MODELS["summary"],
        _SYSTEM,
        f"Brief: {brief}\n\nFacts:\n{_facts(now)}",
        max_tokens=_MAX_TOKENS,
        label="Compose",
    )
    text = (text or "").strip()
    if not text:
        logger.warning("Compose returned nothing for brief %r", brief[:60])
        return None
    return text
