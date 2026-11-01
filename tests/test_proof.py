"""Tests for testing the paper's ideas: proof.py and overnight's proof sessions.

Run: python tests/test_proof.py   (repo root, stdlib only, no API calls)

Invented fixtures throughout. The real failure: by 2026-10-31 the paper had 15 versions, seven ideas
and no independent proof of income for any of them, and nothing was looking.
"""

import asyncio
import json
import sys
import tempfile
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import httpx
import openai

import clock
import config
import proof

_failures: list[str] = []
NIGHT_TIME = datetime(2026, 10, 17, 2, 0, tzinfo=clock.USER_TZ)
GOAL = "Ways an agent could earn money, ranked shortlist"
PAPER = """# Paper

**Summary**
Some ideas.

## Kettle alerts
They charge $29 <https://kettlealerts.example/pricing> <https://e.example/1/0> <https://e.example/1/1>.

## Ranked shortlist

1. **Kettle price alerts** - priced at $29 (evidence: independent)
2. Birdhouse plan e-books: sold online (evidence: no income shown)

## What I am unsure about

Much.
"""


def _check(name: str, condition: bool, detail: str = "") -> None:
    print(f"  {'pass' if condition else 'FAIL'}  {name}" + ("" if condition else f"  {detail}"))
    if not condition:
        _failures.append(name)


def _page(url="https://news.example/a", body="Shops reported revenue of $40,000 last year. Most closed within two years.",
          title="A report"):
    return {"title": title, "url": url, "body": body, "published": ""}


def _row(n=1, what="earned", source="third party", quote="revenue of $40,000 last year"):
    return json.dumps({"n": n, "what": what, "from": source, "quote": quote})


class _Stub:
    def __init__(self, replies=None, results=None):
        self.replies = list(replies or [])
        self.results = results if results is not None else [_page()]
        self.searches, self.judged, self.writes, self.plans = [], [], [], 0
        self.search_fails = False
        self.raises = None

    async def search(self, query, max_results=5, **kwargs):
        self.searches.append((query, kwargs))
        if self.search_fails:
            raise RuntimeError("Exa is down")
        return self.results

    async def complete(self, system, user, max_tokens, cap=None):
        if self.raises:
            raise self.raises
        if system == proof._JUDGE_SYSTEM:
            self.judged.append((user, max_tokens, cap))
            return self.replies.pop(0) if self.replies else "NONE"
        if system.startswith("You plan one step"):
            self.plans += 1
            return f"query {self.plans}"
        if system.startswith("You check the evidence"):
            return "{}"
        if system.startswith("You turn raw search results"):
            return "\n".join(f"- fact {self.plans}-{i} (https://e.example/{self.plans}/{i})" for i in range(5))
        self.writes.append(user)
        return ("# Paper\n\n## Findings\nClaim <https://e.example/1/0> <https://e.example/1/1> <https://e.example/1/2>.\n"
                "\n## Ranked shortlist\n1. **Kettle alerts** - priced\n2. **Rain gauges** - new\n"
                "\n## What I am unsure about\nMuch.\n")


def _setup(paper=PAPER, statement=GOAL):
    import goals
    import work
    from agents import evaluator, overnight
    tmp = tempfile.TemporaryDirectory()
    config.MEMORY_DIR = Path(tmp.name)

    async def _pass(task, response, label="Evaluator", model=None):
        return True, ""

    evaluator.evaluate = _pass
    goals.add(statement, "research")
    folder = work.folder(goals.load()[0]["id"])
    folder.mkdir(parents=True, exist_ok=True)
    if paper:
        (folder / "paper.md").write_text(paper, encoding="utf-8")
    return overnight, folder, tmp


def _full_entry(name="Kettle price alerts", evidence=None, read=3):
    return {"name": name, "searches": 3, "read": read, "evidence": evidence or []}


def _ev(what="earned", source="third party", url="https://news.example/a", quote="revenue of $40,000"):
    return {"url": url, "quote": quote, "what": what, "from": source, "at": "2026-10-17"}


def test_labels() -> None:
    label = proof.label
    _check("someone with nothing to sell reporting money is independent",
           label(_full_entry(evidence=[_ev()])) == "independent")
    _check("the earner's word alone is self-reported", label(_full_entry(evidence=[_ev(source="seller")])) == "self-reported")
    _check("a third party reporting failure is against", label(_full_entry(evidence=[_ev("failed")])) == "against")
    _check("both are mixed", label(_full_entry(evidence=[_ev(), _ev("failed")])) == "mixed")
    _check("one founder's failure story does not sink an idea",
           label(_full_entry(evidence=[_ev("failed", "seller")])) == "no income shown")
    _check("searched and found nothing", label(_full_entry()) == "no income shown")
    _check("never read is not the same as nothing found", label(_full_entry(read=0)) == "unchecked")
    _check("against ranks below not knowing", proof.KINDS.index("against") > proof.KINDS.index("unchecked"))


def test_only_a_real_quote_counts() -> None:
    def judged(row, page=None, sellers=frozenset()):
        rows = proof.parse_judgement(row)
        return proof._kept(rows[0], [page or _page()], sellers, "2026-10-17")

    _check("a quote on the page is kept", judged(_row())["from"] == "third party")
    _check("spacing and case are forgiven", judged(_row(quote="REVENUE of   $40,000 last year")) is not None)
    _check("a quote the model made up is dropped", judged(_row(quote="revenue of $90,000 last year")) is None)
    _check("past the excerpt it read does not count",
           judged(_row(quote="revenue of $40,000 last year"),
                  _page(body="x" * proof._BODY_CAP + " revenue of $40,000 last year")) is None)
    _check("an excerpt that does not exist is dropped", judged(_row(n=2)) is None)
    _check("nor is a nonsense number kept", judged(_row(n="two")) is None)

    def money(quote):
        return judged(_row(quote=quote), _page(body=quote))

    _check("a price is not income", money("The service charges $1,499 per seat") is None)
    _check("hoping is not earning", money("founders could earn revenue of $50,000") is None)
    _check("nor a target", money("targets revenue of $20,000 a month") is None)
    _check("nor an estimate", money("$202,950 est. revenue this year") is None)
    _check("a figure with no money in it is not proof", money("made 4,200 sales in all") is None)
    _check("a trivial sum is not proof", money("All-time revenue $441") is None)
    _check("thousands written short still count", money("My business makes $12K/month") is not None)
    _check("and millions", money("sales reached $4.1M in year one") is not None)

    failed = _row(what="failed", quote="having never made a single penny")
    _check("failure needs no figure", judged(failed, _page(body="It closed, having never made a single penny.")) is not None)


def test_who_says_it() -> None:
    def source(url, model_says="third party", title="A report", sellers=frozenset(), what="earned"):
        row = proof.parse_judgement(_row(what=what, source=model_says))[0]
        kept = proof._kept(row, [_page(url=url, title=title)], sellers, "2026-10-17")
        return kept["from"]

    _check("a personal post is the writer's own, whatever the model says",
           source("https://medium.com/@a/post") == "seller")
    _check("a subdomain of one too", source("https://someone.substack.com/p/x") == "seller")
    _check("as is a how-I title", source("https://news.example/a", title="How I built a kettle empire") == "seller")
    _check("a host the paper cites for its prices cannot be its proof",
           source("https://kettlealerts.example/data", sellers=frozenset(["kettlealerts.example"])) == "seller")
    _check("payment-verified revenue stands whatever the model says",
           source("https://trustmrr.com/startup/x", model_says="seller") == "third party")
    _check("a news host stays as judged", source("https://news.example/a") == "third party")
    _check("and a seller's story stays a seller's", source("https://news.example/a", model_says="seller") == "seller")


def test_reading_replies() -> None:
    parse = proof.parse_judgement
    _check("nothing found", parse("NONE") == [] and parse("none.") == [] and parse("`NONE`") == [])
    _check("no reply is not nothing found", parse("") is None and parse(None) is None)
    _check("prose is not nothing found", parse("I think this looks promising") is None)
    _check("one object per line", len(parse(_row() + "\n" + _row(n=2))) == 2)
    _check("an array", len(parse("[" + _row() + "," + _row(n=2) + "]")) == 2)
    _check("in a code fence", len(parse("```json\n" + _row() + "\n```")) == 1)
    _check("a broken line is skipped, the good one kept", len(parse('{"n": 1, "what\n' + _row())) == 1)
    _check("a stray list is unreadable", parse("[1, 2]") is None)


def test_size() -> None:
    stub = _Stub(results=[_page(body="y" * 5000) for _ in range(5)])
    ledger: dict = {}
    asyncio.run(proof.step("Kettle price alerts", ledger, stub.search, stub.complete))
    user, tokens, cap = stub.judged[0]
    _check("a long page is cut before the model sees it", len(user) < 5 * proof._BODY_CAP + 500, str(len(user)))
    chars = len(proof._JUDGE_SYSTEM) + len(user)
    _check("prompt plus answer fits 20b's 8000 TPM at 3 chars a token", chars // 3 + cap < 8000, str(chars // 3 + cap))
    _check("and the session reserves at least that", proof.RESERVE >= chars // 3 + cap, str(proof.RESERVE))
    _check("a budget a reasoning model cannot spend to nothing", tokens >= 1024 and cap >= tokens)


def test_a_session_tests_the_next_idea() -> None:
    overnight, folder, tmp = _setup()
    stub = _Stub(replies=[_row(), "NONE", _row(what="failed", quote="Most closed within two years")])
    out = asyncio.run(overnight.tick(NIGHT_TIME, search=stub.search, complete=stub.complete))
    _check("it says what it found", "proof checked: Kettle price alerts - mixed" in out, out)
    _check("no paper write, no widening", not stub.writes and not stub.plans)
    queries = [q for q, _ in stub.searches]
    _check("three searches, all on that idea", len(queries) == 3 and all("Kettle price alerts" in q for q in queries), str(queries))
    _check("the first on payment-verified data", stub.searches[0][1]["include_domains"] == ["trustmrr.com"])
    _check("the others on the open web", stub.searches[1][1]["include_domains"] is None)
    _check("read as excerpts", all(k["highlights"] for _, k in stub.searches))
    _check("a failed search would be heard about", all(k["raise_errors"] for _, k in stub.searches))
    text = (folder / "paper.md").read_text(encoding="utf-8")
    _check("the label comes from the ledger", "Kettle price alerts** - priced at $29 (evidence: mixed)" in text, text)
    _check("an idea not yet tested keeps its label", "(evidence: no income shown)" in text, text)
    _check("a proof section sits after the summary, before the ideas",
           text.index("## Proof checked") < text.index("## Kettle alerts") and text.index("Some ideas") < text.index("## Proof checked"), text)
    _check("it explains its labels", "Independent: someone with nothing to sell" in text)
    _check("and shows the words and the page", '"revenue of $40,000 last year" (third party) <https://news.example/a>' in text, text)

    import work
    entry = work._load()[-1]
    _check("the digest gets the paper", entry["finished"] and entry["searches"] == 3, str(entry))
    tmp.cleanup()


def test_it_works_down_the_list_then_widens() -> None:
    overnight, folder, tmp = _setup()
    stub = _Stub(replies=["NONE", "NONE", "NONE", _row()])
    import goals
    gid = goals.load()[0]["id"]
    asyncio.run(overnight.tick(NIGHT_TIME, search=stub.search, complete=stub.complete))
    out = asyncio.run(overnight.tick(NIGHT_TIME, search=stub.search, complete=stub.complete))
    _check("the second session takes the second idea", "Birdhouse plan e-books - independent" in out, out)
    text = (folder / "paper.md").read_text(encoding="utf-8")
    _check("one section, not two", text.count("## Proof checked") == 1, text)
    _check("the shortlist re-sorts on what was found",
           "1. Birdhouse plan e-books: sold online (evidence: independent)" in text
           and "2. **Kettle price alerts** - priced at $29 (evidence: no income shown)" in text, text)

    before = len(stub.searches)
    out = asyncio.run(overnight.tick(NIGHT_TIME, search=stub.search, complete=stub.complete))
    _check("with the list tested it looks for more", stub.plans >= 1 and len(stub.searches) > before, out)
    _check("and that session writes the paper", len(stub.writes) == 1, out)
    sent = stub.writes[0]
    _check("the writer never sees the proof section", "## Proof checked" not in sent, sent[:400])
    _check("it is given the names to keep", "use exactly this name" in sent and "Kettle price alerts" in sent, sent[-300:])
    text = (folder / "paper.md").read_text(encoding="utf-8")
    _check("the section survives the revision", "## Proof checked" in text and "Birdhouse plan e-books" in text, text)
    _check("a renamed idea keeps what was found", "Kettle alerts** - priced (evidence: no income shown)" in text, text)
    _check("a new idea is unchecked", "Rain gauges** - new (evidence: unchecked)" in text, text)

    stub.judged.clear()
    out = asyncio.run(overnight.tick(NIGHT_TIME, search=stub.search, complete=stub.complete))
    _check("the new idea is next", "Rain gauges" in out, out)
    _check("not the renamed one, already tested", not any("Kettle" in u for u, _, _ in stub.judged))
    _check("the ledger is on disk under the goal", goals.load()[0]["id"] == gid and (folder / "proof.json").exists())
    tmp.cleanup()


def test_failures_do_not_become_findings() -> None:
    overnight, folder, tmp = _setup()
    stub = _Stub()
    stub.search_fails = True
    out = asyncio.run(overnight.tick(NIGHT_TIME, search=stub.search, complete=stub.complete))
    _check("a failed search is reported", "search failed" in out, out)
    _check("and says unchecked, not no income", "unchecked" in out, out)
    ledger = proof.load(folder)
    _check("it is not counted as looked", proof.left("Kettle price alerts", ledger) == 3, str(ledger))
    _check("the paper keeps its old label", "priced at $29 (evidence: independent)" in (folder / "paper.md").read_text(encoding="utf-8"))
    tmp.cleanup()

    overnight, folder, tmp = _setup()
    stub = _Stub(replies=["", "", "NONE", "NONE"])
    out = asyncio.run(overnight.tick(NIGHT_TIME, search=stub.search, complete=stub.complete))
    _check("an empty reply stops the session", "model reply unreadable" in out and len(stub.searches) == 1, out)
    _check("and costs that search nothing", proof.left("Kettle price alerts", proof.load(folder)) == 3)
    out = asyncio.run(overnight.tick(NIGHT_TIME, search=stub.search, complete=stub.complete))
    entry = proof._find(proof.load(folder), "Kettle price alerts")
    _check("a second one moves past it, so it cannot wedge the night", entry["searches"] == 1, str(entry))
    _check("without claiming to have read it", entry["read"] == 0 and proof.label(entry) == "unchecked", str(entry))
    _check("and the flag does not stick", "stalled" not in entry, str(entry))
    out = asyncio.run(overnight.tick(NIGHT_TIME, search=stub.search, complete=stub.complete))
    entry = proof._find(proof.load(folder), "Kettle price alerts")
    _check("then it carries on", entry["searches"] == 3 and entry["read"] == 2, str(entry))
    tmp.cleanup()

    overnight, folder, tmp = _setup()
    stub = _Stub(results=[])
    asyncio.run(overnight.tick(NIGHT_TIME, search=stub.search, complete=stub.complete))
    entry = proof._find(proof.load(folder), "Kettle price alerts")
    _check("no results is a real answer, for no model call",
           entry["searches"] == 3 and entry["read"] == 3 and not stub.judged, str(entry))
    tmp.cleanup()

    overnight, folder, tmp = _setup()
    response = httpx.Response(429, request=httpx.Request("POST", "https://api.groq.com/x"))
    stub = _Stub(replies=[_row()])
    real = stub.complete
    calls = {"n": 0}

    async def limited(system, user, max_tokens, cap=None):
        calls["n"] += 1
        if calls["n"] == 2:
            raise openai.RateLimitError("Rate limit reached on tokens per minute", response=response, body=None)
        return await real(system, user, max_tokens, cap)

    out = asyncio.run(overnight.tick(NIGHT_TIME, search=stub.search, complete=limited))
    _check("a 429 stops the night as it always did", "rate limited" in out, out)
    entry = proof._find(proof.load(folder), "Kettle price alerts")
    _check("with what it had already found saved", entry["searches"] == 1 and entry["evidence"], str(entry))
    out = asyncio.run(overnight.tick(datetime(2026, 10, 18, 2, 0, tzinfo=clock.USER_TZ), search=stub.search, complete=stub.complete))
    entry = proof._find(proof.load(folder), "Kettle price alerts")
    _check("the next night picks up at the next search", entry["searches"] == 3 and len(stub.searches) == 4, str(entry))
    tmp.cleanup()

    overnight, folder, tmp = _setup()
    (folder / "proof.json").write_text("{not json", encoding="utf-8")
    stub = _Stub()
    out = asyncio.run(overnight.tick(NIGHT_TIME, search=stub.search, complete=stub.complete))
    _check("a broken ledger costs a repeat, not the night", "proof checked" in out, out)
    tmp.cleanup()


def test_budgets() -> None:
    import work
    overnight, folder, tmp = _setup()
    stub = _Stub()
    work.log_session(folder.name, "research", "earlier", 0, config.OVERNIGHT_SEARCHES - 1, [], [], now=NIGHT_TIME)
    asyncio.run(overnight.tick(NIGHT_TIME, search=stub.search, complete=stub.complete))
    _check("it stops at the night's search cap", len(stub.searches) == 1, str(len(stub.searches)))
    _check("and finishes that idea tomorrow", proof.left("Kettle price alerts", proof.load(folder)) == 2)
    tmp.cleanup()

    overnight, folder, tmp = _setup()
    stub = _Stub()
    work.log_session(folder.name, "research", "earlier", config.OVERNIGHT_TOKENS - proof.RESERVE + 1, 0, [], [], now=NIGHT_TIME)
    out = asyncio.run(overnight.tick(NIGHT_TIME, search=stub.search, complete=stub.complete))
    _check("with too little token room it does not search", not stub.searches and "waits for budget" in out, out)
    tmp.cleanup()


def test_what_it_leaves_alone() -> None:
    overnight, folder, tmp = _setup(statement="Study how kettles boil, ranked shortlist")
    stub = _Stub()
    asyncio.run(overnight.tick(NIGHT_TIME, search=stub.search, complete=stub.complete))
    _check("a goal about something else is not asked about income", stub.plans and not stub.judged, str(stub.plans))
    tmp.cleanup()

    overnight, folder, tmp = _setup(paper="# Paper\n\n## Findings\nx\n\n## What I am unsure about\nMuch.\n")
    stub = _Stub()
    asyncio.run(overnight.tick(NIGHT_TIME, search=stub.search, complete=stub.complete))
    _check("a paper with no shortlist has nothing to test", stub.plans and not stub.judged)
    tmp.cleanup()

    overnight, folder, tmp = _setup(paper=None)
    stub = _Stub()
    asyncio.run(overnight.tick(NIGHT_TIME, search=stub.search, complete=stub.complete))
    _check("nor does no paper", stub.plans and not stub.judged)
    tmp.cleanup()


def test_the_digest_says_what_was_found() -> None:
    overnight, folder, tmp = _setup()
    stub = _Stub(replies=[_row()])
    import work
    asyncio.run(overnight.tick(NIGHT_TIME, search=stub.search, complete=stub.complete))
    text, papers = overnight.digest_section(work.night_of(NIGHT_TIME))
    _check("the verdict is in the digest", "Proof checked: Kettle price alerts - independent" in text, text)
    _check("and the paper is attached", [p.name for p in papers] == ["paper.md"], str(papers))
    tmp.cleanup()


def test_section_text() -> None:
    ledger = {"a": _full_entry(evidence=[_ev(quote=f"q{i} revenue") for i in range(5)]),
              "b": _full_entry("Birdhouses", read=3),
              "c": {"name": "Not searched", "searches": 0, "read": 0, "evidence": []}}
    text = proof.section(ledger)
    _check("at most three quotes an idea", text.count('"q') == 3, text)
    _check("an idea with nothing says so", "**Birdhouses**: no income shown, after 3 searches" in text, text)
    _check("one never searched is not listed", "Not searched" not in text, text)
    _check("strongest first", text.index("Kettle") < text.index("Birdhouses"), text)
    _check("no ledger, no section", proof.with_section("# P\n\n## A\nx\n", {}) == "# P\n\n## A\nx\n")
    _check("adding it twice leaves one", proof.with_section(proof.with_section(PAPER, ledger), ledger).count("## Proof checked") == 1)
    _check("taking it out restores the paper", proof.without_section(proof.with_section(PAPER, ledger)) == PAPER)
    _check("a paper with no sections still works", "## Proof checked" in proof.with_section("# P", ledger))


if __name__ == "__main__":
    test_labels()
    test_only_a_real_quote_counts()
    test_who_says_it()
    test_reading_replies()
    test_size()
    test_a_session_tests_the_next_idea()
    test_it_works_down_the_list_then_widens()
    test_failures_do_not_become_findings()
    test_budgets()
    test_what_it_leaves_alone()
    test_the_digest_says_what_was_found()
    test_section_text()
    print(f"\n{len(_failures)} failures" if _failures else "\nAll pass")
    sys.exit(1 if _failures else 0)
