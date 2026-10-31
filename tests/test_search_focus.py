"""React's search asks Exa what each page says on the query, not for the page's opening.

Run: python tests/test_search_focus.py   (repo root, stdlib only, no network)

Live 2026-10-30: asked to price eight products, React spent 45000 tokens on one, searching
Notion three times, because a page's first 500 characters seldom hold the answer and old
results are trimmed for budget. A focused result is shorter and holds the fact.
"""

import asyncio
import sys
import tempfile
import types
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import config

config.MEMORY_DIR = Path(tempfile.mkdtemp())

_failures: list[str] = []


def _check(name: str, condition: bool, detail: str = "") -> None:
    print(f"  {'pass' if condition else 'FAIL'}  {name}" + ("" if condition else f"  {detail}"))
    if not condition:
        _failures.append(name)


class _Exa:
    """Stands in for exa_py.Exa and records what it was asked."""
    calls: list = []
    fail_focus = False

    def __init__(self, api_key=None):
        pass

    def search_and_contents(self, query, **kwargs):
        _Exa.calls.append((query, kwargs))
        if _Exa.fail_focus and isinstance(kwargs.get("summary"), dict):
            raise RuntimeError("summary is unavailable")
        return SimpleNamespace(results=[SimpleNamespace(
            title="Pricing", url="https://e.example/pricing", published_date="2026-10-01T00:00:00Z",
            summary="Plus is $10 per member per month." if "summary" in kwargs else None,
            text="Skip to content. Toggle navigation." if "text" in kwargs else None)])


def _search(**kwargs):
    from agents import research
    _Exa.calls = []
    real = sys.modules.get("exa_py")
    sys.modules["exa_py"] = types.SimpleNamespace(Exa=_Exa)
    try:
        return asyncio.run(research._run_search("notion price", **kwargs))
    finally:
        if real is None:
            sys.modules.pop("exa_py", None)
        else:
            sys.modules["exa_py"] = real


def test_focus() -> None:
    results = _search(focus="notion price")
    asked = _Exa.calls[0][1]
    _check("the page's answer is what comes back", results[0]["body"] == "Plus is $10 per member per month.", str(results))
    _check("Exa is asked about the query", "notion price" in asked["summary"]["query"], str(asked))
    _check("and told to stay on the page", "Only what the page states" in asked["summary"]["query"])
    _check("numbers are kept as written", "numbers exactly as written" in asked["summary"]["query"])
    _check("raw text is not paid for too", "text" not in asked, str(asked))
    _check("the date still comes through", results[0]["published"] == "2026-10-01", str(results))

    long_query = "x" * 2000
    _search(focus=long_query)
    _check("a long query is held to size", len(_Exa.calls[0][1]["summary"]["query"]) < 600)

    _Exa.fail_focus = True
    try:
        results = _search(focus="notion price")
    finally:
        _Exa.fail_focus = False
    _check("if focus fails it falls back to raw text", len(_Exa.calls) == 2 and "text" in _Exa.calls[1][1]
           and results[0]["body"].startswith("Skip to content"), str(_Exa.calls))


def test_a_second_backend_when_exa_fails() -> None:
    """Exa is the only backend, and digest, overnight research and React all stop without it."""
    from agents import research

    class _Broken:
        def __init__(self, api_key=None):
            pass

        def search_and_contents(self, query, **kwargs):
            raise RuntimeError("402 credits exhausted")

    class _DDGS:
        def __init__(self, timeout=None):
            pass

        def text(self, query, max_results=5):
            return [{"title": "Pricing", "href": "https://e.example/p", "body": "Plus is $10."}][:max_results]

    def run(ddgs, **kwargs):
        real = {name: sys.modules.get(name) for name in ("exa_py", "ddgs")}
        sys.modules["exa_py"] = types.SimpleNamespace(Exa=_Broken)
        sys.modules["ddgs"] = ddgs
        try:
            return asyncio.run(research._run_search("notion price", **kwargs))
        finally:
            for name, module in real.items():
                if module is None:
                    sys.modules.pop(name, None)
                else:
                    sys.modules[name] = module

    results = run(types.SimpleNamespace(DDGS=_DDGS))
    _check("DDGS answers when Exa cannot", results == [{"title": "Pricing", "url": "https://e.example/p",
                                                       "body": "Plus is $10.", "published": ""}], str(results))
    _check("with no package it is as before", run(None) == [], str(run(None)))
    try:
        run(None, raise_errors=True)
        raised = False
    except RuntimeError:
        raised = True
    _check("and a caller that wants to know still hears of the failure", raised)
    results = run(types.SimpleNamespace(DDGS=_DDGS), raise_errors=True, focus="notion price")
    _check("a fallback that works is not a failure", results and results[0]["body"] == "Plus is $10.", str(results))

    class _AlsoBroken:
        def __init__(self, timeout=None):
            pass

        def text(self, query, max_results=5):
            raise RuntimeError("ratelimited")
    _check("if it fails too, nothing breaks", run(types.SimpleNamespace(DDGS=_AlsoBroken)) == [])


def test_other_callers_are_unchanged() -> None:
    _search()
    _check("a plain search still gets 500 characters", _Exa.calls[0][1]["text"] == {"max_characters": 500}, str(_Exa.calls))
    results = _search(use_summary=True)
    _check("the digest's summary is untouched", _Exa.calls[0][1]["summary"] is True and results[0]["body"].startswith("Plus"))


def test_react_asks_for_focus() -> None:
    from agents import react, research
    seen = {}

    async def run_search(query, **kwargs):
        seen.update(query=query, **kwargs)
        return [{"title": "T", "url": "https://e.example/a", "body": "Plus is $10.", "published": "2026-10-01"}]

    real = research._run_search
    try:
        research._run_search = run_search
        out = asyncio.run(react._execute_tool("search", {"query": "notion price"}))
    finally:
        research._run_search = real
    _check("React's search is focused on its query", seen.get("focus") == "notion price", str(seen))
    _check("the result is still marked untrusted", out.startswith(react._UNTRUSTED_OPEN) and "Plus is $10." in out, out[:120])


def test_several_lookups_in_one_call() -> None:
    """These models make one tool call a step, so eight lookups were eight whole prompts."""
    from agents import react, research
    asked = []
    pages = {
        "notion price": [("https://notion.com/pricing", "Plus is $10 per member per month."),
                         ("https://notion.com/blog", "Nothing on this."),
                         ("https://reviews.example/notion", "Reviewers report paying $12.")],
        "figma price": [("https://figma.com/pricing", "Professional is $16 a seat.")],
        "empty": [],
    }

    async def run_search(query, **kwargs):
        asked.append((query, kwargs))
        return [{"url": u, "body": b, "published": "2026-10-01"} for u, b in pages.get(query, [])]

    real = research._run_search
    try:
        research._run_search = run_search
        out = asyncio.run(react._execute_tool("search", {"queries": ["notion price", "figma price", "empty"], "recency_days": 30}))
        many = asyncio.run(react._execute_tool("search", {"queries": [f"q{n}" for n in range(12)]}))
        long_pages = {f"q{n}": [(f"https://example{n}.example/a/long/path/to/a/page", "A fact. " * 80)] * 3 for n in range(8)}
        pages.update(long_pages)
        full = asyncio.run(react._execute_tool("search", {"queries": list(long_pages)}))
    finally:
        research._run_search = real
    _check("every query is searched, focused on itself",
           all(k["focus"] == q for q, k in asked) and {"notion price", "figma price", "empty"} <= {q for q, _ in asked}, str(asked[:3]))
    _check("each answer sits under its query with its source",
           "notion price\n- https://notion.com/pricing (2026-10-01): Plus is $10" in out, out)
    _check("a page with nothing on it is left out", "Nothing on this" not in out and "Reviewers report" in out, out)
    _check("a query with no answer says so", "empty\n- Nothing found." in out, out)
    _check("the time window applies to all", all(k.get("start_published_date") for q, k in asked[:3]), str(asked[0]))
    _check("it is marked untrusted", out.startswith(react._UNTRUSTED_OPEN) and out.endswith(react._UNTRUSTED_CLOSE), out[-60:])
    _check("eight queries at most, and it says so", many.count("- Nothing found.") == 8 and "Only the first 8" in many, many[-120:])
    _check("a full batch fits inside the tool result cap", len(full) <= react._TOOL_RESULT_CAP, str(len(full)))
    schema = next(t for t in react._TOOLS if t["name"] == "search")["input_schema"]
    _check("the tool offers it", "queries" in schema["properties"] and "required" not in schema, str(schema.keys()))
    _check("and the prompt says to use it", "go in one search call, with queries" in react._DEFAULT_SYSTEM_PROMPT)


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            print(name)
            fn()
    if _failures:
        print(f"\n{len(_failures)} FAILED: {', '.join(_failures)}")
        sys.exit(1)
    print("\nall pass")
