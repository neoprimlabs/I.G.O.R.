"""fetch_url returns what a person would read, and search failing is not search finding nothing.

Run: python tests/test_fetch_url.py   (repo root, stdlib only, no network)

Found 2026-10-30: fetch_url returned a page's raw HTML cut to 4000 characters, which on a modern
page is scripts and styling. Hermes' web_extract returns the page's text. A search that failed
came back as "No results found", which a model reports as nothing existing.
"""

import asyncio
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import config

config.MEMORY_DIR = Path(tempfile.mkdtemp())

_failures: list[str] = []


def _check(name: str, condition: bool, detail: str = "") -> None:
    print(f"  {'pass' if condition else 'FAIL'}  {name}" + ("" if condition else f"  {detail}"))
    if not condition:
        _failures.append(name)


PAGE = ("<html><head><title>Plans</title><style>.a{color:red}</style><script>var tracking = 1;</script></head><body>"
        "<nav>Home</nav><h1>Our plans</h1><p>Plus is $10 a month.</p></body></html>")


def _fetch(url="https://e.example/plans", get=None, page_text=None, answer=None, **kwargs):
    """_fetch_url with the network replaced. get returns (status, content type, body)."""
    from agents import react, research
    import httpx
    used = {"get": 0, "page": 0, "answer": 0}

    class _Client:
        def __init__(self, **k):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def get(self, target, headers=None):
            used["get"] += 1
            status, content_type, body = (get or (lambda: (200, "text/html; charset=utf-8", PAGE)))()
            request = httpx.Request("GET", target)
            return httpx.Response(status, headers={"content-type": content_type}, text=body, request=request)

    async def fake_page(target, max_chars=30000):
        used["page"] += 1
        return page_text or ""

    async def fake_answer(target, question):
        used["answer"] += 1
        return answer or ""

    real = httpx.Client, research._page_text, research._page_answer
    try:
        httpx.Client, research._page_text, research._page_answer = _Client, fake_page, fake_answer
        return asyncio.run(react._fetch_url(url, **kwargs)), used
    finally:
        httpx.Client, research._page_text, research._page_answer = real


def test_a_page_reads_as_text() -> None:
    out, used = _fetch()
    _check("the words are there", "Our plans" in out and "Plus is $10 a month." in out, out)
    _check("the markup and scripts are not", "<" not in out and "tracking" not in out and "color:red" not in out, out)
    timed, _ = _fetch(get=lambda: (200, "text/html", "<p>Opens at 9:00 am. Posted 5 days ago.</p>"))
    _check("times and ages stay: they are facts", "Opens at 9:00 am. Posted 5 days ago." in timed, timed)


def test_a_long_page_can_be_read_on() -> None:
    from agents import react
    long_page = "<p>" + "</p><p>".join(f"Paragraph number {n} of the article." for n in range(600)) + "</p>"

    def get():
        return 200, "text/html", long_page
    first, _ = _fetch(get=get)
    _check("a window at a time, inside the tool cap", len(first) < react._TOOL_RESULT_CAP, str(len(first)))
    _check("it says how to go on", "fetch_url with offset=" in first and "Paragraph number 0 " in first, first[-200:])
    offset = int(first.rsplit("offset=", 1)[1].split()[0])
    second, _ = _fetch(get=get, offset=offset)
    _check("and the next window carries on", "Paragraph number 0 " not in second and "Paragraph number 300" not in first, second[:120])
    past, _ = _fetch(get=get, offset=10_000_000)
    _check("past the end says so", "past the end" in past, past)
    short, _ = _fetch()
    _check("a short page has no such note", "offset=" not in short, short)


def test_a_question_about_a_page() -> None:
    out, used = _fetch(answer="Plus is $10 a month.", question="what does plus cost")
    _check("the page's answer comes back", out == "Plus is $10 a month.", out)
    _check("without fetching it here", used["get"] == 0 and used["answer"] == 1, str(used))
    out, used = _fetch(answer="", question="what does plus cost")
    _check("no answer falls back to the page", "Our plans" in out and used["get"] == 1, out)


def test_a_blocked_page_is_tried_another_way() -> None:
    out, used = _fetch(get=lambda: (403, "text/html", "Forbidden"), page_text="The article, as Exa read it.")
    _check("a 403 is read through Exa", out == "The article, as Exa read it." and used["page"] == 1, out)
    out, used = _fetch(get=lambda: (403, "text/html", "Forbidden"))
    _check("if that fails too it says what happened", "HTTP 403" in out, out)
    out, used = _fetch(get=lambda: (200, "application/pdf", "%PDF"), page_text="The PDF's text.")
    _check("a PDF is read through Exa", out == "The PDF's text.", out)
    out, used = _fetch(get=lambda: (200, "image/png", "x"))
    _check("what cannot be read says so", "non-text" in out, out)


def test_internal_addresses_stay_refused() -> None:
    out, used = _fetch(url="http://169.254.169.254/latest/meta-data/", question="what is here")
    _check("with a question too", out.startswith("[refused") and used["answer"] == 0 and used["get"] == 0, out)
    out, used = _fetch(url="http://127.0.0.1:8080/", get=lambda: (403, "text/html", "x"), page_text="internal")
    _check("and no fallback reaches them", out.startswith("[refused") and used["page"] == 0, out)


def test_execute_passes_it_on() -> None:
    from agents import react
    seen = {}

    async def fetch(url, offset=0, question=None):
        seen.update(url=url, offset=offset, question=question)
        return "text"

    real = react._fetch_url
    try:
        react._fetch_url = fetch
        out = asyncio.run(react._execute_tool("fetch_url", {"url": "https://e.example", "offset": 400, "question": "price?"}))
        asyncio.run(react._execute_tool("fetch_url", {"url": "https://e.example", "offset": "soon"}))
    finally:
        react._fetch_url = real
    _check("a bad offset starts at the top", seen["offset"] == 0, str(seen))
    _check("the result stays marked untrusted", out.startswith(react._UNTRUSTED_OPEN), out[:80])
    schema = next(t for t in react._TOOLS if t["name"] == "fetch_url")["input_schema"]["properties"]
    _check("the tool offers both", "offset" in schema and "question" in schema, str(schema.keys()))


def test_search_failing_is_not_search_finding_nothing() -> None:
    from agents import react, research

    async def broken(query, **kwargs):
        raise RuntimeError("402 credits exhausted")

    real = research._run_search
    try:
        research._run_search = broken
        one = asyncio.run(react._execute_tool("search", {"query": "desks"}))
        many = asyncio.run(react._execute_tool("search", {"queries": ["desks", "chairs"]}))
    finally:
        research._run_search = real
    _check("a failed search says it failed", "search failed" in one and "No results" not in one, one)
    _check("and that this says nothing about what exists", "nothing about what exists" in one, one)
    _check("in a batch, too", many.count("search failed") == 2, many)


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            print(name)
            fn()
    if _failures:
        print(f"\n{len(_failures)} FAILED: {', '.join(_failures)}")
        sys.exit(1)
    print("\nall pass")
