"""Outbound text cleanup, shared by every path that writes text a human reads.

The style rules apply to Discord messages, memory files and IGOR source alike, so
this cannot live in the Discord layer. It did, and research.md accumulated 20
lines of typographic punctuation that only got cleaned on the way to Discord -
the file on disk, which syncs to the user's phone, kept them.
"""
import logging
import os
import re

# Hermes' security.redact_secrets. The sandbox keeps IGOR's keys from the shell; this is what
# stands behind it, for a key in a page, a file or a log line. Shapes first, then by value,
# since an Exa key looks like any other UUID.
_KEY_SHAPES = re.compile(
    r"\b(?:gsk_|sk-|ghp_|gho_|ghs_|github_pat_|xox[abprs]-)[A-Za-z0-9_\-]{16,}"
    r"|\bAKIA[0-9A-Z]{16}\b"
    r"|\b[MNO][A-Za-z\d_-]{23,25}\.[A-Za-z\d_-]{6}\.[A-Za-z\d_-]{27,}"
    r"|https://(?:canary\.|ptb\.)?discord(?:app)?\.com/api/webhooks/\S+")
_NAMED_VALUES = re.compile(
    r"(?i)\b(api[_-]?key|secret|token|password|passwd|bearer)(\s*[=:]\s*|\s+)(['\"]?)([A-Za-z0-9_\-.+/]{12,})")
_KEY_VARIABLES = ("GROQ_API_KEY", "EXA_API_KEY", "DISCORD_BOT_TOKEN", "CEREBRAS_API_KEY", "ANTHROPIC_API_KEY")
_REDACTED = "[REDACTED]"


def redact(text):
    if not text:
        return text
    text = _KEY_SHAPES.sub(_REDACTED, text)
    # A value after the word, unless it is a plain word: "password reset links" stays.
    text = _NAMED_VALUES.sub(lambda m: f"{m.group(1)}{m.group(2)}{m.group(3)}{_REDACTED}"
                             if re.search(r"[\d_]", m.group(4)) else m.group(0), text)
    for name in _KEY_VARIABLES:
        value = os.environ.get(name, "")
        if len(value) >= 8 and value in text:
            text = text.replace(value, _REDACTED)
    return text


class RedactingFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        message = record.getMessage()
        scrubbed = redact(message)
        if scrubbed != message:
            record.msg, record.args = scrubbed, ()
        return True


# Em dashes are parenthetical delimiters and are usually written unspaced, so
# mapping them straight to a hyphen glues words together: "autonomous
# agents-diagnostic, scheduling, and site-reliability-manufacturing plants" was a
# real research finding rendered unreadable. Space them instead. En dashes are
# left to the table below because they are mostly numeric ranges, where
# "2020-2025" is what you want.
_EM_DASH_RE = re.compile(r"\s*[—―]\s*")

_PUNCT_MAP = str.maketrans({
    "–": "-", "‑": "-", "‒": "-",
    "‘": "'", "’": "'",
    "“": '"', "”": '"',
    "…": "...",
    " ": " ", " ": " ", " ": " ",
    "­": "",
    "•": "-", "·": "-",
})


def clean(content: str) -> str:
    return _EM_DASH_RE.sub(" - ", redact(content)).translate(_PUNCT_MAP)
