import asyncio
import logging
import time

import config

logger = logging.getLogger(__name__)

_FOCUS_PROMPT = ("What does this page say that answers: {query}? Only what the page states, with "
                 "numbers exactly as written, in at most two sentences. If it says nothing on this, "
                 "say so in three words.")
_KEYLESS_RETRY_WAIT = 2


async def _run_search(
    query: str,
    max_results: int = 5,
    start_published_date: str | None = None,
    max_chars: int = 500,
    use_summary: bool = False,
    exclude_domains: list[str] | None = None,
    focus: str | None = None,
    raise_errors: bool = False,
    highlights: str | None = None,
    include_domains: list[str] | None = None,
    keyless: bool = True,
) -> list[dict]:
    """Search Exa and return title, url, published date and body text.

    max_chars is the per-result text budget. 500 is enough for React, which reads
    many results and pays for them inside an 8000 TPM turn. It is NOT enough for
    summarising an article: page chrome eats it first. A real result looked like
    title, byline, "Skip to content", "Toggle Navigation", a nav link - 500
    characters with no article prose at all, which is how a story about AI
    designing bacteriophages was reported to the user as computer viruses. The
    synthesiser had only the headline. Callers that summarise should ask for more.

    focus asks Exa for what each page says on that question instead. Live 2026-10-30,
    React spent 45000 tokens pricing one product of eight, searching Notion three times
    because a page's first 500 characters seldom hold the answer. For one query focus
    returned "The cheapest paid plan is Plus at $10 per member per month." in 59
    characters, for $0.003 more and a second longer. Falls back to raw text if it fails.

    highlights asks for the page's own words on that question, cut to max_chars. A summary is
    Exa's model talking, so a quote from one cannot be checked against the page; these can.

    keyless=False refuses the DDGS backup, for a caller that must not take its short undated
    snippets, which also ignore include_domains, as a real look.
    """
    def _sync(focus: str | None = focus) -> list[dict]:
        from exa_py import Exa
        exa = Exa(api_key=config.EXA_API_KEY)
        # Exa's raw text is the page from byte zero, so navigation, cookie banners
        # and repeated titles consume the budget before any prose. Measured on one
        # article: 500 chars of chrome, and 1500 chars of chrome. "highlights" with no
        # query was no better. summary=True returned the actual content - it named
        # Stanford, the Arc Institute and bacteriophage genomes, none of which appeared
        # in 1500 characters of raw text. Highlights given a query do reach the point:
        # live 2026-10-31 they returned the revenue figures from four searches of four.
        if focus:
            kwargs: dict = {"num_results": max_results,
                            "summary": {"query": _FOCUS_PROMPT.format(query=focus[:300])}}
        elif highlights:
            kwargs = {"num_results": max_results, "text": {"max_characters": max_chars},
                      "highlights": {"query": highlights[:300], "num_sentences": 3, "highlights_per_url": 2}}
        elif use_summary:
            kwargs = {"num_results": max_results, "summary": True}
        else:
            kwargs = {"num_results": max_results, "text": {"max_characters": max_chars}}
        if start_published_date:
            kwargs["start_published_date"] = start_published_date
        if exclude_domains:
            kwargs["exclude_domains"] = exclude_domains
        if include_domains:
            kwargs["include_domains"] = include_domains
        response = exa.search_and_contents(query, **kwargs)

        def _body(r) -> str:
            if highlights and getattr(r, "highlights", None):
                return " ... ".join(r.highlights)[:max_chars]
            return (getattr(r, "summary", None) or r.text or "") if (use_summary or focus) else (r.text or "")

        return [
            {
                "title": r.title or "No title",
                "url": r.url,
                "body": _body(r),
                # Exa returns this and it was being discarded, so callers had no way
                # to notice a result was years old. React reported December 2024
                # releases as "the latest AI tech" in August 2026 because of it.
                "published": (getattr(r, "published_date", None) or "")[:10],
            }
            for r in response.results
        ]

    try:
        loop = asyncio.get_running_loop()
        try:
            return await loop.run_in_executor(None, _sync)
        except Exception as e:
            if not focus:
                raise
            logger.warning("Exa focused search failed, using raw text - %s: %s", type(e).__name__, e)
            return await loop.run_in_executor(None, _sync, None)
    except Exception as e:
        logger.error("Exa search error - %s: %s", type(e).__name__, e)
        fallback = await _keyless_search(query, max_results) if keyless else []
        if fallback:
            return fallback
        if raise_errors:
            raise
        return []


async def _keyless_search(query: str, max_results: int) -> list[dict]:
    """Hermes' DDGS backend, for when Exa cannot answer: digest, overnight research and React
    all stop without it, and its credits can run out. Needs no key. Results carry a snippet
    and no date, so they are thinner; that beats none."""
    def _sync() -> list[dict]:
        from ddgs import DDGS
        # Live 2026-11-01 3 of 11 first tries said "No results found", two on a query that had just
        # worked. The one retried after a pause came back.
        try:
            rows = DDGS(timeout=15).text(query, max_results=max_results)
        except Exception:
            time.sleep(_KEYLESS_RETRY_WAIT)
            rows = DDGS(timeout=15).text(query, max_results=max_results)
        return [{"title": r.get("title") or "No title", "url": r.get("href") or "",
                 "body": r.get("body") or "", "published": ""} for r in rows]

    try:
        loop = asyncio.get_running_loop()
        results = await loop.run_in_executor(None, _sync)
        if results:
            logger.warning("Search fell back to DDGS, %d results", len(results))
        return results
    except ImportError:
        return []
    except Exception as e:
        logger.error("DDGS search error - %s: %s", type(e).__name__, e)
        return []


async def _page_answer(url: str, question: str) -> str:
    """What one page says on a question. "" when it cannot say."""
    def _sync() -> str:
        from exa_py import Exa
        exa = Exa(api_key=config.EXA_API_KEY)
        response = exa.get_contents([url], summary={"query": _FOCUS_PROMPT.format(query=question[:300])})
        return (getattr(response.results[0], "summary", None) or "") if response.results else ""

    try:
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(None, _sync)
    except Exception as e:
        logger.error("Exa page answer error for %s - %s: %s", url, type(e).__name__, e)
        return ""


async def _page_text(url: str, max_chars: int = 30000) -> str:
    """The page's own text, for checking a claim against it. "" when it cannot be read."""
    def _sync() -> str:
        from exa_py import Exa
        exa = Exa(api_key=config.EXA_API_KEY)
        response = exa.get_contents([url], text={"max_characters": max_chars})
        return (response.results[0].text or "") if response.results else ""

    try:
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(None, _sync)
    except Exception as e:
        logger.error("Exa contents error for %s - %s: %s", url, type(e).__name__, e)
        return ""


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
