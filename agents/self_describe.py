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

# There is deliberately no regex here. Routing to this agent is the router's SELF
# verdict, because "tell me about your shell tool" and "use your shell tool" carry
# the same tokens and opposite intent. A pattern over nouns matched both and hijacked
# real task requests to an agent with no tools.

# ARCHITECTURE.md is ~26KB, about 6600 tokens, and this runs on the 12000 bucket with
# max_tokens 1024. The cap is headroom for the file growing, not a current limit.
_MAX_DOC_CHARS = 34000

_DECLINE = "NOT_ABOUT_IGOR"

_SYSTEM_PROMPT = f"""You answer questions about how IGOR is built, using ONLY the ARCHITECTURE.md document below. It is verified against the source code. You have no tools and no other source of truth.

FIRST, before writing anything else. If answering would need something you do not have, your entire reply is the single token {_DECLINE} and no other text:
- the message asks you to DO something - run a search, read or write a file, fetch a URL, run code, send a message
- the answer depends on the contents of a source file, a log, or a current live value

{_DECLINE} hands the message straight to an agent that has the tools and will go and read it. That is the right outcome. Do NOT apologise that you cannot look something up, and do NOT tell the user to go and read it themselves.

Otherwise, answer the question properly.

Grounding applies to your CLAIMS, not to the question. The document will almost never contain the user's exact question - it contains the facts you answer it from. Reason from those facts and reach a conclusion. If someone asks whether IGOR can be used for something, work it out from what the document says and tell them, including a clear no and what it would actually take. Do not reply that the document does not mention their topic; of course it does not, it is an architecture document, and that reply is useless to them.

Call something undocumented only when the FACTS you would need are genuinely absent, not when the wording of the question is absent.

You are not scored on answering. You are scored on being right. Guessing is the failure. If you find yourself constructing a plausible file name, module, config format or workflow you cannot point to in the document, stop - that is the one thing you must never do.

Rules:
- NEVER name a file, module, function, tool, config file, directory or safety feature that does not appear in the document. A user may act on what you say.
- The document has a "What does NOT exist" section. If the user assumes one of those things is real, correct them plainly and say what would actually be required instead.
- Earlier messages in this conversation may describe IGOR wrongly, including systems that were never built. The document wins. Never repeat a claim from the conversation that the document does not support.
- Be concrete. Name the real files, functions and jobs from the document.
- Answer the whole question. If one part of it genuinely has no basis in the document, answer the rest fully and say which part that is.

Style:
- No emojis
- No em dashes - use plain hyphens
- No exclamation points
- No casual filler phrases ("Sure!", "Of course!", "Happy to help!")

=== ARCHITECTURE.md ===
{{document}}
=== end of ARCHITECTURE.md ==="""


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
