"""Answers questions about IGOR itself, from ARCHITECTURE.md and nothing else.

Exists because React could not do this job. React runs on an 8000 TPM bucket with a
7000 ceiling, and its whole budget is spoken for by tool schemas and history. Three
attempts to make it answer accurately all failed, and the third made things worse:
injecting 1000 tokens of architecture into its prompt pushed it over the ceiling, so
_trim_to_budget forced a final answer at iteration 1 with no tools at all, which
guaranteed the parametric fabrication it was meant to prevent.

The fix is not to negotiate inside that budget. It is to take the question somewhere
that has room. This agent carries no tools, so the whole 26KB document fits on the
12000 bucket with space to spare, and there is no tool loop to starve.

It also cannot fabricate a subsystem, because it has no source of detail except the
file. If something is not in there, it has nothing to say about it, which is the
correct answer.

Returns None when the message turns out not to be about IGOR's construction, and the
orchestrator hands it to React - the same fall-through ConfigEdit uses, for the same
reason: a reading of the actual message beats a router guess.
"""

import logging
import re
from typing import Optional

import config
import llm

logger = logging.getLogger(__name__)

# Nouns that only come up when the subject is IGOR's own construction, paired with a
# second-person or by-name reference. Either order: "what tools can you use" and
# "your scheduled system" both have to match, and a pronoun-first pattern missed the
# first one.
_SELF_WORD = r"(?:igor|your|yours|you)"
_SYSTEM_WORD = (
    r"(?:scheduler|schedule|scheduled|agent|agents|tool|tools|memory|model|models|"
    r"router|routing|architecture|codebase|code|system|systems|capabilit|config|"
    r"configuration|deploy|prompt|abilit|built|internals|pipeline)"
)
_SELF_QUESTION_RE = re.compile(
    rf"\b{_SELF_WORD}\b[^.?!]{{0,80}}?\b{_SYSTEM_WORD}"
    rf"|\b{_SYSTEM_WORD}[^.?!]{{0,80}}?\b{_SELF_WORD}\b",
    re.IGNORECASE,
)

# ARCHITECTURE.md is ~26KB, about 6600 tokens, and this runs on the 12000 bucket with
# max_tokens 1024. The cap is headroom for the file growing, not a current limit.
_MAX_DOC_CHARS = 34000

_DECLINE = "NOT_ABOUT_IGOR"

_SYSTEM_PROMPT = f"""You answer questions about how IGOR itself is built, using the ARCHITECTURE.md document below and nothing else.

The document is verified against the source code. It is the only thing you know about IGOR's construction. You have no tools and no other source.

Rules:
- Answer only from the document. If it does not cover something, say that you do not have it documented rather than guessing.
- NEVER describe a file, module, function, tool, config file, or safety feature that does not appear in the document. Inventing one is the worst thing you can do here, because the user may act on it.
- The document has a section listing what does NOT exist. If the user assumes one of those things is real, correct them directly and plainly.
- Earlier messages in the conversation may contain wrong descriptions of IGOR, including systems that were never built. The document wins. Do not repeat a claim from the conversation that the document contradicts.
- Be concrete. Name the real files and real functions from the document.
- If the user is asking you to DO something rather than asking how IGOR is built, reply with exactly {_DECLINE} and nothing else.

Style:
- No emojis
- No em dashes - use plain hyphens
- No exclamation points
- No casual filler phrases ("Sure!", "Of course!", "Happy to help!")

=== ARCHITECTURE.md ===
{{document}}
=== end of ARCHITECTURE.md ==="""


def looks_self_referential(message: str) -> bool:
    return bool(_SELF_QUESTION_RE.search(message))


def _load_document() -> str:
    """Read per call, so an edit to ARCHITECTURE.md takes effect without a restart."""
    try:
        return (config.BASE_DIR / "ARCHITECTURE.md").read_text(encoding="utf-8")[:_MAX_DOC_CHARS]
    except Exception as e:
        logger.error("SelfDescribe could not read ARCHITECTURE.md - %s: %s", type(e).__name__, e)
        return ""


async def handle(message: str, context: list[dict]) -> Optional[str]:
    document = _load_document()
    if not document:
        return None

    from agents.react import _get_client

    answer = await llm.complete(
        _get_client(),
        config.MODELS["chat"],
        _SYSTEM_PROMPT.format(document=document),
        context + [{"role": "user", "content": message}],
        max_tokens=1024,
        label="SelfDescribe",
    )

    if not answer or answer.strip().upper().startswith(_DECLINE):
        logger.info("SelfDescribe declined, message is a task not a question about IGOR")
        return None
    return answer
