"""Commands answered in code, with no model call: Hermes' gateway slash commands.

Asking "how many tokens are left" cost a router call and a 120b call to read one file.
Each of these is the whole message, with or without a slash, so "help me find a desk" and
"status of my order" stay ordinary requests. The work is done in orchestrator.py, which
holds the turn they act on.
"""

import re
from typing import Optional

_COMMAND = re.compile(r"^\s*/?(stop|retry|undo|usage|status|help|skills)\s*[.!?]*\s*$", re.IGNORECASE)
_SKILL_ACTION = re.compile(r"^\s*/?(archive|restore|pin|unpin)\s+skill\s+([\w-]+)\s*[.!?]*\s*$", re.IGNORECASE)
# "skills" alone is a common word in this user's job searches, so "what skills does this
# role need" must stay an ordinary request. Only a question about IGOR's own matches.
_SKILL_LIST = re.compile(r"^\s*(?:show(?: me)?|list|what are|which are|what)\s+(?:(?:your|the|all|learned)\s+)*"
                         r"skills(?:\s+(?:do you have|have you learned|you have|you learned))?\s*[.!?]*\s*$",
                         re.IGNORECASE)
_SAVE_SKILL = re.compile(r"^\W*(?:(?:please|igor|can you|could you|would you|let'?s)[,\s]+)*"
                         r"(?:save|keep|make|turn|store|remember)\s+(?:"
                         r"(?:that|this|it)\s+(?:(?:as|into)\s+)?(?:an?\s+)?(?:new\s+)?skill\b"
                         r"|(?:an?\s+)?(?:new\s+)?skill\s+(?:from|of|for|out of)\s+(?:that|this|it)\b)",
                         re.IGNORECASE)

HELP = ("These take no model call. stop ends what I am working on. retry runs your last message "
        "again. undo removes your last message and my reply from the conversation; files stay as "
        "they are. usage shows the tokens spent today. status shows what is running and what is "
        "scheduled. rollback lists file changes I can put back. heartbeat every 1h, with a link, "
        "watches a page. skills lists what I have learned; archive, restore, pin or unpin skill "
        "with its name changes one. save that as a skill drafts one from the last task, for you "
        "to approve.")


def parse(text: str) -> Optional[str]:
    m = _COMMAND.match(text or "")
    if m:
        return m.group(1).lower()
    return "skills" if _SKILL_LIST.match(text or "") else None


def skill_action(text: str) -> Optional[tuple[str, str]]:
    m = _SKILL_ACTION.match(text or "")
    return (m.group(1).lower(), m.group(2)) if m else None


def saves_skill(text: str) -> bool:
    return bool(_SAVE_SKILL.match(text or ""))
