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
NOW = datetime(2026, 10, 16, 15, 0, tzinfo=timezone.utc)

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
    test_never_raises()
    if _failures:
        print(f"\n{len(_failures)} FAILED: {', '.join(_failures)}")
        sys.exit(1)
    print("\nall pass")
