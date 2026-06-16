import asyncio
import logging

import config

logger = logging.getLogger(__name__)


async def _run_search(query: str, max_results: int = 5, start_published_date: str | None = None) -> list[dict]:
    def _sync() -> list[dict]:
        from exa_py import Exa
        exa = Exa(api_key=config.EXA_API_KEY)
        kwargs: dict = {"num_results": max_results, "text": {"max_characters": 500}}
        if start_published_date:
            kwargs["start_published_date"] = start_published_date
        response = exa.search_and_contents(query, **kwargs)
        return [
            {"title": r.title or "No title", "url": r.url, "body": r.text or ""}
            for r in response.results
        ]

    try:
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(None, _sync)
    except Exception as e:
        logger.error("Exa search error - %s: %s", type(e).__name__, e)
        return []


def _format_results(results: list[dict]) -> str:
    lines = []
    for i, r in enumerate(results, 1):
        lines.append(f"[{i}] {r.get('title', 'No title')}\nURL: {r.get('url', '')}\n{r.get('body', '')}")
    return "\n\n".join(lines)
