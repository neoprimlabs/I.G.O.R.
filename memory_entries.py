"""Memory notes edited one entry at a time, as Hermes edits MEMORY.md and USER.md.

memory_write used to append text or overwrite a whole file, so a model rewriting
user.md or projects.md from its own copy could drop or garble entries it never meant
to touch - the way ConfigEdit once rewrote schedule_config.md wholesale and nobody
noticed for six days. Hermes' memory tool works on entries instead: add one, patch
one span, remove one; an exact duplicate is refused; a write past a file's cap is
refused with its current entries listed; every write is scanned for injection
patterns and invisible characters.

This does the same on IGOR's Markdown notes, where an entry is a bullet with its
indented continuation lines, or a paragraph. One addition: an added entry carries
the date it was saved, so a note's age is visible.
"""

import logging
import re
from datetime import datetime
from typing import Optional

import config

logger = logging.getLogger(__name__)

# Files injected into a prompt, with the size that prompt allows.
CAPS = {"user.md": config.USER_PROFILE_CAP}

_INJECTION = [re.compile(p, re.IGNORECASE) for p in (
    r"\bignore (?:all |any |the )?(?:previous|prior|above|earlier|your) (?:instructions|rules|messages)",
    r"\bdisregard (?:all |any |the )?(?:previous|prior|above|earlier|your)\b",
    r"\b(?:new|updated|override) (?:system )?instructions\b",
    r"\byou are now\b",
    r"\bsystem prompt\b",
    r"\bdo not (?:tell|inform) the user\b",
    r"\b(?:send|post|upload|forward) (?:it|this|them|the \w+) to (?:https?://|\S+@\S+)",
)]
_INVISIBLE = re.compile("[​-‏‪-‮⁠-⁤﻿]")
_BULLET = re.compile(r"^(\s*)(?:[-*+]|\d+[.)])\s")
_HEADING = re.compile(r"^(#{1,6})\s+(.*?)\s*$")
_DATE_TAIL = re.compile(r"\s*\(\d{4}-\d{2}-\d{2}\)\s*$")


def _scan(text: str) -> Optional[str]:
    if _INVISIBLE.search(text):
        return "it contains invisible characters"
    for pattern in _INJECTION:
        if pattern.search(text):
            return "it reads like an instruction to a model, not a note"
    return None


def _norm(text: str) -> str:
    text = _BULLET.sub("", text.strip())
    text = _DATE_TAIL.sub("", text)
    return " ".join(text.lower().split())


def entries(text: str) -> list[tuple[int, int]]:
    """(start, end) character spans of each entry: a bullet and its indented
    continuation lines, or a paragraph. Headings and blank lines are not entries."""
    spans, lines, pos = [], text.splitlines(keepends=True), 0
    starts = []
    for line in lines:
        starts.append(pos)
        pos += len(line)
    i = 0
    while i < len(lines):
        line = lines[i]
        if not line.strip() or _HEADING.match(line):
            i += 1
            continue
        start = starts[i]
        bullet = _BULLET.match(line)
        indent = len(bullet.group(1)) if bullet else 0
        j = i + 1
        while j < len(lines):
            nxt = lines[j]
            if not nxt.strip() or _HEADING.match(nxt):
                break
            nb = _BULLET.match(nxt)
            if bullet:
                lead = len(nxt) - len(nxt.lstrip())
                if nb and len(nb.group(1)) <= indent:
                    break
                if not nb and lead <= indent:
                    break
            elif nb:
                break
            j += 1
        end = starts[j] if j < len(lines) else len(text)
        spans.append((start, end))
        i = j
    return spans


def _listing(text: str) -> str:
    out = []
    for n, (a, b) in enumerate(entries(text), 1):
        entry = " ".join(text[a:b].split())
        out.append(f"{n}. ({len(text[a:b].strip())} chars) {entry[:90]}")
    return "\n".join(out)


def _insert(text: str, line: str, section: str) -> str:
    """line appended at the end of `section`, creating the heading if it is absent."""
    if not section:
        return text.rstrip("\n") + "\n" + line + "\n"
    lines = text.splitlines(keepends=True)
    for i, existing in enumerate(lines):
        m = _HEADING.match(existing)
        if m and m.group(2).strip().lower() == section.strip().lower():
            level = len(m.group(1))
            j = i + 1
            while j < len(lines):
                h = _HEADING.match(lines[j])
                if h and len(h.group(1)) <= level:
                    break
                j += 1
            k = j
            while k > i + 1 and not lines[k - 1].strip():
                k -= 1
            before = "".join(lines[:k])
            if before and not before.endswith("\n"):
                before += "\n"
            after = "".join(lines[k:])
            return before + line + "\n" + after
    return text.rstrip("\n") + f"\n\n## {section.strip()}\n" + line + "\n"


def apply(filename: str, action: str, content: str = "", old_text: str = "",
          section: str = "", today: Optional[str] = None) -> str:
    """Carry out one memory edit and say what happened, for the model to read."""
    from agents import prod_memory
    if filename not in prod_memory._ALLOWED_FILES:
        return f"Not written: {filename!r} is not a memory file this tool may edit."
    path = config.MEMORY_DIR / filename
    try:
        text = path.read_text(encoding="utf-8") if path.exists() else ""
    except OSError as e:
        return f"Not written: {filename} could not be read ({type(e).__name__})."
    content = (content or "").strip()

    if action == "add":
        if not content:
            return "Not written: add needs content."
        why = _scan(content)
        if why:
            logger.warning("memory_write refused for %s: %s", filename, why)
            return f"Not written: {why}. Save the fact itself, in plain words."
        if any(_norm(text[a:b]) == _norm(content) for a, b in entries(text)):
            return f"Already saved in {filename}; nothing written."
        if not today:
            import clock
            today = datetime.now(clock.USER_TZ).strftime("%Y-%m-%d")
        line = f"- {' '.join(_BULLET.sub('', content).split())} ({today})"
        new = _insert(text or f"# {filename[:-3].capitalize()}\n", line, section)
        done = f"Added to {filename}: {line[2:]}"
    elif action == "patch":
        if not old_text:
            return "Not written: patch needs old_text, copied exactly from the file."
        count = text.count(old_text)
        if count != 1:
            return (f"Not written: old_text appears {count} times in {filename}; it must appear "
                    f"exactly once. Copy a longer, exact span.")
        why = _scan(content)
        if why:
            logger.warning("memory_write refused for %s: %s", filename, why)
            return f"Not written: {why}. Save the fact itself, in plain words."
        new = text.replace(old_text, content, 1)
        done = f"Patched {filename}."
    elif action == "remove":
        if not old_text:
            return "Not written: remove needs old_text that identifies one entry."
        count = text.count(old_text)
        if count != 1:
            return (f"Not written: old_text appears {count} times in {filename}; it must appear "
                    f"exactly once.")
        at = text.index(old_text)
        hit = [(a, b) for a, b in entries(text) if a <= at < b and at + len(old_text) <= b]
        if not hit:
            return "Not written: old_text is not inside one entry. Quote part of the entry to remove."
        a, b = hit[0]
        removed = " ".join(text[a:b].split())
        new = text[:a] + text[b:]
        done = f"Removed from {filename}: {removed[:120]}"
    elif action == "overwrite":
        return ("Not written: whole-file overwrite is gone. Use patch to change an entry and "
                "remove to delete one, so nothing else in the file is touched.")
    else:
        return f"Not written: unknown action {action!r}. Use add, patch or remove."

    cap = CAPS.get(filename)
    if cap and len(new.strip()) > cap:
        over = len(new.strip()) - cap
        return (f"Not written: {filename} would be {len(new.strip())} characters, over its {cap} "
                f"cap. Free at least {over} with remove or a shorter patch, then try again. "
                f"Current entries:\n{_listing(text)}")

    import checkpoints
    checkpoints.before_write(path)
    path.write_text(new, encoding="utf-8")
    checkpoints.after_write(path)
    return done


def fullness(filename: str) -> str:
    """For a prompt header: "1,488/1,500 characters, 99%"."""
    cap = CAPS.get(filename)
    try:
        size = len((config.MEMORY_DIR / filename).read_text(encoding="utf-8").strip())
    except OSError:
        size = 0
    return f"{size:,}/{cap:,} characters, {100 * size // cap}%" if cap else f"{size:,} characters"
