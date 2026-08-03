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
            {
                "title": r.title or "No title",
                "url": r.url,
                "body": r.text or "",
                # Exa returns this and it was being discarded, so callers had no way
                # to notice a result was years old. React reported December 2024
                # releases as "the latest AI tech" in August 2026 because of it.
                "published": (getattr(r, "published_date", None) or "")[:10],
            }
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
        published = r.get("published") or "date unknown"
        lines.append(
            f"[{i}] {r.get('title', 'No title')}\n"
            f"URL: {r.get('url', '')}\n"
            f"Published: {published}\n"
            f"{r.get('body', '')}"
        )
    return "\n\n".join(lines)
