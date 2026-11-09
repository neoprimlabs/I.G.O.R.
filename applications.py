"""What an application draft says about the user must come from what IGOR holds about them.

2026-11-01: asked for a cover letter, Direct wrote one claiming an evaluation framework running BLEU,
ROUGE and "GPT-Eval", a model router at 31/31, weekly metric reports to stakeholders and a 12% accuracy
gain. Of those, only the count of automated checks is in the resume; the rest was made up, in a letter
written for the user to send under their own name. A prompt rule only asks, so this is checked in code,
as links.py checks links: in a reply shaped like an application, every figure and every named tool or
acronym has to appear in the resume, the user's notes and profile, the conversation, or what a tool
returned that turn. A sentence or bullet holding one that does not is taken out, and the reply says so.
"""

import re

import config

_CLOSE = re.compile(r"\b(?:best regards|kind regards|warm regards|sincerely|regards,|"
                    r"thank you for (?:your )?consider)", re.IGNORECASE)
_OPEN = re.compile(r"^\W*(?:dear|hi|hello|to)\b[^\n]{0,60}(?:hiring|manager|team|recruit|\[)",
                   re.IGNORECASE | re.MULTILINE)
_ABOUT = re.compile(r"\b(?:cover letter|application|apply|applying)\b", re.IGNORECASE)

_FIGURE = re.compile(
    r"(?<![\w.])(?:\$\s?\d[\d,]*(?:\.\d+)?\s?[kKmM]?\b|\d+(?:\.\d+)?\s?(?:%|percent\b)|\d+\s?/\s?\d+|"
    r"\d{1,3}(?:,\d{3})+(?:\.\d+)?|\d{2,}(?:\.\d+)?)")
# Acronyms of three letters or more (BLEU, GPT-Eval) and names cased like products (SageMaker).
_NAME = re.compile(r"\b(?:[A-Z][A-Z0-9]{2,}(?:-[A-Za-z0-9]+)?|[A-Z][a-z]+[A-Z][A-Za-z0-9]*)\b")
# Letter words, not claims about the user.
_NOT_CLAIMS = frozenset({"usa", "ceo", "cto", "faq", "pdf", "est", "pst", "cst", "utc", "asap", "llc",
                         "inc", "fyi", "ps", "linkedin"})
_YEAR = re.compile(r"^(?:19[5-9]\d|20[0-4]\d)$")
_BULLET = re.compile(r"^\s*(?:[-*•]|\d+[.)])\s")
_SENTENCE = re.compile(r"(?<=[.!?])\s+(?=[A-Z\"“(*])")
_HELD = ("resume.md", "private/profile.md", "user.md", "preferences.md")


def from_conversation(messages: list) -> str:
    """The user's own words and what IGOR posted unprompted. IGOR's other replies and the summary are
    not a source: run over the real 11-01 letter, BLEU, ROUGE and GPT-Eval were held only because
    Direct had already invented them in an earlier reply about the posting."""
    return "\n".join(str(m.get("content") or "") for m in messages or []
                     if m.get("role") == "user" or str(m.get("content") or "").startswith("[sent proactively"))


def is_application(reply: str, message: str = "") -> bool:
    return bool(_CLOSE.search(reply or "")) and bool(
        _OPEN.search(reply or "") or _ABOUT.search(message or "") or _ABOUT.search(reply or ""))


def _held() -> str:
    parts = []
    for name in _HELD:
        try:
            parts.append((config.MEMORY_DIR / name).read_text(encoding="utf-8"))
        except OSError:
            pass
    return "\n".join(parts)


def _plain(text: str) -> str:
    """Lower case, I.G.O.R. as IGOR, 2,000 as 2000, 12 % and 12 percent as 12%, no dollar signs."""
    text = re.sub(r"(?<=[A-Za-z])\.(?=[A-Za-z]\b)", "", text or "")
    text = re.sub(r"(?<=\d),(?=\d{3})", "", text.lower())
    text = re.sub(r"(?<=\d)\s*(?:%|percent\b)", "%", text)
    text = re.sub(r"(?<=\d)\s*/\s*(?=\d)", "/", text)
    return text.replace("$", "")


def _unheld(unit: str, held: str) -> list[str]:
    missing = []
    for found in _FIGURE.finditer(unit):
        figure = _plain(found.group(0)).replace(" ", "")
        if _YEAR.match(figure):
            continue
        if not re.search(rf"(?<![\d.]){re.escape(figure)}(?!\d)", held):
            missing.append(found.group(0).strip())
    for found in _NAME.finditer(unit):
        name = _plain(found.group(0))
        if name in _NOT_CLAIMS:
            continue
        if not re.search(rf"(?<![a-z0-9]){re.escape(name)}(?![a-z0-9])", held):
            missing.append(found.group(0))
    return missing


def guard(reply: str, message: str = "", read: str = "") -> str:
    """The draft without any sentence or bullet whose figures or named tools nothing IGOR holds or read
    shows, and a line saying what was taken out. Anything that is not an application passes untouched."""
    if not reply or not is_application(reply, message):
        return reply
    held = _plain("\n".join((_held(), message or "", read or "")))
    removed: list[str] = []
    lines = []
    for line in reply.split("\n"):
        if _BULLET.match(line):
            missing = _unheld(line, held)
            if missing:
                removed += missing
                continue
            lines.append(line)
            continue
        kept = []
        for unit in _SENTENCE.split(line):
            missing = _unheld(unit, held)
            if missing:
                removed += missing
            else:
                kept.append(unit)
        if kept or not line.strip():
            lines.append(" ".join(kept))
    if not removed:
        return reply
    text = re.sub(r"\n{3,}", "\n\n", "\n".join(lines)).rstrip()
    named = ", ".join(dict.fromkeys(removed))
    return (f"{text}\n\n-# Taken out of the draft, as nothing on file or in this conversation shows it: "
            f"{named}.")
