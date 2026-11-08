"""Asking the user a question in the middle of a turn and waiting for the answer (Hermes' clarify).

Without it, React had to guess or end the turn with a question, and the answer started a new turn
with none of what the first one had found. As in Hermes, the question goes out as a numbered list
when it has choices, the next message is the answer, a number picks a choice, "skip" leaves it to
IGOR, and a message that is neither, when there were choices, is a new message rather than an
answer: the question is dropped and the message waits for the turn to finish. The same shape as
approvals.py, which waits on a yes or no the same way.
"""

import asyncio
import logging
import re
from typing import Awaitable, Callable, Optional

logger = logging.getLogger(__name__)

TIMEOUT_S = 600
MAX_CHOICES = 4
_CHOICE_CHARS = 200
_QUESTION_CHARS = 600
_SKIP = frozenset({"skip", "skip it", "you decide", "your call", "up to you"})

_send: Optional[Callable[[str], Awaitable[object]]] = None
_pending: Optional[tuple[str, list[str], asyncio.Future]] = None


def set_sender(send: Callable[[str], Awaitable[object]]) -> None:
    """Where questions go: the user's DM. Unset, nothing can be asked."""
    global _send
    _send = send


def waiting() -> Optional[str]:
    return _pending[0] if _pending is not None and not _pending[2].done() else None


def _clean_choices(choices) -> list[str]:
    if isinstance(choices, str):
        choices = [choices]
    if not isinstance(choices, list):
        return []
    out = [" ".join(str(c).split())[:_CHOICE_CHARS] for c in choices if str(c).strip()]
    return list(dict.fromkeys(out))[:MAX_CHOICES]


async def ask(question: str, choices=None) -> str:
    """The user's answer, worded for the model, or why there is none."""
    global _pending
    question = " ".join(str(question or "").split())[:_QUESTION_CHARS]
    if not question:
        return "[clarify refused: no question given]"
    if _send is None:
        return "[clarify refused: questions cannot reach the user from here. Go on with your best judgement]"
    if waiting() is not None:
        return "[clarify refused: another question is already waiting for the user]"
    options = _clean_choices(choices)
    future = asyncio.get_running_loop().create_future()
    _pending = (question, options, future)
    lines = [f"Before I go on: {question}"]
    lines += [f"{n}. {choice}" for n, choice in enumerate(options, 1)]
    lines.append(("Reply with a number or in your own words" if options else "Reply in your own words")
                 + f', or say skip to leave it to me. With no reply in {TIMEOUT_S // 60} minutes I go on without it.')
    try:
        await _send("\n".join(lines))
        got = await asyncio.wait_for(asyncio.shield(future), timeout=TIMEOUT_S)
    except asyncio.TimeoutError:
        logger.info("Clarify: no answer in %ds: %s", TIMEOUT_S, question[:100])
        return (f"NO ANSWER: the user did not reply within {TIMEOUT_S // 60} minutes. Go on with your best "
                "judgement and say what you assumed, or stop and say what you need from them.")
    except Exception as e:
        logger.error("Clarify request failed - %s: %s", type(e).__name__, e)
        return f"[clarify failed: the question could not be sent ({type(e).__name__}). Go on with your best judgement]"
    finally:
        _pending = None
    return got


def answer(text: str) -> Optional[str]:
    """If a question is waiting and this message answers it, settle it and return the reply to send.
    Anything else returns None and the message is handled as usual."""
    if _pending is None or _pending[2].done():
        return None
    question, options, future = _pending
    said = " ".join((text or "").split())
    bare = re.sub(r"[.!]+$", "", said.lower())
    if not said:
        return None
    if bare in _SKIP:
        future.set_result("SKIPPED: the user left it to you. Go on with your best judgement, and say what you chose.")
        return "Understood, I will decide."
    if options:
        picked = re.fullmatch(r"#?(\d)", bare)
        index = int(picked.group(1)) if picked else next(
            (i for i, choice in enumerate(options, 1) if bare == choice.lower().rstrip(".")), 0)
        if 1 <= index <= len(options):
            future.set_result(f"The user chose {index}: {options[index - 1]}")
            logger.info("Clarify: choice %d", index)
            return f"Got it: {options[index - 1]}."
        # Hermes drops the question on free prose when it had choices, so a new request is not
        # swallowed as an answer to a question the user has moved past.
        future.set_result("NO ANSWER: the user sent a new message instead of answering, and it comes after this "
                          "turn. Finish now with what you have, and say what you still need from them.")
        logger.info("Clarify: dropped for a new message")
        return None
    future.set_result(f"The user answered, in their own words: {said[:2000]}")
    logger.info("Clarify: answered")
    return "Got it."


def cancel(reason: str = "the user said stop") -> None:
    if _pending is not None and not _pending[2].done():
        _pending[2].set_result(f"NO ANSWER: {reason}. Stop now.")
