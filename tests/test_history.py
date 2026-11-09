"""Watch history (history.py), fed by the real watcher code paths.

Run: python tests/test_history.py   (repo root, no API calls)

The watchers used to keep only their latest state, so nothing could say when the
AG01 was last in stock. The rule carried over from the stock watch: a failed fetch
records nothing, because no data is not "unavailable".
"""

import json
import sys
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import config

_failures: list[str] = []
# Yesterday, not a fixed date: describe() reads the last 30 days of the real clock, so a fixed
# 2026-10-16 would have failed every run from 11-15 on and blocked the deploy gate.
NOW = (datetime.now(timezone.utc) - timedelta(days=1)).replace(hour=15, minute=0, second=0, microsecond=0)

WATCHLIST = """# Stock Watch

## AOOSTAR AG01
url: https://aoostar.com/products/aoostar-ag01-egpu-dock
variant: us
under: 200
"""


def _check(name: str, condition: bool, detail: str = "") -> None:
    print(f"  {'pass' if condition else 'FAIL'}  {name}" + ("" if condition else f"  {detail}"))
    if not condition:
        _failures.append(name)


def _product(us: bool, price: int = 17900) -> str:
    return json.dumps({"title": "AOOSTAR AG01 OCuLink eGPU Dock", "available": us, "variants": [
        {"title": "AG01(OCULINK PORTS) / US", "available": us, "price": price},
        {"title": "AG01(OCULINK PORTS) / UK", "available": False, "price": 17900},
    ]})


def test_stock_history() -> None:
    import history
    from agents import stock
    config.MEMORY_DIR = Path(tempfile.mkdtemp())
    (config.MEMORY_DIR / "stock_watch.md").write_text(WATCHLIST, encoding="utf-8")

    hour = timedelta(hours=1)
    for i, (available, price) in enumerate([(False, 17900), (False, 17900), (True, 17900),
                                            (True, 17900), (True, 16900), (False, 16900)]):
        stock.check(fetch=lambda url, a=available, p=price: (_product(a, p), "test"), now=NOW + i * hour)

    def broken(url):
        raise OSError("429 from the vendor")
    stock.check(fetch=broken, now=NOW + 6 * hour)

    rows = history.changes("ag01 us", now=NOW + 7 * hour)
    avail = [r[4] for r in reversed(rows) if r[3] == "available"]
    prices = [r[4] for r in reversed(rows) if r[3] == "price"]
    _check("six hourly checks of availability collapse to its changes",
           avail == ["0", "1", "0"], str(avail))
    _check("and price to its changes", prices == ["179.00", "169.00"], str(prices))
    _check("a failed fetch records nothing, so it cannot fake a stock-out",
           all(r[0] < int((NOW + 6 * hour).timestamp()) for r in rows), str(rows[:2]))
    _check("only the watched variant is matched by its words",
           all("/ US" in r[2] for r in rows), str({r[2] for r in rows}))

    text = history.describe("ag01")
    _check("the tool reads yes/no for availability", "available=yes" in text and "available=no" in text, text)
    _check("and says when the watch messages, so a quiet one can be explained",
           "only when an item becomes buyable" in text, text)
    _check("and says plainly when nothing matches",
           history.describe("zebra").startswith("No watch history"))


def test_price_and_jobs_history() -> None:
    import history
    from agents import jobs, prices
    config.MEMORY_DIR = Path(tempfile.mkdtemp())
    (config.MEMORY_DIR / "price_watch.md").write_text(
        "# Price Watch\n\n## AG01 dock\nquery: ag01\nunder: 150\n", encoding="utf-8")
    prices.check(fetch=lambda q: [{"title": "AOOSTAR AG01 eGPU dock $179", "url": "https://d/1"}])
    rows = history.changes("ag01 dock")
    _check("the price watch records its cheapest match",
           any(r[3] == "cheapest_match" and r[4] == "179.00" for r in rows), str(rows))
    _check("labelled as unverified when shown",
           "not checked as the right product" in history.describe("ag01 dock"), history.describe("ag01 dock"))

    (config.MEMORY_DIR / "job_search.md").write_text(
        "# Job Search\n\nwant: engineer\n", encoding="utf-8")
    found = [{"title": "AI Support Engineer", "company": "Acme", "url": "https://j/1", "source": "remoteok"}]
    jobs.collect(now=NOW, fetchers=[("test", lambda: found)])
    rows = history.changes("engineer", now=NOW + timedelta(minutes=1))
    _check("each new matching job posting is recorded with its source",
           any(r[2] == "AI Support Engineer - Acme" and r[4] == "remoteok" for r in rows), str(rows))
    _check("job rows alone carry no note about the stock and price watches",
           "becomes buyable" not in history.describe("engineer"), history.describe("engineer"))


def test_watcher_names() -> None:
    """2026-11-09: asked how the watches were doing, React asked this for "price watch" and "stock
    watch", which are in no item's name, and told the user neither had found anything."""
    import history
    config.MEMORY_DIR = Path(tempfile.mkdtemp())
    history.record("stock", [("Kettle|Steel / US", "available", 1, "https://k/1")], now=NOW)
    history.record("price", [("Kettle deal", "cheapest_match", "39.00", "https://k/2")], now=NOW)
    history.record("jobs", [("Kettle Tester - Acme", "posted", "remoteok", "https://j/1")], now=NOW)

    sources = lambda q: {r[1] for r in history.changes(q, now=NOW + timedelta(hours=1))}
    _check("a watcher's name finds its rows: stock watch", sources("stock watch") == {"stock"}, str(sources("stock watch")))
    _check("price watch", sources("price watch") == {"price"}, str(sources("price watch")))
    _check("job watch", sources("job watch") == {"jobs"}, str(sources("job watch")))
    _check("the watches, alone, mean price and stock", sources("the watches") == {"stock", "price"}
           and sources("watches") == {"stock", "price"}, str(sources("the watches")))
    _check("item words still narrow it", sources("steel") == {"stock"}, str(sources("steel")))

    history.record("stock", [("Kettle|Steel / US", "available", 1, "https://k/1")], now=NOW + timedelta(hours=5))
    shown = history.describe("stock")
    import clock
    later = (NOW + timedelta(hours=5)).astimezone(clock.USER_TZ)
    _check("an unchanged watch still shows when it last checked",
           f"Latest check recorded, unchanged since the rows above: stock {later:%a %Y-%m-%d %H:%M}" in shown, shown)
    _check("but not for jobs, whose rows are new postings, not checks",
           "Latest check recorded" not in history.describe("job"), history.describe("job"))

    missed = history.describe("zebra")
    _check("a miss still starts as it did", missed.startswith("No watch history"), missed)
    _check("but says it is not the same as finding nothing", "not the same as the watches finding nothing" in missed, missed)
    _check("and names what is on record", "Kettle|Steel / US" in missed and "Kettle deal" in missed
           and "jobs: 1 posting" in missed, missed)
    config.MEMORY_DIR = Path(tempfile.mkdtemp())
    _check("an empty store says nothing was recorded",
           "recorded nothing in that time" in history.describe("zebra"), history.describe("zebra"))


def test_never_raises() -> None:
    import history
    config.MEMORY_DIR = Path(tempfile.mkdtemp()) / "missing"
    raised = False
    try:
        history.record("stock", [("x", "available", 1, "")])
        history.changes("x")
        history.describe("x")
    except Exception as e:
        raised = True
        print("   raised:", type(e).__name__, e)
    _check("an unwritable or missing store never raises into a watcher", not raised)


if __name__ == "__main__":
    test_stock_history()
    test_price_and_jobs_history()
    test_watcher_names()
    test_never_raises()
    if _failures:
        print(f"\n{len(_failures)} FAILED: {', '.join(_failures)}")
        sys.exit(1)
    print("\nall pass")
