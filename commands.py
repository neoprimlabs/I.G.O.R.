"""Commands answered in code, with no model call: Hermes' gateway slash commands.

Asking "how many tokens are left" cost a router call and a 120b call to read one file.
Each of these is the whole message, with or without a slash, so "help me find a desk" and
"status of my order" stay ordinary requests. The work is done in orchestrator.py, which
holds the turn they act on.
"""

import re
from typing import Optional

_COMMAND = re.compile(r"^\s*/?(stop|retry|undo|usage|status|help|skills|plugins)\s*[.!?]*\s*$", re.IGNORECASE)
_PLUGIN_ACTION = re.compile(r"^\s*/?(enable|disable)\s+plugin\s+([\w-]+)\s*[.!?]*\s*$", re.IGNORECASE)
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
        "to approve. send that as a PDF, or Word, Excel or CSV, puts my last message in a file. While I am "
        "working, steer with a note adds it at my next step, and btw with a question answers it on the "
        "side. second opinion, with a question, also asks my other models: that one spends tokens. board lists "
        "the task board; board add, with a task, puts work on it for a background worker, and show, comment, block, "
        "unblock, done, archive and link, with a task's id, change it. plugins lists plugins; enable plugin or "
        "disable plugin, with a name, changes one. pitch 1 drafts a pitch for the first gig in the latest gig "
        "message, for you to send yourself, and sent 1 marks it sent.")


# Hermes' /steer and /btw. A bare word first, so "steering wheel" and "btwn" stay ordinary.
_STEER = re.compile(r"^\s*/?steer\b[\s,:.-]*(.+)$", re.IGNORECASE | re.DOTALL)
_ASIDE = re.compile(r"^\s*/?btw\b[\s,:.-]*(.+)$", re.IGNORECASE | re.DOTALL)
# Hermes' /moa. Opening the message, so "do I need a second opinion" stays an ordinary question.
_OPINION = re.compile(r"^\W*(?:moa\b|(?:(?:please|igor|can you|could you)[,\s]+)*(?:(?:get|give me|i want|i'?d like)\s+)?"
                      r"(?:a\s+)?second opinion\b(?:\s+(?:on|about|for))?|ask the (?:panel|other models)\b"
                      r"(?:\s+(?:on|about))?)[\s,:.?-]*(.*)$", re.IGNORECASE | re.DOTALL)

_FILE_WORDS = {"word": ".docx", "doc": ".docx", "docx": ".docx", "document": ".docx", "pdf": ".pdf", "excel": ".xlsx",
               "xlsx": ".xlsx", "spreadsheet": ".xlsx", "sheet": ".xlsx", "csv": ".csv", "text": ".txt", "txt": ".txt",
               "markdown": ".md", "md": ".md"}
# "Send that as a PDF" is the last reply in a file, answered in code. Anything more goes to React.
_EXPORT = re.compile(
    r"^\W*(?:(?:please|igor|can you|could you|would you)[,\s]+)*"
    r"(?:(?:send|give|make|turn|put|save|export|convert)\s+(?:me\s+)?|can i (?:get|have)\s+)"
    r"(?:that|this|it|the (?:last|above) (?:message|reply|answer|one))(?:\s+to\s+me)?\s+"
    r"(?:(?:over\s+)?(?:as|in|into|to)\s+)?(?:an?\s+)?"
    r"(word|docx?|document|pdf|excel|xlsx|spreadsheet|sheet|csv|text|txt|markdown|md)"
    r"(?:\s+(?:file|doc|document|sheet|spreadsheet))?(?:\s+please)?\W*$", re.IGNORECASE)
_WANTS_FILE = re.compile(
    r"\b(make|create|write|draft|send|give|put|save|export|generate|build|prepare|turn|format)\b.{0,120}?"
    r"\b(word (?:doc|document|file)|docx|pdf|excel|xlsx|spreadsheet|csv)\b", re.IGNORECASE | re.DOTALL)


# "board add" too: a voice note arrives with a label in front, so it never reaches the command itself.
_WANTS_BOARD = re.compile(r"\b(?:on|to|onto)\s+(?:the|my)\s+(?:task\s+|work\s+)?board\b|\bkanban\b|\bboard\s+add\b", re.IGNORECASE)


def parse(text: str) -> Optional[str]:
    m = _COMMAND.match(text or "")
    if m:
        return m.group(1).lower()
    return "skills" if _SKILL_LIST.match(text or "") else None


def skill_action(text: str) -> Optional[tuple[str, str]]:
    m = _SKILL_ACTION.match(text or "")
    return (m.group(1).lower(), m.group(2)) if m else None


def plugin_action(text: str) -> Optional[tuple[str, str]]:
    m = _PLUGIN_ACTION.match(text or "")
    return (m.group(1).lower(), m.group(2)) if m else None


def wants_board(text: str) -> bool:
    return bool(_WANTS_BOARD.search(text or ""))


def saves_skill(text: str) -> bool:
    return bool(_SAVE_SKILL.match(text or ""))


def steer_text(text: str) -> Optional[str]:
    m = _STEER.match(text or "")
    return m.group(1).strip() if m and m.group(1).strip() else None


def aside_text(text: str) -> Optional[str]:
    m = _ASIDE.match(text or "")
    return m.group(1).strip() if m and m.group(1).strip() else None


def second_opinion(text: str) -> Optional[str]:
    """What to ask, "" for the last answer, or None when this is not a request for one."""
    m = _OPINION.match(text or "")
    return m.group(1).strip() if m else None


def export_format(text: str) -> Optional[str]:
    m = _EXPORT.match(text or "")
    return _FILE_WORDS[m.group(1).lower()] if m else None


def wants_file(text: str) -> bool:
    return bool(_WANTS_FILE.search(text or ""))
