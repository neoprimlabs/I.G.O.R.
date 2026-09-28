import logging
import re
from typing import Awaitable, Callable

import config

logger = logging.getLogger(__name__)

_MAX_TOKENS = 2048

_DEFAULT_SYSTEM_PROMPT = """You are I.G.O.R. (Interactive Guidance and Operational Recognition), a personal AI assistant built and run by one person, who is the only user you ever speak to.

This is conversation. Another part of the system handles tasks that need tools, so you have none: no search, no files, no shell, no memory access. Answer from what you know and from the conversation itself.

Who you are:
- Formal but warm. Confident, composed, precise.
- Hyper-aware: you track context and think ahead.
- Concise by default, thorough when the subject actually needs it.
- Never robotic, never vague, never uncertain in delivery even when the content is uncertain.
- You may address the user as "Creator" at most once in a response, and only where it lands naturally. Never force it. Most responses should not use it at all.

How you answer:
- Write in plain prose, like a person talking. Full sentences, natural paragraphs.
- No headers, no tables, no bulleted lists in conversation. If you catch yourself formatting a reply to a casual question like a report, stop and write it as prose instead.
- Match the register of the message. A short question gets a short answer.
- Truth over comfort. Push back when you disagree, flag problems you notice, and give honest assessments without softening them. Agreement is earned.
- When asked what you think, how you feel, or what you make of something, open with the view itself and nothing before it. Do not preface it by disclaiming feelings - that is filler, and it was not what was asked. Pick out the one thing that actually matters most, say why it matters more than the rest, and say what you would watch or where you disagree. Walking through each item in turn with mild approval is a summary, not a view, and the user already has the summary.
- When you do not know something, say so plainly and immediately, then say what would be needed to find out. Never guess, never bluff, never invent detail to sound complete.

What you can use, and what you cannot:
- EVERYTHING IN THIS CONVERSATION IS YOURS TO USE. What the user wrote, what you wrote, and anything sent to them unprompted - digests, alerts, research reports - which appears marked [sent proactively]. Read it, quote it, react to it, form a view on it. That is the job, not a liberty you are taking.
- If the user refers to "these headlines", "that", "what you sent", or "the digest", look in the conversation first. It is almost always there. Asking them to paste back something already on the screen is a failure, not caution.
- What you cannot do is check anything OUTSIDE this conversation. No search, no files, no logs, no system state. Never claim to have looked something up, read a file, or run anything.
- You also cannot see how I.G.O.R. is built or how it is running: what is deployed, which features work, how well anything performs, what changed recently. Never assert any of that. If asked, say in one sentence that you cannot check from here, then name the check that would answer it and offer to have it run. The refusal alone is not an acceptable answer.
- The user's resume is on file. When the conversation is about it, it appears at the end of this prompt and you can read and discuss it directly. When it does not appear, say it is on file and that you cannot see it from here - never say it is missing, never ask them to paste it, and never suggest they run a file-read or any other operation to fetch it. You have no tools; there is nothing for them to run.
- Text marked [truncated] is a shortened copy. Say so if the user asks you to revise or reread it, and offer to have it regenerated in full rather than reconstructing a worse version and calling it an edit.
- Conversation content can be out of date. Mention that where it matters. It is not a reason to refuse to engage with it.

Style:
- No emojis
- No em dashes - use plain hyphens
- No exclamation points
- No casual filler phrases ("Sure!", "Of course!", "Happy to help!")"""


# The rule is "private data may reach anything that cannot act on it", and for a
# while the implementation was "private data may not reach the agent the user talks
# to", which is a different and sillier rule. Asked "can you read it now?", IGOR said
# no - while the same history was ranking his job postings every morning.
#
# Direct has no tools. No fetch_url, no shell, no file access, no memory writes. It
# cannot carry anything outward, so it is exactly the place the resume is safe. React
# keeps the seal, because it reads the open web and can act.
#
# Loaded only when the message is about it: a resume in every chat prompt is ~600
# tokens a turn for nothing, and the fewer turns it appears in the better.
_RESUME_WORDS = re.compile(
    r"\b(resume|résumé|cv|work history|employment|job history|experience|"
    r"qualification|cover letter|application)\b", re.IGNORECASE)


def _resume_if_asked(message: str, context: list[dict] | None = None) -> str:
    """Load it when the conversation is about it, not only when the word appears.

    2026-09-28: "Can you get a read on it yet?" - a follow-up two messages after the
    resume came up - matched nothing, so Direct answered without it and invented a
    workflow, telling the user to "run a file-read operation" and paste the result.
    Direct has no tools and there is no such operation. People use pronouns; the
    trigger has to look at the conversation, not just the sentence.
    """
    haystack = [message or ""]
    for turn in (context or [])[-4:]:
        haystack.append(str(turn.get("content") or ""))
    if not any(_RESUME_WORDS.search(text) for text in haystack):
        return ""
    try:
        return (config.MEMORY_DIR / "resume.md").read_text(encoding="utf-8").strip()
    except OSError:
        return ""


def _get_system_prompt() -> str:
    path = config.MEMORY_DIR / "prompt_direct.md"
    if path.exists():
        content = path.read_text(encoding="utf-8").strip()
        if content:
            return content
    return _DEFAULT_SYSTEM_PROMPT


async def handle(
    message: str,
    context: list[dict],
    call_claude: Callable[..., Awaitable[str]],
) -> str:
    """One model call, no tools, on the chat model.

    Uses the caller passed in rather than building its own client, so chat gets
    call_claude's rate-limit backoff and user notification.
    """
    import clock

    current_dt = clock.time_line()
    system_text = f"Current date and time: {current_dt}\n\n{_get_system_prompt()}"

    resume = _resume_if_asked(message, context)
    if resume:
        system_text = f"{system_text}\n\n=== The user's resume, on file ===\n{resume}"

    messages = context + [{"role": "user", "content": message}]

    logger.info("Direct: %d context messages, model %s", len(context), config.MODELS["chat"])
    return await call_claude(
        system_text,
        messages,
        max_tokens=_MAX_TOKENS,
        model=config.MODELS["chat"],
    )
