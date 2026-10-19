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

import asyncio
import logging
import re
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
- If the brief has words in double quotes, the user dictated them: include them exactly as written.
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


# 2026-10-16, live: a scheduled "something thought-provoking" composed into one
# opener sentence and nothing else. compose() may not invent anything, which is
# right for a check-in and wrong for a message whose whole point is new material.
# Hermes runs a full agent on a stored prompt when a job fires; this does the same,
# with read-only tools only, because nobody is watching when it runs.
_AGENT_TOOLS = ["search", "fetch_url", "memory_read", "search_memory"]

# 2026-10-19, live: a weekday "Practical idea for IGOR" proposed a #brain-dump channel
# and !commands for a bot that only reads DMs, to build a knowledge hub it already has.
# The run knew IGOR by name and nothing else. Hermes grounds a cron job by loading the
# project's context file into it (`workdir`); this attaches the verified self-summary
# SelfDescribe answers from, about 950 tokens, only when the brief is about IGOR.
_ABOUT_IGOR = re.compile(r"\bI\.?G\.?O\.?R\b", re.IGNORECASE)
_AGENT_MAX_TOKENS = 2048
_AGENT_TIMEOUT_S = 180

_AGENT_SYSTEM = """You are I.G.O.R., writing one Discord message to the one person you talk to. They asked for it earlier and it is being sent now. You have no conversation history: the brief is everything you know about what they want.

- Write the message itself, ready to send. No preamble, no "here is your message", and no mention of scheduling, reminders, or that it was requested.
- Do what the brief asks. If it asks for material - a question, an idea, an argument to sit with - write original material and give it real depth.
- If the brief has words in double quotes, the user dictated them: include them exactly as written.
- Use tools only when the brief needs something current or something from memory. memory_read user.md holds the user's stated preferences.
- Never invent facts about the user, about I.G.O.R., or about events - what anyone did or said, or how something went.
- Plain prose. No headers or lists unless the brief asks for them.

Style:
- No emojis
- No em dashes - use plain hyphens
- No exclamation points
- No casual filler phrases ("Sure!", "Of course!", "Happy to help!")"""


async def write(brief: str, now: Optional[datetime] = None,
                recent: Optional[list[str]] = None) -> Optional[str]:
    """The message for a scheduled brief: an agent run, then compose(), then None.

    Each step is a model call that can fail, time out or come back empty, and the
    caller sends the brief itself if this returns None.
    """
    from agents import react
    task = f"What this message is for: {brief}"
    if _ABOUT_IGOR.search(brief):
        from agents import self_describe
        summary = self_describe._load_document()
        if summary:
            task += ("\n\nThe brief is about I.G.O.R. itself. This is what I.G.O.R. is today, "
                     "verified against its source code. Anything you suggest must fit this setup, "
                     "and must not propose something it already does:\n" + summary)
    if recent:
        earlier = "\n".join(f"- {r}" for r in recent)
        task += ("\n\nThis message repeats. Earlier ones in the series, newest first - say "
                 "something new, and do not repeat their ideas or wording. If the brief names "
                 "one specific idea, take it as an example of the kind wanted, not as the "
                 f"content of every message:\n{earlier}")
    try:
        text = await asyncio.wait_for(
            react.handle(
                task,
                [],
                max_tokens=_AGENT_MAX_TOKENS,
                allowed_tools=_AGENT_TOOLS,
                system_override=_AGENT_SYSTEM,
                label="Scheduled",
            ),
            timeout=_AGENT_TIMEOUT_S,
        )
    except Exception as e:
        logger.error("Scheduled-message agent failed, falling back to compose - %s: %s",
                     type(e).__name__, e)
        text = None
    text = (text or "").strip()
    if text:
        return text
    logger.warning("Scheduled-message agent returned nothing, falling back to compose")
    return await compose(brief, now)
