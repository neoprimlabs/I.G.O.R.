import asyncio
import logging
from typing import Awaitable, Callable

logger = logging.getLogger(__name__)

_SYNTHESIS_PROMPT = """You are I.G.O.R.'s Research agent — web search, fact-finding, and summarization.

You receive search results from DuckDuckGo and synthesize them into a clear, accurate response.

Rules:
- Never present information as fact without citing its source
- Cite the URL for every substantive claim drawn from search results
- If results are insufficient to answer the question, say so explicitly and suggest alternatives
- Concise by default, detailed on request
- Lead with the direct answer, follow with supporting detail and citations
- Attribute clearly: "According to [source]..." not bare assertions"""

_NO_RESULTS_PROMPT = """You are I.G.O.R.'s Research agent. A web search was attempted but returned no results.

Tell the user clearly that the search returned no results, state what was searched for, and immediately offer alternatives (rephrase the query, suggest a specific source to check, offer to try a different approach)."""


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
        logger.error("DuckDuckGo search error — %s: %s", type(e).__name__, e)
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
    results = await _run_search(message)

    if results:
        formatted = _format_results(results)
        user_content = f"User query: {message}\n\nSearch results:\n\n{formatted}"
        system = _SYNTHESIS_PROMPT
    else:
        user_content = f"User query: {message}\n\nSearch returned no results."
        system = _NO_RESULTS_PROMPT

    messages = context + [{"role": "user", "content": user_content}]
    return await call_claude(system, messages)
