import asyncio
import functools
import logging
import re
from datetime import datetime, timezone
from typing import Awaitable, Callable

import openai

import config
import llm

logger = logging.getLogger(__name__)

# Exact commands only. The broad substring triggers this replaced sent any message
# containing "digest", "watchlist" or "scheduler" to the read-only Monitor, so
# "deep research on digest formats" never reached the research branch and
# "drop weather from the digest" hit an agent that cannot write. The router owns
# intent now; these are here purely so a known command never costs a model call.
_DIGEST_COMMANDS = frozenset({
    "trigger digest", "run digest", "send digest",
    "fire digest", "morning digest",
})

# Deviates from the GAMEPLAN text, which ended the CONFIG line with "preferences".
# Live testing showed llama-3.1-8b reading opinion questions ("what do you think
# about self hosting") as CONFIG, because "preferences" reads as "opinions" as
# easily as "saved settings". CONFIG is now stated as an action on stored config,
# and CHAT explicitly claims opinion questions.
_ROUTER_PROMPT = """Classify the user message into exactly one word from this list:
CHAT - greetings, small talk, opinions, what you think about something, how you are doing, anything social
SELF - questions ABOUT how IGOR is built or what it can do: its agents, models, routing, tools, memory, scheduler, architecture, design, limits. Also asking what IGOR can or cannot do, or how one of its capabilities could be used or extended
TASK - requests to DO work: search, write, analyze, code, read files, produce documents, calculations
MONITOR - asking for a current value: schedule times, watchlist contents, digest contents, system health
CONFIG - requests to CHANGE a saved setting: add or remove a digest section, change a schedule time, edit the watchlist
RESEARCH - asking to START a long-running investigation, and naming the subject to investigate
Reply with the single word only.

Four rules that override the above:
A message that mentions research, or talks about the research feature, without asking to start one is CHAT.
Asking what a setting is, or how something is built or configured, is never CONFIG. CONFIG requires wanting something changed.
Asking how IGOR works, or what IGOR can do, is SELF, not CHAT and not TASK, even though the subject is IGOR.
Naming one of IGOR's tools while asking for work to be done is TASK, not SELF. "Use your shell tool to check disk space" is TASK. "What does your shell tool do" is SELF."""

_VERDICT_MAP = {
    "CHAT": "Direct",
    "SELF": "SelfDescribe",
    "TASK": "React",
    "MONITOR": "Monitor",
    "CONFIG": "ConfigEdit",
    "RESEARCH": "ResearchLoop",
}

_ROUTER_TIMEOUT_S = 15

# GAMEPLAN A.2. Every correction the user types is a labelled example of what IGOR
# got wrong, and until now all of it was discarded when the context window rolled.
# This logs the pair and nothing else: no model call, no inference, no injection.
# Deciding what a correction MEANS is V.1's job, done in batch over an accumulated
# corpus - one correction looks like noise, five of the same shape are a preference.
#
# Explicit markers only. A false positive is worse than a miss here: it would teach
# IGOR a preference the user never expressed, which is how six junk skills
# accumulated in July. "actually" and "don't" alone are far too common in ordinary
# requests to qualify.
_CORRECTION_PATTERNS = [
    re.compile(p, re.IGNORECASE) for p in (
        r"^\s*(no|nope|nah)\b\s*[,.\-]",
        r"\bthat'?s (not right|not correct|wrong|incorrect|not what)\b",
        r"\bthat is (wrong|incorrect|not right)\b",
        r"\bnot what i (meant|asked|said|wanted)\b",
        # "i said" alone is narration far more often than correction ("I said I
        # would check later"), so it is deliberately absent.
        r"\bi (meant|asked for)\b",
        r"\byou (missed|misunderstood|misread)\b",
        r"\byou (got|had) .{0,25}\bwrong\b",
        r"\bshould(n'?t| not) have\b",
        r"\bactually,?\s+(no\b|that'?s (not|wrong|incorrect)|it'?s not)",
        r"\bwrong\b[^.?!]{0,30}\b(answer|file|one|thing|agent|source)\b",
    )
]

# Rotate rather than prune by age: capture must never fail, and a size check is one
# stat call. Age-based curation belongs with V.1's review pass.
_CORRECTIONS_MAX_BYTES = 200_000


def _looks_like_correction(message: str) -> bool:
    return any(p.search(message) for p in _CORRECTION_PATTERNS)

# Storage is cheap, the per-request token budget is not, so these are separate
# numbers. _STORE_CAP is what goes to SQLite; the rest govern what is injected.
_STORE_CAP = 20000
_OLD_ENTRY_CAP = 700

# Per-destination context budgets, in characters. React carries 12 tool schemas
# (~1900 tokens) on an 8000 TPM model, so it has roughly 2500 tokens of room for
# history. Direct carries no tools on a 12000 bucket and can afford far more.
# One number cannot serve both.
_CONTEXT_BUDGET_REACT = 8500
_CONTEXT_BUDGET_DIRECT = 24000

# SelfDescribe gets almost no history on purpose. At 8000 chars it answered a
# question about disk usage with the contents of an earlier scheduler conversation:
# the document is the ground truth here and prior turns are mostly a source of stale
# claims to repeat. Two turns is enough to resolve "what about the other one".
_CONTEXT_BUDGET_SELF = 1500


def _cap(text: str, limit: int) -> str:
    if len(text) <= limit:
        return text
    return text[:limit] + "\n[truncated]"

_RESEARCH_LOOP_TRIGGERS = frozenset({
    "deep research",
    "research loop",
    "autoresearch",
    "start research loop",
})

_SYNTH_TRIGGERS = frozenset({
    "synthesize research", "synthesise research",
    "summarize research", "summarise research",
    "summarize the research", "summarise the research",
})

_STOP_RESEARCH_TRIGGERS = frozenset({
    "stop research",
    "stop the research",
    "end research",
    "halt research",
})

_CRITIC_PROMPT = """You are a skill evaluator for an AI assistant system.

Given a task and a response, decide if the approach used is worth capturing as a reusable skill.

Capture if the response shows:
- A multi-step search sequence that worked well (e.g. checked memory first, then searched for X before Y)
- A domain insight or constraint that filtered results usefully (e.g. applied hardware limits to narrow recommendations)
- A user-specific adaptation that improved relevance

Skip if:
- The response is a single lookup or a factual answer from training data
- The approach was obvious given the question
- Nothing about the method would help future responses
- The approach is just an output format or document structure (tables, section headings, bullet layouts, checklists) - formatting is not a skill
- A similar skill already appears in the existing skills list below

Respond with exactly one line:
CAPTURE: [one concrete sentence - what to do, not just what happened]
SKIP

One line only. No explanation."""

def _extract_research_question(content: str) -> tuple[str, int]:
    import re
    lower = content.lower()
    for trigger in sorted(_RESEARCH_LOOP_TRIGGERS, key=len, reverse=True):
        if lower.startswith(trigger):
            remainder = content[len(trigger):].lstrip(": ").strip()
            match = re.match(r'^\[(\d+)\]\s*', remainder)
            if match:
                n = max(1, min(int(match.group(1)), 100))
                return remainder[match.end():].strip(), n
            return remainder, 100
    return content.strip(), 100


# Intentionally empty. The critic below is disabled (config.ENABLE_CRITIC) and
# skills_react.md is gone: it injected unreviewed captures into every React prompt
# with nothing maintaining it, and one stale formatting entry spent weeks steering
# answers toward tables while prompt edits failed to override it. _critic_pass
# returns immediately on an empty map, so the machinery below is inert rather than
# writing to a file nothing reads. GAMEPLAN R4.4 replaces this with review files
# and explicit sign-off, and will not reuse this storage.
_SKILL_FILES: dict[str, str] = {}


def _write_skill(agent_name: str, content: str) -> None:
    filename = _SKILL_FILES.get(agent_name)
    if not filename:
        return
    path = config.MEMORY_DIR / filename
    try:
        with path.open("a", encoding="utf-8") as f:
            f.write(f"{content}\n")
        logger.info("Skill captured for %s", agent_name)
    except Exception as e:
        logger.error("Skill write failed for %s - %s: %s", agent_name, type(e).__name__, e)


# Type alias: a bound call_claude with client and notify already applied.
# Signature: async (system: str, messages: list[dict], max_tokens: int = 1024) -> str
CallClaude = Callable[..., Awaitable[str]]


async def call_claude(
    client: openai.AsyncOpenAI,
    notify: Callable[[str], Awaitable[None]],
    system: str,
    messages: list[dict],
    max_tokens: int = 1024,
    model: str | None = None,
) -> str:
    max_retries = 3
    for attempt in range(max_retries):
        try:
            return await llm.complete(
                client,
                model or config.MODELS["chat"],
                system,
                messages,
                max_tokens=max_tokens,
                label="Chat",
            )
        except openai.RateLimitError:
            if attempt < max_retries - 1:
                wait = 30 * (2 ** attempt)
                logger.error("Rate limit hit (attempt %d/%d), retrying in %ds", attempt + 1, max_retries, wait)
                await notify(f"Rate limit reached. Retrying in {wait} seconds...")
                await asyncio.sleep(wait)
            else:
                logger.error("Rate limit exhausted after %d attempts", max_retries)
                return "Rate limit exhausted. Please try again in a few minutes."
        except (openai.APIStatusError, openai.APIConnectionError) as e:
            logger.error("Groq API error %s: %s", type(e).__name__, e)
            raise
    return "Unexpected error reaching Groq API."


class Orchestrator:
    def __init__(self, notify: Callable[[str], Awaitable[None]], notify_file: Callable[[str], Awaitable[None]] | None = None) -> None:
        self._client = openai.AsyncOpenAI(
            api_key=config.GROQ_API_KEY,
            base_url="https://api.groq.com/openai/v1",
            max_retries=5,
        )
        self._notify = notify
        self._notify_file = notify_file or notify
        from context_store import load
        self._context: list[dict] = load()

    async def process(self, user_id: int, content: str) -> tuple[str, bool] | None:
        """Entry point for every incoming message.

        Returns None for unauthorized users (silent drop, no acknowledgment).
        Returns (response, as_file) where as_file signals the bot to send a file attachment.
        Prefixes: "file: <task>" sends response as a downloadable file.
        """
        if user_id != config.AUTHORIZED_USER_ID:
            return None

        file_mode = content.lower().startswith("file:")
        task = content[5:].strip() if file_mode else content

        # A "file:" request is always document work regardless of what it asks
        # for, so it skips the router and goes straight to the tool agent.
        destination = "React" if file_mode else await self._classify(task)

        # Before _update_context runs, so the last assistant entry is still the reply
        # being corrected rather than the one about to be written.
        if _looks_like_correction(task):
            self._log_correction(task, destination)
        if destination == "StopResearch":
            file_mode = True

        try:
            response = await self._route(destination, task, file_mode=file_mode)
        except openai.RateLimitError:
            logger.error("Route to %s rate limited after all retries", destination)
            return "Groq free tier rate limit hit. Wait a minute and try again - shorter requests recover faster.", False
        except openai.APIStatusError as e:
            if "rate_limit_exceeded" in str(e) or "Request too large" in str(e):
                logger.error("Route to %s request too large for TPM budget: %s", destination, str(e)[:200])
                return "That request plus conversation context exceeds the per-minute token budget. Wait a minute, then try a shorter message - or split the task into smaller steps.", False
            logger.error("Route to %s failed - %s: %s", destination, type(e).__name__, e)
            return f"Something went wrong ({type(e).__name__}). Details have been logged.", False
        except Exception as e:
            logger.error("Route to %s failed - %s: %s", destination, type(e).__name__, e)
            return f"Something went wrong ({type(e).__name__}). Details have been logged.", False

        if destination in ("ResearchLoop", "StopResearch"):
            return response, file_mode

        skill_captured = False
        if config.ENABLE_CRITIC:
            skill_captured = await self._critic_pass(destination, task, response)
        self._update_context(task, response)
        if file_mode:
            return response, file_mode
        parts = []
        if destination == "Monitor":
            parts.append("`[Monitor]`")
        if skill_captured:
            parts.append("`[Skill captured]`")
        suffix = "\n\n" + " ".join(parts) if parts else ""
        return f"{response}{suffix}", file_mode

    async def _classify(self, content: str) -> str:
        lower = content.lower().strip()

        # Before the research triggers: "synthesize research" is about findings
        # that already exist, not a request to gather more.
        if any(trigger in lower for trigger in _SYNTH_TRIGGERS):
            return "SynthesizeResearch"
        if any(lower.startswith(trigger) for trigger in _RESEARCH_LOOP_TRIGGERS):
            return "ResearchLoop"
        if any(trigger in lower for trigger in _STOP_RESEARCH_TRIGGERS):
            return "StopResearch"
        if lower in _DIGEST_COMMANDS:
            return "Monitor"

        # No fast path for self-referential questions. A regex over nouns cannot tell
        # "tell me about your shell tool" from "use your shell tool" - same tokens,
        # opposite intent - and the one deployed here hijacked ordinary task requests
        # to a tool-less agent. Rule-based routing fails on exactly this case, which
        # is why the SELF verdict below belongs to the router: the classification is
        # semantic, and only a model can make it.

        # The router runs alone on its 8000 TPM bucket and reserves 10 tokens, so
        # it costs almost nothing. Called directly rather than through
        # call_claude because call_claude's rate-limit path notifies the user and
        # sleeps 30s or more, which is the wrong behaviour for classification -
        # better to fail fast to React than to make the user wait to be routed.
        #
        # reasoning_effort "none" is load-bearing, not a saving. Every model Groq
        # still offers is a reasoning model, and a 10-token budget is spent on
        # hidden reasoning before a verdict is ever emitted: measured 2026-08-18,
        # the same call without it returns an unterminated think tag, which
        # _VERDICT_MAP misses, so every message routes to React and nothing logs
        # an error. llm.model_params adds the reasoning_format that keeps that
        # think text out of content in the first place.
        try:
            response = await asyncio.wait_for(
                self._client.chat.completions.create(
                    model=config.MODELS["router"],
                    messages=[
                        {"role": "system", "content": _ROUTER_PROMPT},
                        {"role": "user", "content": content[:1000]},
                    ],
                    max_tokens=10,
                    temperature=0,
                    **llm.model_params(config.MODELS["router"], reasoning_effort="none"),
                ),
                timeout=_ROUTER_TIMEOUT_S,
            )
            raw = (response.choices[0].message.content or "").strip().upper()
        except Exception as e:
            logger.warning("Router unavailable (%s: %s) - defaulting to React", type(e).__name__, e)
            return "React"

        verdict = raw.split()[0].strip(".,:;\"'") if raw.split() else ""
        destination = _VERDICT_MAP.get(verdict, "React")
        logger.info("Router: %s -> %s", verdict or "(empty)", destination)
        return destination

    async def _critic_pass(self, destination: str, task: str, response: str) -> bool:
        if destination not in _SKILL_FILES:
            return False
        call = functools.partial(call_claude, self._client, self._notify)
        skills_path = config.MEMORY_DIR / _SKILL_FILES[destination]
        existing = skills_path.read_text(encoding="utf-8").strip() if skills_path.exists() else "(none)"
        messages = [{"role": "user", "content": f"Existing skills:\n{existing}\n\nTask: {task}\n\nResponse:\n{response}"}]
        try:
            verdict = await call(_CRITIC_PROMPT, messages, max_tokens=1024, model=config.MODELS["summary"])
            verdict = verdict.strip()
            logger.info("Critic verdict for %s: %s", destination, verdict[:80])
            if verdict.upper().startswith("CAPTURE:"):
                _write_skill(destination, verdict[8:].strip())
                return True
        except Exception as e:
            logger.error("Critic pass failed for %s - %s: %s", destination, type(e).__name__, e)
        return False

    def _make_caller(self, file_mode: bool = False) -> CallClaude:
        base = functools.partial(call_claude, self._client, self._notify)
        if not file_mode:
            return base

        async def _file_caller(system: str, messages: list, max_tokens: int = 4096) -> str:
            return await base(system, messages, max_tokens)

        return _file_caller

    async def _route(self, destination: str, content: str, file_mode: bool = False) -> str:
        from agents import monitor, react, research_loop

        if destination == "ResearchLoop":
            question, iterations = _extract_research_question(content)
            return await research_loop.start(question, self._notify, notify_file=self._notify_file, max_iterations=iterations)

        if destination == "StopResearch":
            return await research_loop.stop()

        if file_mode:
            content = content + "\n\n[File output: Write a comprehensive detailed report with full prose, section headers, and thorough coverage. Return the complete document as your response text - do not write it to a server file, do not call write_file, do not mention restarts.]"
        call = self._make_caller(file_mode=file_mode)
        max_tokens = 3072 if file_mode else 2048

        if destination == "Monitor":
            return await monitor.handle(content, self._window(), call)

        if destination == "Direct":
            from agents import direct
            # Direct carries no tool schemas on the 12000 bucket, so it can hold far
            # more history than React. This is what makes "what did you just say"
            # answerable in chat.
            return await direct.handle(content, self._window(_CONTEXT_BUDGET_DIRECT), call)

        if destination == "SelfDescribe":
            from agents import self_describe
            self_result = await self_describe.handle(content, self._window(_CONTEXT_BUDGET_SELF))
            if self_result is not None:
                return self_result
            logger.info("SelfDescribe declined the message, forwarding to React")

        if destination == "ConfigEdit":
            from agents import prod_memory
            config_result = await prod_memory.handle(content, call)
            if config_result is not None:
                return config_result
            # ConfigEdit read the message and said it was not a config change. That
            # is a better signal than the router's guess, so hand it to React rather
            # than failing at the user. Costs one extra call on a misroute.
            logger.info("ConfigEdit declined the message, forwarding to React")

        if destination == "SynthesizeResearch":
            # React has research.md in its memory_read allowlist, but it will not
            # know to open it from "synthesize research" alone. Say so explicitly.
            content = (
                "Read the memory file research.md and write a condensed synthesis of "
                "what it contains: the strongest through-lines across iterations, "
                "what is well supported by sources, what is thin or single-sourced, "
                "and what is still open. Keep the source URLs for anything you cite. "
                "Do not go and search for anything new.\n\n"
                f"The user asked: {content}"
            )

        react.set_notify(self._notify)
        response = await react.handle(content, self._window(), max_tokens=max_tokens)

        if file_mode:
            from agents import evaluator
            passed, feedback = await evaluator.evaluate(content, response)
            if not passed:
                retry_content = (
                    f"{content}\n\n[Your previous attempt was rejected by an independent evaluator: "
                    f"{feedback}. Produce a corrected, complete response.]"
                )
                response = await react.handle(retry_content, self._window(), max_tokens=max_tokens)
                passed, feedback = await evaluator.evaluate(content, response)
                if not passed:
                    response = f"[Evaluator warning: {feedback}]\n\n{response}"
        return response

    def _window(self, char_budget: int = _CONTEXT_BUDGET_REACT) -> list[dict]:
        """Newest turns in full, older turns compressed, within a char budget.

        "Reread that", "fix the third point", "you missed X" all refer to the most
        recent thing said. That entry earns its tokens; a turn from twenty minutes
        ago does not. Older entries keep their opening, which is enough to know what
        was discussed without carrying the whole thing.

        Spends the budget newest-first, so whatever the user is most likely talking
        about survives whole and the compression falls on the oldest turns.
        """
        window = self._context[-config.CONTEXT_WINDOW:]
        if not window:
            return []

        # Greedy newest-first: each entry takes what it needs, up to what is left.
        # No per-entry floor and no reservation, so unused budget flows backwards to
        # older turns instead of being held for a newest entry that does not want it.
        # Reserving up front looked safe but starved the wrong turn: a digest sat at
        # 700 chars while 20000 went unused because the newest entry was a 25-char
        # acknowledgement.
        out, remaining = [], char_budget
        for m in reversed(window):
            if remaining <= 0:
                break
            content = m.get("content") or ""
            if len(content) > remaining:
                content = _cap(content, remaining)
            remaining -= len(content)
            out.append({**m, "content": content})
        return list(reversed(out))

    def _update_context(self, user_msg: str, assistant_msg: str) -> None:
        """Store generously, inject sparingly.

        This used to truncate to 1500 chars at write time, which destroyed the text
        rather than merely leaving it out of the prompt. A 5500-char reply was kept
        as 1500, so "reread that and fix X" had nothing to reread - and the model
        answered from the fragment without saying so. Disk is free; the per-request
        budget is what is scarce, so the trimming belongs in _window().
        """
        from context_store import append
        user_msg = _cap(user_msg, _STORE_CAP)
        assistant_msg = _cap(assistant_msg, _STORE_CAP)
        self._context.append({"role": "user", "content": user_msg})
        self._context.append({"role": "assistant", "content": assistant_msg})
        if len(self._context) > config.CONTEXT_WINDOW:
            self._context = self._context[-config.CONTEXT_WINDOW:]
        append("user", user_msg)
        append("assistant", assistant_msg)

    def _log_correction(self, user_msg: str, destination: str) -> None:
        """Append a correction and what it was correcting. Never raises.

        Capture only. This file is deliberately NOT in React's memory_read allowlist:
        letting an agent pull it into a reply would be injection by another name, and
        the whole point is that nothing acts on it until reviewed.
        """
        prior = next((m for m in reversed(self._context) if m.get("role") == "assistant"), None)
        if prior is None:
            return
        try:
            path = config.MEMORY_DIR / "corrections.md"
            if path.exists() and path.stat().st_size > _CORRECTIONS_MAX_BYTES:
                stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
                path.rename(config.MEMORY_DIR / f"corrections_{stamp}.md")
            with path.open("a", encoding="utf-8") as f:
                f.write(
                    f"\n## {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}\n"
                    f"Answered by: {destination}\n\n"
                    f"IGOR said:\n> {_cap(prior.get('content') or '', 600).replace(chr(10), chr(10) + '> ')}\n\n"
                    f"User corrected:\n> {_cap(user_msg, 600).replace(chr(10), chr(10) + '> ')}\n"
                )
            logger.info("Logged a correction against %s", destination)
        except Exception as e:
            logger.error("Correction logging failed - %s: %s", type(e).__name__, e)

    def record_outbound(self, text: str) -> None:
        """Record something IGOR sent without being asked.

        _update_context only runs for incoming messages, so proactive sends - the
        morning digest, research reports, watchlist alerts, the send_message tool -
        never entered context at all. IGOR would send the user a digest at 09:00 and
        have no record of it at 14:00, which is exactly what happened on 2026-08-08.

        Stored as an assistant turn with no paired user message, because there was
        no user message. The marker matters: without it the model reads this as a
        reply to whatever came before.
        """
        if not text or not text.strip():
            return
        entry = {"role": "assistant", "content": _cap(f"[sent proactively]\n{text.strip()}", _STORE_CAP)}
        self._context.append(entry)
        if len(self._context) > config.CONTEXT_WINDOW:
            self._context = self._context[-config.CONTEXT_WINDOW:]
        from context_store import append
        append("assistant", entry["content"])
        logger.info("Recorded proactive message into context (%d chars)", len(text))

    def reset_context(self) -> None:
        self._context.clear()
        from context_store import clear
        clear()
