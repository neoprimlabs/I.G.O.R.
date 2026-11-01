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


def _page(url="https://news.example/a", body="Shops reported revenue of $40,000 last year. Most kettle shops closed within 2 years.",
          title="A report"):
    return {"title": title, "url": url, "body": body, "published": ""}


def _row(n=1, what="earned", source="third party", quote="revenue of $40,000 last year", business="idea", scope="many"):
    who = "news" if source == "third party" else "the earner"
    return json.dumps({"n": n, "what": what, "business": business, "scope": scope, "who": who, "quote": quote})


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
    return {"url": url, "quote": quote, "what": what, "from": source, "many": True, "at": "2026-10-17"}


def test_labels() -> None:
    label = proof.label
    _check("someone with nothing to sell reporting money is independent",
           label(_full_entry(evidence=[_ev()])) == "independent")
    _check("the earner's word alone is self-reported", label(_full_entry(evidence=[_ev(source="seller")])) == "self-reported")
    _check("a third party reporting failure is against", label(_full_entry(evidence=[_ev("failed")])) == "against")
    _check("both are mixed", label(_full_entry(evidence=[_ev(), _ev("failed")])) == "mixed")
    _check("payment data on a nearby product is only that",
           label(_full_entry(evidence=[_ev(source="verified")])) == "similar product")
    _check("and beats the earner's word", label(_full_entry(evidence=[_ev(source="verified"), _ev(source="seller")])) == "similar product")
    _check("a study of many still decides against it",
           label(_full_entry(evidence=[_ev(source="verified"), _ev("failed")])) == "mixed")
    _check("independent proof outranks it", proof.KINDS.index("independent") < proof.KINDS.index("similar product") < proof.KINDS.index("self-reported"))
    _check("one business closing, even in the news, does not sink an idea",
           label(_full_entry(evidence=[{**_ev("failed"), "many": False}])) == "no income shown")
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
    _check("a quote the model made up is dropped", judged(_row(quote="profits fell by half across the region")) is None)
    _check("a figure it changed is dropped however close the rest", judged(_row(quote="revenue of $90,000 last year")) is None)
    survey = _page(body="About 80% of authors with 1-3 books made under $100 a month, a survey found.")
    kept = judged(_row(what="failed", quote="About 80% of authors with 1-3 books reported under $100 a month"), survey)
    _check("a reworded quote with the same numbers is kept", kept is not None)
    _check("in the page's words, not the model's",
           kept and "made under $100" in kept["quote"] and "reported" not in kept["quote"], str(kept))
    _check("a reworded quote with a different number is not",
           judged(_row(what="failed", quote="About 90% of authors with 1-3 books reported under $100 a month"), survey) is None)
    _check("a stray fragment is no failure", judged(_row(what="failed", quote="revenue of $40,000"), _page()) is None)
    _check("who says it is read as a fact",
           proof._kept({"n": 1, "what": "earned", "business": "idea", "who": "Researchers", "quote": "revenue of $40,000 last year"},
                       [_page()], frozenset(), "2026-10-17")["from"] == "third party")
    _check("and an answer it does not know counts for nothing",
           proof._kept({"n": 1, "what": "earned", "business": "idea", "who": "a trusted source", "quote": "revenue of $40,000 last year"},
                       [_page()], frozenset(), "2026-10-17")["from"] == "seller")
    _check("one in the page's title counts, since the model is shown it",
           judged(_row(quote="$14,934 last 30 days"), _page(title="Certifyr - $14,934 last 30 days | TrustMRR")) is not None)
    _check("past what the model read does not count",
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

    _check("money from selling tools to people trying the idea is not proof of the idea",
           judged(_row(business="tools")) is None)
    failed = _row(what="failed", quote="having never made a single penny")
    _check("failure needs no figure", judged(failed, _page(body="It closed, having never made a single penny.")) is not None)


def test_who_says_it() -> None:
    def source(url, model_says="third party", title="A report", sellers=frozenset(), what="earned", business="idea"):
        row = proof.parse_judgement(_row(what=what, source=model_says, business=business))[0]
        kept = proof._kept(row, [_page(url=url, title=title)], sellers, "2026-10-17")
        return kept["from"]

    _check("a personal post is the writer's own, whatever the model says",
           source("https://medium.com/@a/post") == "seller")
    _check("a subdomain of one too", source("https://someone.substack.com/p/x") == "seller")
    _check("as is a how-I title", source("https://news.example/a", title="How I built a kettle empire") == "seller")
    _check("a host the paper cites for its prices cannot be its proof",
           source("https://kettlealerts.example/data", sellers=frozenset(["kettlealerts.example"])) == "seller")
    _check("payment-verified revenue stands whatever the model says",
           source("https://trustmrr.com/startup/x", model_says="seller") == "verified")
    _check("and is never called third party, whatever the model says",
           source("https://trustmrr.com/startup/x", model_says="third party") == "verified")
    _check("unless the model never said it does the idea, which earns no promotion",
           source("https://trustmrr.com/startup/x", model_says="seller", business="") == "seller")
    _check("a news host stays as judged", source("https://news.example/a") == "third party")
    _check("and a seller's story stays a seller's", source("https://news.example/a", model_says="seller") == "seller")


def test_tools_and_ideas() -> None:
    def kept(idea, business, url="https://trustmrr.com/startup/x", who="the earner"):
        row = {"n": 1, "what": "earned", "business": business, "scope": "one", "who": who, "quote": "revenue of $40,000 last year"}
        return proof._kept(row, [_page(url=url)], frozenset(), "2026-10-17", idea)

    _check("a tool sold to people trying the idea is not proof of it", kept("Birdhouse plan e-books", "tools") is None)
    _check("where selling software is the idea, it is proof, whatever the model answered",
           kept("Kettle API wrappers", "tools")["from"] == "verified")
    _check("the same for a service", kept("Kettle alert services", "tools")["from"] == "verified")
    _check("a business doing the idea is promoted on verified data", kept("Birdhouse plan e-books", "idea")["from"] == "verified")
    named = proof._kept({"n": 1, "what": "earned", "business": "idea", "scope": "one", "who": "the earner", "quote": "revenue of $40,000 last year"},
                        [_page(url="https://trustmrr.com/startup/x", title="AnyKettle - $3,000 last 30 days | TrustMRR")], frozenset(), "2026-10-17", "Kettle API wrappers")
    _check("and the product is named, so a reader can judge it", named["product"] == "AnyKettle", str(named))
    entry = _full_entry(evidence=[named])
    _check("in the section", "(earned, verified, AnyKettle)" in proof.section({"a": entry}), proof.section({"a": entry}))
    _check("a failure on that site earns no standing",
           proof._kept({"n": 1, "what": "failed", "business": "idea", "scope": "many", "who": "researchers", "quote": "revenue of $40,000 last year"},
                       [_page(url="https://trustmrr.com/startup/x")], frozenset(), "2026-10-17", "Kettle API wrappers")["from"] == "seller")
    def said_market(url):
        row = {"n": 1, "what": "earned", "business": "idea", "scope": "one", "who": "marketplace data", "quote": "revenue of $40,000 last year"}
        return proof._kept(row, [_page(url=url)], frozenset(), "2026-10-17", "Kettle API wrappers")["from"]
    _check("a real marketplace has marketplace data", said_market("https://acquire.com/listing/x") == "third party")
    _check("an article site does not", said_market("https://sidehustles.example/how-much") == "seller")
    _check("the same answer elsewhere stays the earner's", kept("Kettle API wrappers", "idea", url="https://news.example/a")["from"] == "seller")


def test_what_counts_as_against() -> None:
    def many(quote, idea="Birdhouse plan e-books", title="A report", scope="many"):
        row = {"n": 1, "what": "failed", "business": "idea", "scope": scope, "who": "researchers", "quote": quote}
        return proof._kept(row, [_page(body=quote, title=title)], frozenset(), "2026-10-17", idea)["many"]

    _check("a share of people, on this idea", many("About 80% of authors with 1-3 e-books earn under $100 a month"))
    _check("a figure on all startups says nothing of this one", not many("93% of startups ultimately shut down for good"))
    _check("though the page's title can name the idea", many("93% of them ultimately shut down for good", title="Birdhouse plans: a study"))
    _check("words with no figure are not data", not many("Recorded closure with no past end date", title="The birdhouse die-off"))
    _check("one business is not many", not many("Birdhouse plan shop lost 90% of sales and closed", scope="one"))


def test_reading_replies() -> None:
    parse = proof.parse_judgement
    _check("nothing found", parse("NONE") == [] and parse("none.") == [] and parse("`NONE`") == [])
    _check("said after some words", parse("No excerpt here qualifies.\nNONE") == [])
    _check("no reply is not nothing found", parse("") is None and parse(None) is None)
    _check("prose is not nothing found", parse("I think this looks promising") is None)
    _check("one object per line", len(parse(_row() + "\n" + _row(n=2))) == 2)
    _check("an array", len(parse("[" + _row() + "," + _row(n=2) + "]")) == 2)
    _check("in a code fence", len(parse("```json\n" + _row() + "\n```")) == 1)
    _check("a broken line is skipped, the good one kept", len(parse('{"n": 1, "what\n' + _row())) == 1)
    _check("spread over lines", parse('{\n "n": 1,\n "what": "earned"\n}') == [{"n": 1, "what": "earned"}])
    _check("after a sentence of preamble", len(parse("Here is what I found:\n" + _row())) == 1)
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
    stub = _Stub(replies=[_row(), "NONE", _row(what="failed", quote="Most kettle shops closed within 2 years")])
    out = asyncio.run(overnight.tick(NIGHT_TIME, search=stub.search, complete=stub.complete))
    _check("it says what it found", "proof checked: Kettle price alerts - mixed" in out, out)
    _check("a label the shortlist sorts on", "mixed" in proof.KINDS and "similar product" in overnight._EVIDENCE_KINDS)
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
    _check("and that one earner is not everyone", "not that most people do" in text)
    _check("and shows the words and the page", '"revenue of $40,000 last year" (earned, third party) <https://news.example/a>' in text, text)

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
    stub = _Stub(replies=["", _row(), "I think this looks promising", "", "NONE"])
    out = asyncio.run(overnight.tick(NIGHT_TIME, search=stub.search, complete=stub.complete))
    entry = proof._find(proof.load(folder), "Kettle price alerts")
    _check("an empty reply is asked again on the same excerpts", len(stub.judged) == 5 and stub.judged[0][0] == stub.judged[1][0], str(len(stub.judged)))
    _check("with no second search", len(stub.searches) == 3, str(len(stub.searches)))
    _check("what the retry found counts", entry["evidence"], str(entry))
    _check("two bad replies count as looked but not read", entry["searches"] == 3 and entry["read"] == 2, str(entry))
    tmp.cleanup()

    overnight, folder, tmp = _setup()
    stub = _Stub(replies=[""] * 6)
    out = asyncio.run(overnight.tick(NIGHT_TIME, search=stub.search, complete=stub.complete))
    entry = proof._find(proof.load(folder), "Kettle price alerts")
    _check("a model that never answers cannot wedge the night", proof.left("Kettle price alerts", proof.load(folder)) == 0, str(entry))
    _check("and nothing is claimed from it", proof.label(entry) == "unchecked" and "unchecked" in out, out)
    out = asyncio.run(overnight.tick(NIGHT_TIME, search=stub.search, complete=stub.complete))
    _check("the next idea gets its turn", "Birdhouse" in out, out)
    tmp.cleanup()

    overnight, folder, tmp = _setup()
    stub = _Stub()
    asyncio.run(overnight.tick(NIGHT_TIME, search=stub.search, complete=stub.complete))
    _check("each search says what it was looking for",
           "payment-verified" in stub.judged[0][0] and "really earn" in stub.judged[1][0] and "failures" in stub.judged[2][0],
           stub.judged[2][0][:200])
    _check("and a failure search asks Exa about failure, not money",
           "closed or failed" in stub.searches[2][1]["highlights"] and "money actually" in stub.searches[0][1]["highlights"])
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
    many = _full_entry(evidence=[_ev(quote=f"win {i} revenue") for i in range(4)] + [_ev("failed", "seller", quote="we closed")])
    shown = proof._shown(many)
    _check("a failure is shown beside the wins, even a founder's own",
           len(shown) == 3 and shown[-1]["quote"] == "we closed", str(shown))
    ledger2: dict = {}
    wins = "\n".join(_row(quote=f"revenue of ${i},000 here") for i in range(2, 9))
    loss = _row(what="failed", source="seller", quote="Sadly we closed the doors for good")
    page = _page(body=" ".join(f"revenue of ${i},000 here." for i in range(2, 9)) + " Sadly we closed the doors for good.")
    stub = _Stub(replies=[wins + "\n" + loss], results=[page])
    asyncio.run(proof.step("Kettle price alerts", ledger2, stub.search, stub.complete))
    rows = proof._find(ledger2, "Kettle price alerts")["evidence"]
    _check("wins cannot crowd a failure out of the ledger",
           len(rows) == proof._KEEP_EARNED + 1 and rows[-1]["what"] == "failed", str(rows))
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
    test_tools_and_ideas()
    test_what_counts_as_against()
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
