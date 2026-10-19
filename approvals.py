"""Asking the user before a risky shell command runs.

Hermes (checked 2026-10-19) has two tiers: a hardline blocklist that is always
refused, and a list of risky-but-sometimes-legitimate command shapes that need the
user's approval. On a chat platform it posts the command, waits for yes or no, and
treats no answer within 300 seconds as a no; an expired request cannot be reopened,
and the agent is told not to retry in that turn. Its "smart" mode, where a second
model waves low-risk commands through, is not built here: on the free tier that is
a model call per command, and IGOR's shell is used a few times a week.

IGOR's hardline tier is react._hardline_refusal. This module is the middle tier:
the patterns are Hermes' list, narrowed to what the igor user can actually do, plus
git commands that throw work away. One request waits at a time. The waiting turn
holds an asyncio future; the user's reply arrives as a separate Discord message,
which the orchestrator hands to answer() before anything else sees it.
"""

import asyncio
import logging
import re
from typing import Awaitable, Callable, Optional

logger = logging.getLogger(__name__)

TIMEOUT_S = 300
_YES = frozenset({"yes", "y", "approve", "approved", "ok", "okay", "go", "do it", "run it"})
_NO = frozenset({"no", "n", "deny", "denied", "cancel", "stop", "don't", "dont"})

# (why, pattern), checked against the lowercased command.
_RISKY = (
    ("it deletes recursively", r"(^|[\s;&|(])rm\s+(-[a-z]*r[a-z]*|--recursive)\b"),
    ("it deletes files found by a search", r"\bfind\b[^|;&]*\s-(delete|exec\s+rm)\b|\bxargs\s+(-\S+\s+)*rm\b"),
    ("it opens up file permissions", r"\bchmod\s+(-[a-z]+\s+)*(0?777|0?666|[ao]?\+w|o\+w)\b"),
    ("it stops or kills processes", r"(^|[\s;&|(])(kill|pkill|killall)\b"),
    ("it stops or restarts a service", r"\bsystemctl\s+(stop|restart|disable|mask|kill)\b"),
    ("it writes to keys, SSH settings or system config",
     r"(>|\btee\b)[^|;&]*(\.env\b|\.ssh/|/etc/)|\bsed\s+(-[a-z]*i|--in-place)\b[^|;&]*/etc/"),
    ("it destroys database tables or rows",
     r"\b(drop\s+(table|database)|truncate\s+table)\b|\bdelete\s+from\s+\w+\s*(;|$|\"|')"),
    ("it runs code passed on the command line",
     r"(^|[\s;&|(])(ba|z|k|da)?sh\s+-c\b|\b(python3?|perl|ruby)\s+-(c|e)\b|\bnode\s+-e\b"),
    ("it throws away uncommitted work in git",
     r"\bgit\s+(-c\s+\S+\s+|-C\s+\S+\s+)*(reset\s+--hard|clean\s+-[a-z]*f|checkout\s+--\s|restore\s)"),
)

_send: Optional[Callable[[str], Awaitable[object]]] = None
_pending: Optional[tuple[str, asyncio.Future]] = None


def set_sender(send: Callable[[str], Awaitable[object]]) -> None:
    """Where approval requests go: the user's DM. Unset, nothing can be approved."""
    global _send
    _send = send


def risk(command: str) -> Optional[str]:
    """Why a command needs the user's approval, or None."""
    text = (command or "").lower()
    for why, pattern in _RISKY:
        if re.search(pattern, text):
            return why
    return None


async def ask(command: str, why: str) -> tuple[bool, str]:
    """Wait for the user's yes or no. (approved, reason it did not run)."""
    global _pending
    if _send is None:
        return False, "approvals cannot reach the user from here"
    if _pending is not None and not _pending[1].done():
        return False, "another command is already waiting for the user's approval"
    future = asyncio.get_running_loop().create_future()
    _pending = (command, future)
    shown = command if len(command) <= 400 else command[:400] + " ..."
    try:
        await _send(f"IGOR wants to run a command that needs your OK, because {why}:\n"
                    f"`{shown}`\nReply **yes** to run it or **no** to stop it. "
                    f"No reply in {TIMEOUT_S // 60} minutes counts as no.")
        approved = await asyncio.wait_for(asyncio.shield(future), timeout=TIMEOUT_S)
        return approved, "" if approved else "the user said no"
    except asyncio.TimeoutError:
        return False, f"the user did not answer within {TIMEOUT_S // 60} minutes"
    except Exception as e:
        logger.error("Approval request failed - %s: %s", type(e).__name__, e)
        return False, f"the request could not be sent ({type(e).__name__})"
    finally:
        _pending = None


def answer(text: str) -> Optional[str]:
    """If a command is waiting and this message is a yes or no, settle it and return
    the reply to send. Anything else returns None and is handled as a normal message."""
    if _pending is None or _pending[1].done():
        return None
    word = re.sub(r"[.!]+$", "", (text or "").strip().lower())
    if word in _YES:
        _pending[1].set_result(True)
        logger.info("Approval given: %s", _pending[0][:120])
        return "Approved. Running it now."
    if word in _NO:
        _pending[1].set_result(False)
        logger.info("Approval refused: %s", _pending[0][:120])
        return "Stopped. It will not run."
    return None


def waiting() -> Optional[str]:
    """The command waiting for approval, if any."""
    return _pending[0] if _pending is not None and not _pending[1].done() else None
