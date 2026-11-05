"""Links in a reply must come from something IGOR read.

2026-11-05: asked for entry-level remote jobs, React searched the job boards and listed five. Three
links were wrong: two RemoteOK postings with made-up numbers, and a real RemoteOK posting given an
address on We Work Remotely that never existed. The search results were real; the model wrote the
addresses from memory instead of copying them. A prompt rule only asks, so this is checked in code:
every link in a reply has to appear in what a tool returned that turn, in the user's message, or in
the conversation the reply was written from. Any other link is taken out, and the reply says how many,
so whatever it pointed to reads as unconfirmed.
"""

import re

_URL = re.compile(r"https?://[^\s<>\"'`\])]+", re.IGNORECASE)
_MD_LINK = re.compile(r"\[([^\]\n]+)\]\((https?://[^\s)]+)\)", re.IGNORECASE)
_TRAILING = ".,;:!?*_"


def _norm(url: str) -> str:
    """Scheme, a leading www., case and closing punctuation do not make a different page."""
    url = url.rstrip(_TRAILING).rstrip("/").lower()
    url = re.sub(r"^https?://", "", url)
    return url[4:] if url.startswith("www.") else url


# What was read can name a site without its scheme ("Website: kettle.example"), and a reply may then
# write it with one. Only links in a reply are checked, so a file name caught here does no harm.
_BARE = re.compile(r"(?<![\w@/.-])(?:[a-z0-9-]+\.)+[a-z]{2,}(?:/[^\s<>\"'`\])]*)?", re.IGNORECASE)


def found(text: str) -> set:
    text = text or ""
    return {_norm(u) for u in _URL.findall(text)} | {_norm(u) for u in _BARE.findall(text)}


def _keep_seen(reply: str, seen: set) -> tuple:
    removed = 0

    def markdown(match):
        nonlocal removed
        if _norm(match.group(2)) in seen:
            return match.group(0)
        removed += 1
        return match.group(1)

    def bare(match):
        nonlocal removed
        url = match.group(0)
        core = url.rstrip(_TRAILING)
        if _norm(core) in seen:
            return url
        removed += 1
        return url[len(core):]

    text = _URL.sub(bare, _MD_LINK.sub(markdown, reply))
    return text, removed


def guard(reply: str, seen: set) -> str:
    """The reply with every link it could not have read taken out, and a line saying so."""
    if not reply or not _URL.search(reply):
        return reply
    text, removed = _keep_seen(reply, seen)
    if not removed:
        return reply
    text = re.sub(r"[ \t]+\n", "\n", text).rstrip()
    return (f"{text}\n\n-# {removed} link{'s' if removed != 1 else ''} taken out: not in anything I read "
            f"for this reply, so treat what {'they' if removed != 1 else 'it'} pointed to as unconfirmed.")


def in_conversation(messages: list) -> set:
    return set().union(*(found(str(m.get("content") or "")) for m in messages or [])) if messages else set()
