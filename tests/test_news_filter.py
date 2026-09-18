"""The digest kept sending the same news cycle, and exclusions did nothing.

Run: python tests/test_news_filter.py   (repo root, stdlib only, no API calls)

Measured over the six digests stored in context.db on 2026-09-17: five of six were
dominated by AI safety discourse - slowdown calls, a hoax claim, a rogue-agent
warning, an oversight plan - because the query is "artificial intelligence news",
the top 5 results are whatever the cycle is chewing on, and nothing compares today's
results against what was already sent. The user's words: "I'm sick of seeing 3
headlines on ai safety every morning. They are not productive."

`digest_config.md` already had an Exclusions section. It was annotated
"pending code implementation" and never wired to anything, so what it listed was
ignored. These tests cover the three parts: parsing that section, filtering results
against it and against what was already sent, and remembering what went out.
"""

import json
import sys
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import config

_failures: list[str] = []


def _check(name: str, condition: bool, detail: str = "") -> None:
    if condition:
        print(f"  pass  {name}")
    else:
        print(f"  FAIL  {name}  {detail}")
        _failures.append(name)


NOW = datetime(2026, 9, 17, 13, 0, tzinfo=timezone.utc)

CONFIG_TEXT = """# Digest Config

## Sections
- tasks
- ai_news
- daily_forecast

## Exclusions
- Apple/iOS focused news
- AI safety debate, slowdown calls, regulation commentary
"""

# Shapes taken from real digests: the first two are what the user is tired of, the
# last two are the kind of thing they said they wanted instead.
SAFETY = {"url": "https://www.bloomberg.com/news/articles/2026-09-14/ai-bosses-safety-call",
          "title": "AI bosses risk clash with Wall Street over safety call",
          "summary": "Altman and Amodei call for a slower pace of frontier AI development."}
HOAX = {"url": "https://www.bbc.com/news/articles/cw980n0nd0qjo",
        "title": "Trump calls AI safety fears a hoax",
        "summary": "The president rejected calls for greater safeguards."}
RELEASE = {"url": "https://groq.com/blog/new-model",
           "title": "Groq serves a new 27B reasoning model",
           "summary": "Throughput and pricing details for the new endpoint."}
TOOLING = {"url": "https://github.blog/2026-09-16-agent-tooling",
           "title": "A smaller tool schema format for agents",
           "summary": "Cuts per-request token overhead for tool definitions."}
APPLE = {"url": "https://www.macrumors.com/2026/09/16/ios-ai",
         "title": "iOS 27 adds on-device summarisation",
         "summary": "Apple ships a new Neural Engine feature."}


def test_parse_exclusions() -> None:
    from agents import monitor

    terms = monitor._parse_exclusions(CONFIG_TEXT)
    _check("apple is excluded", "apple" in terms, str(terms))
    _check("ios is excluded", "ios" in terms, str(terms))
    _check("safety is excluded", "safety" in terms, str(terms))
    _check("slowdown is excluded", "slowdown" in terms, str(terms))
    _check("regulation is excluded", "regulation" in terms, str(terms))
    # "news", "ai" and friends would match every story ever fetched.
    for generic in ("news", "ai", "artificial", "intelligence", "focused", "commentary"):
        _check(f"the generic word {generic!r} is not a filter", generic not in terms, str(terms))
    _check("no Sections entries leak in as exclusions",
           "tasks" not in terms and "ai_news" not in terms, str(terms))


def test_filter() -> None:
    from agents import monitor

    terms = monitor._parse_exclusions(CONFIG_TEXT)
    kept = monitor._filter_news_results([SAFETY, HOAX, RELEASE, TOOLING, APPLE], set(), terms)
    urls = [r["url"] for r in kept]
    _check("the safety story is dropped", SAFETY["url"] not in urls, str(urls))
    _check("the hoax story is dropped", HOAX["url"] not in urls, str(urls))
    _check("the Apple story is dropped", APPLE["url"] not in urls, str(urls))
    _check("the model release is kept", RELEASE["url"] in urls, str(urls))
    _check("the tooling story is kept", TOOLING["url"] in urls, str(urls))

    kept = monitor._filter_news_results([RELEASE, TOOLING], {RELEASE["url"]}, terms)
    _check("a story already sent is not sent again",
           [r["url"] for r in kept] == [TOOLING["url"]], str([r["url"] for r in kept]))

    same_domain = [RELEASE, dict(RELEASE, url="https://groq.com/blog/other", title="Another Groq post")]
    kept = monitor._filter_news_results(same_domain, set(), terms)
    _check("two stories from one domain become one", len(kept) == 1, str(kept))

    _check("with no exclusions configured, nothing is filtered on that basis",
           len(monitor._filter_news_results([SAFETY, HOAX], set(), [])) == 2)


def test_seen_store() -> None:
    from agents import monitor

    config.MEMORY_DIR = Path(tempfile.mkdtemp())
    _check("an absent store reads as empty", monitor._load_seen_news() == {})

    monitor._remember_news([RELEASE["url"], TOOLING["url"]], now=NOW)
    seen = monitor._load_seen_news()
    _check("what went out is remembered", set(seen) == {RELEASE["url"], TOOLING["url"]}, str(seen))

    # Two weeks on, the cycle has moved; an old story is allowed back rather than
    # being blocked forever by a file that only grows.
    old = NOW - timedelta(days=20)
    (config.MEMORY_DIR / "news_seen.json").write_text(
        json.dumps({SAFETY["url"]: old.isoformat(), RELEASE["url"]: NOW.isoformat()}),
        encoding="utf-8")
    monitor._remember_news([TOOLING["url"]], now=NOW)
    seen = monitor._load_seen_news()
    _check("entries older than the window are pruned", SAFETY["url"] not in seen, str(seen))
    _check("recent entries survive the prune", RELEASE["url"] in seen, str(seen))

    (config.MEMORY_DIR / "news_seen.json").write_text("{not json", encoding="utf-8")
    _check("an unreadable store reads as empty rather than raising",
           monitor._load_seen_news() == {})
    monitor._remember_news([RELEASE["url"]], now=NOW)
    _check("and is rewritten cleanly", RELEASE["url"] in monitor._load_seen_news())


if __name__ == "__main__":
    print("exclusions parsing")
    test_parse_exclusions()
    print("\nfiltering")
    test_filter()
    print("\nthe seen store")
    test_seen_store()

    if _failures:
        print(f"\n{len(_failures)} FAILED: {', '.join(_failures)}")
        sys.exit(1)
    print("\nall pass")
