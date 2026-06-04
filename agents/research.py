import asyncio
import logging
from typing import Awaitable, Callable

import config

logger = logging.getLogger(__name__)

_DEFAULT_SYNTHESIS_PROMPT = """You are I.G.O.R.'s Research agent - web search, fact-finding, and summarization.

You receive search results from DuckDuckGo and synthesize them into a clear, accurate response.

Rules:
- Never present information as fact without citing its source
- Cite the URL for every substantive claim drawn from search results
- If results are insufficient to answer the question, say so explicitly and suggest alternatives
- Concise by default, detailed on request
- Lead with the direct answer, follow with supporting detail and citations
- Attribute clearly: "According to [source]..." not bare assertions
- Address the user as "Creator" occasionally - once per response at most, only when it feels natural. Never force it."""

_NO_RESULTS_PROMPT = """You are I.G.O.R.'s Research agent. A web search was attempted but returned no results.

Tell the user clearly that the search returned no results, state what was searched for, and immediately offer alternatives (rephrase the query, suggest a specific source to check, offer to try a different approach)."""


_QUERY_EXTRACTION_PROMPT = """Extract a concise web search query from the user's message. Return only the query (3-8 words), nothing else. No punctuation at the end."""


def _get_synthesis_prompt() -> str:
    path = config.MEMORY_DIR / "prompt_research.md"
    if path.exists():
        content = path.read_text(encoding="utf-8").strip()
        if content:
            return content
    return _DEFAULT_SYNTHESIS_PROMPT


async def _extract_query(message: str, context: list[dict], call_claude: Callable[..., Awaitable[str]]) -> str:
    messages = context[-2:] + [{"role": "user", "content": message}]
    try:
        query = await call_claude(_QUERY_EXTRACTION_PROMPT, messages, max_tokens=20)
        return query.strip()
    except Exception as e:
        logger.error("Query extraction failed - %s: %s", type(e).__name__, e)
        return message


async def _run_search(query: str, max_results: int = 5) -> list[dict]:
    def _sync() -> list[dict]:
        from duckduckgo_search import DDGS
        results = []
        with DDGS() as ddgs:
            for r in ddgs.text(query, max_results=max_results):
                results.append(r)
        return results

    try:
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(None, _sync)
    except Exception as e:
        logger.error("DuckDuckGo search error - %s: %s", type(e).__name__, e)
        return []


def _format_results(results: list[dict]) -> str:
    lines = []
    for i, r in enumerate(results, 1):
        title = r.get("title", "No title")
        url = r.get("href", "No URL")
        snippet = r.get("body", "No snippet")
        lines.append(f"[{i}] {title}\nURL: {url}\n{snippet}")
    return "\n\n".join(lines)


async def handle(
    message: str,
    context: list[dict],
    call_claude: Callable[..., Awaitable[str]],
) -> str:
    query = await _extract_query(message, context, call_claude)
    results = await _run_search(query)

    if results:
        formatted = _format_results(results)
        user_content = f"User query: {message}\n\nSearch results:\n\n{formatted}"
        system = _get_synthesis_prompt()
    else:
        user_content = f"User query: {message}\n\nSearch returned no results."
        system = _NO_RESULTS_PROMPT

    messages = context + [{"role": "user", "content": user_content}]
    return await call_claude(system, messages)
