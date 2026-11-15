"""Tests for role briefs: agents/briefs.py and its hooks in overnight.py.

Run: python tests/test_briefs.py   (repo root, stdlib only, no API calls)
Every posting, company and profile line here is invented.
"""

import asyncio
import sys
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import httpx
import openai

import clock
import config

_failures: list[str] = []
NOW = datetime(2026, 11, 16, 7, 0, tzinfo=timezone.utc)
NIGHT_TIME = datetime(2026, 11, 17, 2, 0, tzinfo=clock.USER_TZ)
NIGHT = "2026-11-17"

PROFILE = """# Profile
- Two years of retail customer service, handling returns and complaints
- Built a personal AI assistant that runs on a cloud server, using LLM APIs daily
- Tested language model outputs for made-up facts and wrote up each failure with evidence
"""

DESCRIPTION = """<p>Quillfield builds tutoring software for community colleges.</p>
<ul><li>Hands-on experience using multiple LLMs</li>
<li>Experience writing clear reports of model failures</li>
<li>3+ years of experience in Kubernetes administration</li></ul>
<p>Pay: $40 - $55 per hour, remote in the US.</p>"""

JOB = {"title": "AI Evaluation Specialist", "company": "Quillfield", "url": "https://jobs.example.org/quillfield/1",
       "source": "greenhouse:quillfield", "description": DESCRIPTION}


def _check(name: str, condition: bool, detail: str = "") -> None:
    if condition:
        print(f"  pass  {name}")
    else:
        print(f"  FAIL  {name}  {detail}")
        _failures.append(name)


def _run(coro):
    return asyncio.run(coro)


def _fresh():
    from agents import briefs
    # history.db stays open on Windows, so its folder cannot always be removed there.
    tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
    config.MEMORY_DIR = Path(tmp.name)
    (config.MEMORY_DIR / "private").mkdir()
    (config.MEMORY_DIR / "private" / "profile.md").write_text(PROFILE, encoding="utf-8")
    return briefs, tmp


def _open(url):
    return 200


class _Web:
    """Fake search and model. Search pages always name the company unless told otherwise."""

    # The posting's asks come out years first: 1 is Kubernetes, which shares no word with the profile and
    # is never offered; 2 is the LLM ask, whose one candidate is A.
    def __init__(self, fail_search=False, reply=None, match_reply="1 GAP\n2 A\n"):
        self.queries, self.calls, self.fail_search = [], [], fail_search
        self.reply, self.match_reply = reply, match_reply

    async def search(self, query, **kw):
        self.queries.append((query, kw))
        if self.fail_search:
            raise RuntimeError("search down")
        return [{"title": "Quillfield raises funding", "url": "https://news.example.org/q",
                 "body": "Quillfield raised $12 million in October to expand its tutoring software. "
                         "Evaluators at Quillfield are paid $45 an hour, one contractor wrote."},
                {"title": "Quillfeld Bakery opens", "url": "https://bakery.example.org/",
                 "body": "Quillfeld Bakery sells bread in Ohio."}]

    async def complete(self, system, user, max_tokens, cap=None):
        from agents import briefs
        self.calls.append(system)
        if system == briefs.MATCH_SYSTEM:
            return self.match_reply
        if self.reply is not None:
            return self.reply
        return ("1 | Quillfield raised $12 million in October to expand its tutoring software.\n"
                "1 | Evaluators at Quillfield are paid $45 an hour, one contractor wrote.")


def test_queue_takes_only_picks_once() -> None:
    briefs, tmp = _fresh()
    other = dict(JOB, url="https://jobs.example.org/other/2", title="Nurse")
    _check("picks queued", briefs.queue([JOB, other], "1 APPLY fits\n2 SKIP nursing", now=NOW) == 1)
    _check("the same posting is not queued twice", briefs.queue([JOB], "1 APPLY fits", now=NOW) == 0)
    _check("no notes, no picks", briefs.queue([other], "", now=NOW) == 0)
    _check("a broken call never raises", briefs.queue([{"title": None}], "1 APPLY", now=NOW) == 0)
    tmp.cleanup()


def test_next_due_counts_attempts_and_drops_stale() -> None:
    briefs, tmp = _fresh()
    briefs.queue([JOB], "1 APPLY", now=NOW)
    first = briefs.next_due(NIGHT, NOW)
    _check("due tonight", first is not None and first["attempts"] == 1, str(first))
    _check("second attempt allowed", briefs.next_due(NIGHT, NOW)["attempts"] == 2)
    _check("a posting that failed twice is let go", briefs.next_due(NIGHT, NOW) is None)
    briefs.queue([JOB], "1 APPLY", now=NOW)
    _check("stale after three days", briefs.next_due(NIGHT, NOW + timedelta(days=4)) is None)
    tmp.cleanup()


def test_two_briefs_a_night_at_most() -> None:
    import work
    briefs, tmp = _fresh()
    for _ in range(2):
        work.log_session(briefs.GOAL, "brief", "brief written: x", 0, 0, [], [], now=NIGHT_TIME)
    briefs.queue([JOB], "1 APPLY", now=NOW)
    _check("the night's cap holds", briefs.next_due(NIGHT, NIGHT_TIME) is None)
    tmp.cleanup()


def test_gates() -> None:
    briefs, tmp = _fresh()
    dropped, _ = briefs.gates(dict(JOB), NOW, status=lambda url: 404)
    _check("a closed posting is dropped", dropped == "the posting is closed", dropped)
    _, warnings = briefs.gates(dict(JOB), NOW, status=lambda url: None)
    _check("unreachable is a warning, not a drop", any("Could not open" in w for w in warnings), str(warnings))
    agency = dict(JOB, description="We are hiring on behalf of our client, a tutoring company.")
    _, warnings = briefs.gates(agency, NOW, status=_open)
    _check("an agency is flagged", any("agency" in w for w in warnings), str(warnings))
    volunteer = dict(JOB, description="This is a volunteer role, paid on results.")
    _, warnings = briefs.gates(volunteer, NOW, status=_open)
    _check("a red flag is named", any("Red flag" in w for w in warnings), str(warnings))
    import history
    history.record("jobs", [("AI Evaluation Specialist - Quillfield", "posted", "greenhouse", "https://old")],
                   NOW - timedelta(days=20))
    _, warnings = briefs.gates(dict(JOB), NOW, status=_open)
    _check("a repost is flagged with its first date", any("Posted before" in w for w in warnings), str(warnings))
    _, warnings = briefs.gates(dict(JOB, title="Data Annotator"), NOW, status=_open)
    _check("a different title is not a repost", not any("Posted before" in w for w in warnings), str(warnings))
    tmp.cleanup()


def test_posting_pay() -> None:
    from agents import briefs
    _check("the posting's own pay line", briefs.posting_pay(DESCRIPTION) == ["Pay: $40 - $55 per hour, remote in the US."],
           str(briefs.posting_pay(DESCRIPTION)))
    _check("a funding figure is not pay", briefs.posting_pay("<p>We raised $5 million last year.</p>") == [])


def test_quotes_are_held_to_the_page() -> None:
    from agents import briefs
    results = [{"title": "T", "url": "https://a", "body": "Quillfield serves 40 colleges across three states."}]
    kept = briefs.keep_quotes([(1, "Quillfield serves 40 colleges across three states.")], results, "about")
    _check("a real quote is kept", kept == [("Quillfield serves 40 colleges across three states.", "https://a")], str(kept))
    _check("an invented quote is dropped",
           briefs.keep_quotes([(1, "Quillfield is the market leader with 900 clients.")], results, "about") == [])
    _check("pay without a figure is dropped",
           briefs.keep_quotes([(1, "Quillfield serves 40 colleges across three states.")], results, "pay") == [])
    _check("an excerpt number out of range is dropped", briefs.keep_quotes([(3, "anything at all here")], results, "about") == [])
    _check("NONE reads as nothing", briefs.parse_quotes("NONE") == [])
    _check("prose reads as unreadable", briefs.parse_quotes("I could not find anything useful.") is None)


def test_similar_names_are_not_the_company() -> None:
    from agents import briefs
    _check("the company by name", briefs._names_company("Scale AI", {"title": "Scale AI hires", "body": ""}))
    _check("by its domain", briefs._names_company("Scale AI", {"title": "", "body": "", "url": "https://scale.ai/x"}))
    _check("not a similar name", not briefs._names_company("Scale AI", {"title": "Scaleway cloud", "body": "Scaleway"}))


def test_match_picks_only_offered_lines() -> None:
    briefs, tmp = _fresh()
    asks = ["Hands-on experience using multiple LLMs", "Experience writing clear reports of model failures",
            "3+ years of experience in Kubernetes administration"]

    async def model(system, user, max_tokens, cap=None):
        return "1 A\n2 A\n3 A"
    table, read = _run(briefs.match(asks, PROFILE, model))
    _check("read", read)
    _check("an LLM ask maps to the profile's own line", "LLM APIs" in table[0][1], str(table[0]))
    _check("an ask sharing no word with the profile is a gap", table[2][1] == "", str(table[2]))

    async def letter_not_offered(system, user, max_tokens, cap=None):
        return "1 Z\n2 GAP"
    table, _ = _run(briefs.match(asks[:2], PROFILE, letter_not_offered))
    _check("a letter not offered is a gap", table[0][1] == "" and table[1][1] == "", str(table))

    calls = []

    async def garbled(system, user, max_tokens, cap=None):
        calls.append(1)
        return "These all look fine to me."
    table, read = _run(briefs.match(asks[:1], PROFILE, garbled))
    _check("unreadable twice is not checked, not a gap", not read and len(calls) == 2, str((read, calls)))
    tmp.cleanup()


def test_run_writes_a_brief() -> None:
    briefs, tmp = _fresh()
    briefs.queue([JOB], "1 APPLY", now=NOW)
    item = briefs.next_due(NIGHT, NOW)
    web = _Web()
    summary, searches, files = _run(briefs.run(item, web.search, web.complete, NIGHT, NOW, status=_open))
    text = (config.MEMORY_DIR / files[0]).read_text(encoding="utf-8")
    _check("written", summary == "brief written: AI Evaluation Specialist - Quillfield", summary)
    _check("three searches", searches == 3 and len(web.queries) == 3, str(web.queries))
    _check("news is limited to 90 days", web.queries[1][1].get("start_published_date") == "2026-08-18",
           str(web.queries[1][1]))
    _check("the posting's pay is shown", '"Pay: $40 - $55 per hour, remote in the US."' in text)
    _check("a checked quote with its link", "raised $12 million" in text and "<https://news.example.org/q>" in text)
    _check("pay others report needs a figure", "paid $45 an hour" in text)
    _check("the similar name never reaches the model", all("Bakery" not in str(q) for q in web.calls))
    _check("the profile's own words", "using LLM APIs daily" in text)
    _check("no em dash", "—" not in text)
    _check("the queue is empty after", briefs._load_queue() == [])
    tmp.cleanup()


def test_run_says_what_it_could_not_check() -> None:
    briefs, tmp = _fresh()
    briefs.queue([JOB], "1 APPLY", now=NOW)
    item = briefs.next_due(NIGHT, NOW)
    web = _Web(fail_search=True)
    _, searches, files = _run(briefs.run(item, web.search, web.complete, NIGHT, NOW, status=_open))
    text = (config.MEMORY_DIR / files[0]).read_text(encoding="utf-8")
    _check("a failed search is not counted", searches == 0)
    _check("and is said, not hidden", text.count("Not checked: the search or the reading failed.") == 3, text)
    _check("the posting's own facts still go out", "$40 - $55" in text)
    tmp.cleanup()


def test_closed_posting_is_skipped() -> None:
    briefs, tmp = _fresh()
    briefs.queue([JOB], "1 APPLY", now=NOW)
    item = briefs.next_due(NIGHT, NOW)
    web = _Web()
    summary, searches, files = _run(briefs.run(item, web.search, web.complete, NIGHT, NOW, status=lambda u: 410))
    _check("skipped with its reason", summary.endswith("the posting is closed") and not files and not web.queries,
           summary)
    _check("and leaves the queue", briefs._load_queue() == [])
    tmp.cleanup()


def _limit() -> openai.RateLimitError:
    response = httpx.Response(429, request=httpx.Request("POST", "https://api.groq.com/x"))
    return openai.RateLimitError("Rate limit reached", response=response, body=None)


def test_tick_runs_the_brief_first_and_the_digest_sends_it() -> None:
    briefs, tmp = _fresh()
    from agents import overnight
    import goals
    goals.add("Study something", "research")
    briefs.queue([JOB], "1 APPLY", now=NOW)
    web = _Web()
    original = briefs._status
    briefs._status = _open
    try:
        result = _run(overnight.tick(NIGHT_TIME, search=web.search, complete=web.complete))
    finally:
        briefs._status = original
    _check("the brief ran instead of the goal", result.startswith("role_brief: brief written"), result)
    text, files = overnight.digest_section(NIGHT)
    _check("listed in the digest", "Role brief:" in text and "Quillfield" in text, text)
    _check("and sent as a file", len(files) == 1 and files[0].name.startswith(NIGHT), str(files))
    _check("a quiet queue leaves the night to the goals",
           not _run(overnight.tick(NIGHT_TIME, search=web.search, complete=web.complete)).startswith("role_brief"))
    tmp.cleanup()


def test_rate_limit_keeps_the_posting_for_one_more_try() -> None:
    briefs, tmp = _fresh()
    from agents import overnight
    briefs.queue([JOB], "1 APPLY", now=NOW)

    async def capped(system, user, max_tokens, cap=None):
        raise _limit()
    web = _Web()
    original = briefs._status
    briefs._status = _open
    try:
        result = _run(overnight.tick(NIGHT_TIME, search=web.search, complete=capped))
        queued = briefs._load_queue()
        _check("stopped, not crashed", result == "rate limited", result)
        _check("still queued with one attempt", len(queued) == 1 and queued[0]["attempts"] == 1, str(queued))
        _run(overnight.tick(NIGHT_TIME, search=web.search, complete=capped))
        _check("let go after the second", briefs.next_due(NIGHT, NIGHT_TIME) is None)
    finally:
        briefs._status = original
    tmp.cleanup()


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            print(name)
            fn()
    if _failures:
        print(f"\n{len(_failures)} FAILED: {', '.join(_failures)}")
        sys.exit(1)
    print("\nall pass")
