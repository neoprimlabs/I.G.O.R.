"""Outbound text cleanup, shared by every path that writes text a human reads.

The style rules apply to Discord messages, memory files and IGOR source alike, so
this cannot live in the Discord layer. It did, and research.md accumulated 20
lines of typographic punctuation that only got cleaned on the way to Discord -
the file on disk, which syncs to the user's phone, kept them.
"""
import re

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
    return _EM_DASH_RE.sub(" - ", content).translate(_PUNCT_MAP)
