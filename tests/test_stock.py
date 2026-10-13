"""Tests for agents/stock.py - alerting when a part comes back in stock.

Run: python tests/test_stock.py   (repo root, stdlib only, no network)

The price watch reads Slickdeals, which only knows about discounts somebody posted.
It cannot see a vendor putting stock back on the shelf, and for a part that sells out
that is the event worth knowing about. Measured 2026-10-13: the AOOSTAR AG01 was in
stock on the vendor's store at $179, under target, and the price watch had no way to
see it - the only Slickdeals posting was 18 months old and correctly age-filtered.

Fixtures are trimmed from the real /products/<handle>.js response.
"""

import json
import sys
import tempfile
from datetime import datetime, timezone
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


NOW = datetime(2026, 10, 13, 1, 0, tzinfo=timezone.utc)

URL = "https://aoostar.com/products/aoostar-ag01-egpu-dock"

WATCHLIST = """# Stock Watch

## AOOSTAR AG01
url: https://aoostar.com/products/aoostar-ag01-egpu-dock
variant: us
under: 200
"""


def _product(us=True, uk=False, us_price=17900):
    """The shape the real endpoint returns: prices in integer cents."""
    return json.dumps({
        "id": 9697535000874,
        "title": "AOOSTAR AG01 OCuLink eGPU Dock",
        "available": us or uk,
        "variants": [
            {"title": "AG01(OCULINK PORTS) / US", "available": us,
             "price": us_price, "inventory_management": "shopify"},
            {"title": "AG01(OCULINK PORTS) / UK", "available": uk,
             "price": 17900, "inventory_management": "shopify"},
            {"title": "AG01(OCULINK PORTS) / AU", "available": False,
             "price": 17900, "inventory_management": "shopify"},
        ],
    })


def _fresh(watchlist=WATCHLIST):
    config.MEMORY_DIR = Path(tempfile.mkdtemp())
    (config.MEMORY_DIR / "stock_watch.md").write_text(watchlist, encoding="utf-8")
    return config.MEMORY_DIR


def _run(payload, now=NOW):
    from agents import stock
    return stock.check(fetch=lambda url: (payload, "test"), now=now)


def test_the_json_url_is_derived() -> None:
    from agents import stock

    cases = [
        ("https://aoostar.com/products/x", "https://aoostar.com/products/x.js"),
        ("https://aoostar.com/products/x/", "https://aoostar.com/products/x.js"),
        ("https://aoostar.com/products/x?variant=12", "https://aoostar.com/products/x.js"),
        ("https://aoostar.com/products/x#tab", "https://aoostar.com/products/x.js"),
        ("https://aoostar.com/products/x.js", "https://aoostar.com/products/x.js"),
        ("https://aoostar.com/products/x.json", "https://aoostar.com/products/x.json"),
    ]
    for given, want in cases:
        _check(f"{given[-28:]} -> .js", stock._json_url(given) == want,
               stock._json_url(given))


def test_a_restock_is_reported_once() -> None:
    _fresh()

    # First check: nothing known, it is available, so say so.
    hits = _run(_product(us=True))
    _check("the first check reports what is in stock", len(hits) == 1, str(hits))
    _check("naming the variant", hits and "US" in hits[0]["variant"], str(hits))
    _check("with the price in dollars, not cents",
           hits and hits[0]["price"] == 179.0, str(hits))
    _check("and flagged as a first sighting rather than a change",
           hits and hits[0]["first_seen"], str(hits))

    # Still available on the next check: an hourly job must not repeat itself.
    _check("an unchanged in-stock variant says nothing",
           _run(_product(us=True)) == [], "repeated an alert")


def test_the_transition_is_what_matters() -> None:
    _fresh()
    _run(_product(us=True))
    _check("it goes out of stock quietly", _run(_product(us=False)) == [])

    hits = _run(_product(us=True))
    _check("and coming back is an alert", len(hits) == 1, str(hits))
    _check("no longer a first sighting",
           hits and not hits[0]["first_seen"], str(hits))


def test_only_the_wanted_variant_counts() -> None:
    _fresh()
    _run(_product(us=False, uk=False))
    _check("a different region coming back is ignored",
           _run(_product(us=False, uk=True)) == [],
           "alerted on a variant that was not asked for")

    hits = _run(_product(us=True, uk=True))
    _check("the asked-for variant still alerts", len(hits) == 1, str(hits))
    _check("and only that one", hits and "US" in hits[0]["variant"], str(hits))


def test_the_price_ceiling_is_respected() -> None:
    _fresh()
    _check("in stock but over target is not an alert",
           _run(_product(us=True, us_price=24900)) == [], "alerted over the ceiling")

    # And it must still alert later if the price comes down while in stock.
    hits = _run(_product(us=True, us_price=17900))
    _check("the same variant alerts once it is under target",
           len(hits) == 1, str(hits))


def test_a_failed_fetch_does_not_fake_a_restock() -> None:
    """The subtle one. If a failed check recorded "unavailable", the next successful
    check would look like a restock and send a message about nothing."""
    from agents import stock

    _fresh()
    _run(_product(us=True))

    def _broken(url):
        raise RuntimeError("relay down")

    _check("a fetch failure reports nothing",
           stock.check(fetch=_broken, now=NOW) == [])
    _check("and does not clear what was known",
           stock._load_state()["alertable"].get("AOOSTAR AG01|AG01(OCULINK PORTS) / US"),
           str(stock._load_state()))
    _check("so the next good check is still not a restock",
           _run(_product(us=True)) == [], "faked a restock after a failure")


def test_unparseable_payloads() -> None:
    from agents import stock

    _fresh()
    _check("html instead of json is not a crash", _run("<html>nope</html>") == [])
    _check("empty is not a crash", _run("") == [])
    _check("json without variants is rejected", _run('{"title": "x"}') == [])

    # The relay returns the JSON wrapped in markdown preamble.
    wrapped = "Title: AOOSTAR\nURL Source: x\n\nMarkdown Content:\n" + _product(us=True)
    hits = _run(wrapped)
    _check("json wrapped by the relay is still found", len(hits) == 1, str(hits))


def test_the_watchlist() -> None:
    from agents import stock

    _fresh()
    items = stock._watchlist()
    _check("the item is read", len(items) == 1, str(items))
    _check("with its url", items and items[0]["url"] == URL, str(items))
    _check("variant lowercased for matching", items and items[0]["variant"] == "us", str(items))
    _check("and the ceiling", items and items[0]["under"] == 200.0, str(items))

    config.MEMORY_DIR = Path(tempfile.mkdtemp())
    _check("no file means nothing watched", stock._watchlist() == [])

    _fresh("## Broken\nvariant: us\n")
    _check("an entry with no url is skipped", stock._watchlist() == [])


def test_the_message() -> None:
    from agents import stock

    _fresh()
    text = stock.format_for_discord(_run(_product(us=True)))
    _check("it says back in stock", "Back in stock" in text, text)
    _check("names the product", "AOOSTAR AG01 OCuLink eGPU Dock" in text, text)
    _check("gives the price", "179.00" in text, text)
    _check("links the page", URL in text, text)
    _check("and says to confirm shipping before buying",
           "checkout" in text.lower(), text)


def test_status_for_monitor() -> None:
    from agents import stock

    _fresh()
    _check("before any check it says so", "not yet checked" in stock.status(), stock.status())
    _run(_product(us=True))
    s = stock.status()
    _check("after a check it names the item", "AOOSTAR AG01" in s, s)
    # Only watched variants are tracked, so with "variant: us" this is 1 of 1.
    _check("counts what is in stock among watched variants", "1 of 1" in s, s)
    _check("and says when it last ran", "last run" in s, s)

    config.MEMORY_DIR = Path(tempfile.mkdtemp())
    _check("nothing configured is not a crash",
           "nothing configured" in stock.status(), stock.status())


if __name__ == "__main__":
    print("deriving the json url")
    test_the_json_url_is_derived()
    print("\na restock reported once")
    test_a_restock_is_reported_once()
    print("\nthe transition")
    test_the_transition_is_what_matters()
    print("\nvariant matching")
    test_only_the_wanted_variant_counts()
    print("\nthe price ceiling")
    test_the_price_ceiling_is_respected()
    print("\na failed fetch")
    test_a_failed_fetch_does_not_fake_a_restock()
    print("\nbad payloads")
    test_unparseable_payloads()
    print("\nthe watchlist")
    test_the_watchlist()
    print("\nthe message")
    test_the_message()
    print("\nstatus")
    test_status_for_monitor()

    if _failures:
        print(f"\n{len(_failures)} FAILED: {', '.join(_failures)}")
        sys.exit(1)
    print("\nall pass")
