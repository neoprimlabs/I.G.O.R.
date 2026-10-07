"""Tests for agents/prices.py - watching for a part to drop below a target price.

Run: python tests/test_prices.py   (repo root, stdlib only, no network)

Direct retail scraping is not available and not attempted. Measured from the server
on 2026-10-06: Best Buy refused the connection outright, Micro Center, camelcamelcamel
and PCPartPicker returned 403, Newegg redirected to a bot check. Best Buy's official
API no longer issues keys to free email addresses, and Keepa is about $50 a month,
which collides with the standing no-paid-services decision.

Slickdeals publishes an RSS search that answers from the server, free and without a
key, with the price in the title. It is deal-driven rather than a continuous price
feed - it catches drops when they are posted, which is the shape of "tell me when it
dips below X" anyway.

Fixtures are trimmed from a real response.
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


NOW = datetime(2026, 10, 6, 12, 0, tzinfo=timezone.utc)

WATCHLIST = """# Price Watch

## RTX 5070 Ti
query: rtx 5070 ti
under: 650

## Mac mini M6
query: mac mini m6
under: 700
"""

FEED = """<?xml version="1.0"?><rss version="2.0"><channel>
<item><title>ASUS RTX 5070 Ti 16GB Graphics Card $599.99 + Free Shipping</title>
<link>https://slickdeals.net/f/1</link></item>
<item><title>GIGABYTE RTX 5070 Ti OC Edition $719.00 at Newegg</title>
<link>https://slickdeals.net/f/2</link></item>
<item><title>$769* | Apple 2026 Mac mini Desktop Computer M6 chip (16GB / 256GB) at Amazon</title>
<link>https://slickdeals.net/f/3</link></item>
<item><title>RTX 5070 Ti Waterblock, no card included, $89.99</title>
<link>https://slickdeals.net/f/4</link></item>
<item><title>Some unrelated laptop deal, great value</title>
<link>https://slickdeals.net/f/5</link></item>
</channel></rss>"""


def _fresh():
    config.MEMORY_DIR = Path(tempfile.mkdtemp())
    (config.MEMORY_DIR / "price_watch.md").write_text(WATCHLIST, encoding="utf-8")
    return config.MEMORY_DIR


def test_watchlist() -> None:
    from agents import prices

    _fresh()
    items = prices._watchlist()
    _check("both items are read", len(items) == 2, str(items))
    _check("with name, query and target",
           items[0]["name"] == "RTX 5070 Ti" and items[0]["query"] == "rtx 5070 ti"
           and items[0]["under"] == 650.0, str(items[0]))

    config.MEMORY_DIR = Path(tempfile.mkdtemp())
    _check("no file means nothing watched, not a crash", prices._watchlist() == [])

    (config.MEMORY_DIR / "price_watch.md").write_text(
        "## Broken\nquery: thing\nunder: cheap\n", encoding="utf-8")
    _check("an unreadable target is skipped rather than guessed",
           prices._watchlist() == [], str(prices._watchlist()))


def test_price_extraction() -> None:
    from agents import prices

    cases = [
        ("ASUS RTX 5070 Ti 16GB $599.99 + Free Shipping", 599.99),
        ("$769* | Apple Mac mini M6 at Amazon", 769.0),
        ("$59.99: Cooler Master QUBE 540 Case", 59.99),
        ("Intel Core Ultra 5 250KF Processor $169.99", 169.99),
        ("Was $1,299.00 now $999.00 at Newegg", 999.0),
        ("No price in this title at all", None),
    ]
    for title, want in cases:
        got = prices._lowest_price(title)
        _check(f"{str(want):>8} from: {title[:46]}", got == want, f"got {got}")


def test_matching() -> None:
    from agents import prices

    item = {"name": "RTX 5070 Ti", "query": "rtx 5070 ti", "under": 650.0}
    deals = prices._from_feed(FEED)
    hits = prices._hits(item, deals, set())

    titles = [h["title"] for h in hits]
    _check("the under-target card is found",
           any("599.99" in t for t in titles), str(titles))
    _check("the over-target card is not", not any("719.00" in t for t in titles), str(titles))
    _check("an unrelated product is not", not any("Mac mini" in t for t in titles), str(titles))
    _check("a title with no price is not", not any("unrelated laptop" in t for t in titles), str(titles))

    # A waterblock is not a graphics card, but it carries every query word and a low
    # price. Nothing here can tell them apart, so the message has to show the title
    # and let a human decide rather than announcing the card dropped to $89.
    _check("an accessory that matches the words is still reported",
           any("Waterblock" in t for t in titles), str(titles))

    _check("the price comes back with the hit",
           all(isinstance(h["price"], float) for h in hits), str(hits))

    seen = {deals[0]["url"]}
    again = prices._hits(item, deals, seen)
    _check("something already reported is not reported twice",
           not any("599.99" in h["title"] for h in again), str(again))


def test_stale_deals_are_not_news() -> None:
    """The first live run found an AOOSTAR AG01 at $179 posted 11 April 2025 and
    would have sent it as a price drop. Slickdeals searches by relevance, not
    recency, so an eighteen-month-old post ranks alongside yesterday's."""
    from agents import prices

    fresh = (NOW - timedelta(days=3)).strftime("%a, %d %b %Y %H:%M:%S +0000")
    stale = (NOW - timedelta(days=550)).strftime("%a, %d %b %Y %H:%M:%S +0000")
    feed = f"""<?xml version="1.0"?><rss version="2.0"><channel>
<item><title>AOOSTAR AG01 OCulink eGPU Dock 800W PSU $179</title>
<link>https://slickdeals.net/f/old</link><pubDate>{stale}</pubDate></item>
<item><title>Minisforum DEG1 eGPU Oculink Dock Refurbished $44</title>
<link>https://slickdeals.net/f/new</link><pubDate>{fresh}</pubDate></item>
<item><title>Mystery eGPU Dock Oculink $50</title>
<link>https://slickdeals.net/f/undated</link></item>
</channel></rss>"""

    deals = prices._from_feed(feed)
    _check("the post date is read", deals[0]["posted"] is not None, str(deals[0]))

    item = {"name": "eGPU dock", "query": "oculink dock", "under": 200.0}
    urls = [h["url"] for h in prices._hits(item, deals, set(), now=NOW)]
    _check("an eighteen-month-old deal is not reported",
           "https://slickdeals.net/f/old" not in urls, str(urls))
    _check("a recent one is", "https://slickdeals.net/f/new" in urls, str(urls))
    _check("one with no date is kept rather than guessed away",
           "https://slickdeals.net/f/undated" in urls, str(urls))


def test_seen_store() -> None:
    from agents import prices

    path = _fresh()
    _check("an absent store reads empty", prices._load_seen() == {})
    prices.remember([{"url": "https://slickdeals.net/f/1"}], now=NOW)
    _check("what was reported is remembered",
           "https://slickdeals.net/f/1" in prices._load_seen())

    old = (NOW - timedelta(days=40)).isoformat()
    (path / "prices_seen.json").write_text(
        json.dumps({"https://old": old}), encoding="utf-8")
    prices.remember([], now=NOW)
    _check("old entries are pruned", "https://old" not in prices._load_seen())

    (path / "prices_seen.json").write_text("{not json", encoding="utf-8")
    _check("an unreadable store reads empty", prices._load_seen() == {})


def test_message() -> None:
    from agents import prices

    hits = [{"name": "RTX 5070 Ti", "under": 650.0, "price": 599.99,
             "title": "ASUS RTX 5070 Ti 16GB $599.99 + Free Shipping",
             "url": "https://slickdeals.net/f/1"}]
    text = prices.format_for_discord(hits)
    _check("the message names the part", "RTX 5070 Ti" in text, text)
    _check("and the price", "599.99" in text, text)
    _check("and what the target was", "650" in text, text)
    _check("and links the deal", "slickdeals.net/f/1" in text, text)


def test_the_weekly_heartbeat() -> None:
    """A watcher that only speaks on success cannot be trusted to be watching. The
    job watch sent nothing for five days and neither of us noticed until a question
    came in that it could not answer."""
    from agents import prices

    _fresh()
    prices.check(fetch=lambda q: prices._from_feed(FEED))

    state = prices._load_state()
    _check("the closest price is recorded even with no hit",
           state["closest"]["Mac mini M6"]["price"] == 769.0, str(state.get("closest")))
    _check("along with the target it is being judged against",
           state["closest"]["Mac mini M6"]["under"] == 700.0, str(state.get("closest")))

    text = prices.heartbeat(now=NOW)
    _check("the first heartbeat goes out", "Still watching" in text, text)
    _check("naming the item and how close it got",
           "Mac mini M6" in text and "769" in text, text)
    _check("and the target", "700" in text, text)

    _check("a second one the same day does not",
           prices.heartbeat(now=NOW + timedelta(days=2)) == "",
           prices.heartbeat(now=NOW + timedelta(days=2)))
    _check("but one a week later does",
           "Still watching" in prices.heartbeat(now=NOW + timedelta(days=8)))

    _fresh()
    _check("nothing watched means no heartbeat at all", prices.heartbeat(now=NOW) == "")


def test_a_broken_feed_is_not_a_crash() -> None:
    from agents import prices

    _check("malformed xml yields no deals", prices._from_feed("<not xml") == [])
    _check("an empty feed yields no deals", prices._from_feed("") == [])


if __name__ == "__main__":
    print("watchlist")
    test_watchlist()
    print("\nprice extraction")
    test_price_extraction()
    print("\nmatching")
    test_matching()
    print("\nstale deals")
    test_stale_deals_are_not_news()
    print("\nthe seen store")
    test_seen_store()
    print("\nthe message")
    test_message()
    print("\nthe weekly heartbeat")
    test_the_weekly_heartbeat()
    print("\nbroken input")
    test_a_broken_feed_is_not_a_crash()

    if _failures:
        print(f"\n{len(_failures)} FAILED: {', '.join(_failures)}")
        sys.exit(1)
    print("\nall pass")
