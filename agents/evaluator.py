import logging
from typing import Awaitable, Callable

import config

logger = logging.getLogger(__name__)

_DEFAULT_SYSTEM_PROMPT = """You are I.G.O.R.'s Evaluator - an independent quality check on agent output before it is delivered to the user.

You receive a task and the response produced for it. Judge only whether the response fulfills the task's contract:
- Complete: not cut off mid-sentence, no sections the task asked for missing
- On-task: addresses what was actually asked, not an adjacent topic
- Format: matches the requested output shape (document, list, summary)
- Clean: no tool call syntax, no internal system notes, no meta-commentary about how the response was produced

Respond with exactly one line:
PASS
FAIL: [one concrete sentence - what is missing or wrong]

A response that fulfills the contract passes even if you would have written it differently. Do not fail a response for style preferences.

Style:
- No emojis
- No em dashes - use plain hyphens
- No exclamation points
- No casual filler phrases ("Sure!", "Of course!", "Happy to help!")"""


def _get_system_prompt() -> str:
    path = config.MEMORY_DIR / "prompt_evaluator.md"
    if path.exists():
        content = path.read_text(encoding="utf-8").strip()
        if content:
            return content
    return _DEFAULT_SYSTEM_PROMPT


async def evaluate(task: str, response: str, call_claude: Callable[..., Awaitable[str]]) -> tuple[bool, str]:
    """Judge response against task contract. Returns (passed, feedback).

    Fails open: any error in the evaluator itself counts as a pass, so the
    evaluator can degrade IGOR's output quality checks but never its availability.
    """
    messages = [{"role": "user", "content": f"Task:\n{task[:1500]}\n\nResponse:\n{response[:6000]}"}]
    try:
        verdict = (await call_claude(_get_system_prompt(), messages, 1024)).strip()
    except Exception as e:
        logger.error("Evaluator failed open - %s: %s", type(e).__name__, e)
        return True, ""

    first_line = verdict.splitlines()[0].strip() if verdict else ""
    if first_line.upper().startswith("FAIL"):
        feedback = first_line.split(":", 1)[1].strip() if ":" in first_line else "contract not met"
        logger.info("Evaluator FAIL: %s", feedback[:120])
        return False, feedback
    if not first_line.upper().startswith("PASS"):
        logger.warning("Evaluator verdict unparseable, failing open: %s", first_line[:80])
    return True, ""
