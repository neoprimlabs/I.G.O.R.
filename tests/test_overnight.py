"""Tests for the overnight runner and research sessions: agents/overnight.py.

Run: python tests/test_overnight.py   (repo root, stdlib only, no API calls)
"""

import asyncio
import sys
import tempfile
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import httpx
import openai

import clock
import config

_failures: list[str] = []
NIGHT_TIME = datetime(2026, 10, 17, 2, 0, tzinfo=clock.USER_TZ)
NIGHT = "2026-10-17"
DAILY = ("Rate limit reached for model `openai/gpt-oss-20b` on tokens per day (TPD): "
         "Limit 200000, Used 199900, Requested 3530. Please try again in 23m3.264s.")


def _check(name: str, condition: bool, detail: str = "") -> None:
    if condition:
        print(f"  pass  {name}")
    else:
        print(f"  FAIL  {name}  {detail}")
        _failures.append(name)


def _limit(text: str) -> openai.RateLimitError:
    response = httpx.Response(429, request=httpx.Request("POST", "https://api.groq.com/x"))
    return openai.RateLimitError(text, response=response, body=None)


def _fresh():
    import goals
    import work
    from agents import evaluator, overnight
    tmp = tempfile.TemporaryDirectory()
    config.MEMORY_DIR = Path(tmp.name)
    calls = {"eval": 0}

    async def _pass(task, response, label="Evaluator", model=None):
        calls["eval"] += 1
        calls["label"] = label
        calls["model"] = model
        return calls.get("verdict", (True, ""))

    evaluator.evaluate = _pass
    return overnight, goals, work, tmp, calls


class _Stubs:
    def __init__(self, findings_per_step=5, unsure=True, links=3, raises=None):
        self.queries, self.systems, self.writes = [], [], 0
        self.findings_per_step, self.unsure, self.links, self.raises = (
            findings_per_step, unsure, links, raises)
        self.n = 0
        self.plan_users = []
        self.caps = []
        self.empty_write = False

    async def search(self, query, max_results=5):
        self.queries.append(query)
        return [{"title": "t", "url": "https://e.example/a", "content": "c"}]

    async def complete(self, system, user, max_tokens, cap=None):
        self.systems.append(system)
        self.caps.append((system[:20], max_tokens, cap))
        if self.raises:
            raise self.raises
        if system.startswith("You plan one step"):
            self.n += 1
            self.plan_users.append(user)
            return f"query number {self.n}"
        if system.startswith("You turn raw search results"):
            lines = [f"- fact {self.n}-{i} (https://e.example/{self.n}/{i})"
                     for i in range(self.findings_per_step)]
            return "\n".join(lines) + f"\nNext: thread {self.n}"
        self.writes += 1
        if self.empty_write:
            return ""
        body = "# Paper\n\n## Findings\n" + "".join(
            f"Claim {i} ([src](https://e.example/p{i})).\n" for i in range(self.links))
        if self.unsure:
            body += "\n## What I am unsure about\nMuch.\n"
        return body


def _first_id(goals) -> str:
    return goals.load()[0]["id"]


def _run(coro):
    return asyncio.run(coro)


def test_outside_window_costs_nothing() -> None:
    overnight, goals, work, tmp, _ = _fresh()
    goals.add("Study autonomous systems", "research")
    s = _Stubs()
    out = _run(overnight.tick(datetime(2026, 10, 17, 12, 0, tzinfo=clock.USER_TZ),
                              search=s.search, complete=s.complete))
    _check("no model call outside the window", s.systems == [] and s.queries == [], str(out))
    _check("nothing logged", work.spent("2026-10-17") == (0, 0))
    tmp.cleanup()


def test_research_appends_findings_and_moves_next_action() -> None:
    overnight, goals, work, tmp, _ = _fresh()
    goals.add("Study autonomous systems", "research")
    gid = _first_id(goals)
    s = _Stubs(findings_per_step=2)
    _run(overnight.tick(NIGHT_TIME, search=s.search, complete=s.complete))
    text = (work.folder(gid) / "findings.md").read_text(encoding="utf-8")
    _check("findings carry a session marker", "<!-- session 1 -->" in text, text)
    _check("findings are appended", "fact 1-0" in text and "fact 3-1" in text, text)
    _check("three searches ran", len(s.queries) == 3, str(s.queries))
    _check("next_action is the planner's last thread",
           goals.load()[0]["next_action"] == "thread 3", goals.load()[0]["next_action"])
    _check("search count logged", work.spent(NIGHT)[1] == 3, str(work.spent(NIGHT)))
    _check("below the trigger no paper is written", s.writes == 0)
    tmp.cleanup()


def test_paper_written_after_trigger() -> None:
    overnight, goals, work, tmp, calls = _fresh()
    goals.add("Study autonomous systems", "research")
    gid = _first_id(goals)
    s = _Stubs(findings_per_step=5)
    _run(overnight.tick(NIGHT_TIME, search=s.search, complete=s.complete))
    folder = work.folder(gid)
    _check("one write call", s.writes == 1)
    _check("finished paper saved", (folder / f"paper-{NIGHT}.md").exists(),
           str(sorted(p.name for p in folder.iterdir())))
    _check("evaluator ran under the Overnight label",
           calls["eval"] == 1 and calls["label"] == "Overnight")
    _check("paper is listed as finished in the log",
           f"paper-{NIGHT}.md" in " ".join(work._load()[-1]["finished"]))
    tmp.cleanup()


def test_paper_without_unsure_section_stays_draft() -> None:
    overnight, goals, work, tmp, calls = _fresh()
    goals.add("Study autonomous systems", "research")
    gid = _first_id(goals)
    s = _Stubs(findings_per_step=5, unsure=False)
    _run(overnight.tick(NIGHT_TIME, search=s.search, complete=s.complete))
    folder = work.folder(gid)
    _check("saved as a draft", (folder / f"draft-{NIGHT}.md").exists())
    _check("no finished paper", not (folder / f"paper-{NIGHT}.md").exists())
    _check("the reason is in the log", "unsure" in work._load()[-1]["summary"].lower(),
           work._load()[-1]["summary"])
    _check("nothing finished", work._load()[-1]["finished"] == [])
    _check("evaluator skipped for a draft", calls["eval"] == 0)
    ok, why = overnight.paper_is_finished("x\n## What I am unsure about\n" +
                                          " ".join(f"https://a.example/{i}" for i in range(2)))
    _check("two links is not finished", not ok and "link" in why, why)
    ok, _ = overnight.paper_is_finished("x\n## What I am unsure about\n" +
                                        " ".join(f"https://a.example/{i}" for i in range(3)))
    _check("three distinct links and the section is finished", ok)
    ok, _ = overnight.paper_is_finished("x\n## What I am unsure about\n" + "https://a.example/1 " * 5)
    _check("repeated links count once", not ok)
    tmp.cleanup()


def test_budget_stops_the_night() -> None:
    overnight, goals, work, tmp, _ = _fresh()
    goals.add("Study autonomous systems", "research")
    work.log_session("g", "research", "s", config.OVERNIGHT_TOKENS, 1, [], [], now=NIGHT_TIME)
    s = _Stubs()
    out = _run(overnight.tick(NIGHT_TIME, search=s.search, complete=s.complete))
    _check("no call at the token cap", s.systems == [] and s.queries == [], str(out))
    work.log_session("g", "research", "s", 0, 0, [], [], stopped="rate limited", now=NIGHT_TIME)
    overnight2_out = _run(overnight.tick(NIGHT_TIME, search=s.search, complete=s.complete))
    _check("stopped night costs nothing", s.systems == [] and bool(work.stopped(NIGHT)),
           overnight2_out)
    tmp.cleanup()


def test_search_cap() -> None:
    overnight, goals, work, tmp, _ = _fresh()
    goals.add("Study autonomous systems", "research")
    work.log_session("g", "research", "s", 10, config.OVERNIGHT_SEARCHES - 1, [], [], now=NIGHT_TIME)
    s = _Stubs(findings_per_step=1)
    _run(overnight.tick(NIGHT_TIME, search=s.search, complete=s.complete))
    _check("only the searches left are used", len(s.queries) == 1, str(s.queries))
    s2 = _Stubs()
    _run(overnight.tick(NIGHT_TIME, search=s2.search, complete=s2.complete))
    _check("at the cap there is no call", s2.systems == [] and s2.queries == [])
    tmp.cleanup()


def test_daily_cap_429_stops_and_records_limit() -> None:
    overnight, goals, work, tmp, _ = _fresh()
    goals.add("Study autonomous systems", "research")
    s = _Stubs(raises=_limit(DAILY))
    out = _run(overnight.tick(NIGHT_TIME, search=s.search, complete=s.complete))
    _check("stopped records the limit", work.stopped(NIGHT) == "daily cap 20b: 200000",
           work.stopped(NIGHT) + " / " + out)
    n = len(s.systems)
    _run(overnight.tick(NIGHT_TIME, search=s.search, complete=s.complete))
    _check("the next tick makes no call", len(s.systems) == n)
    tmp.cleanup()

    overnight, goals, work, tmp, _ = _fresh()
    goals.add("Study autonomous systems", "research")
    s = _Stubs(raises=_limit("Rate limit on 20b. Please try again in 40s."))
    _run(overnight.tick(NIGHT_TIME, search=s.search, complete=s.complete))
    _check("a per-minute limit is recorded as rate limited",
           work.stopped(NIGHT) == "rate limited", work.stopped(NIGHT))
    tmp.cleanup()


def test_restart_resumes() -> None:
    overnight, goals, work, tmp, _ = _fresh()
    goals.add("Study autonomous systems", "research")
    gid = _first_id(goals)
    s = _Stubs(findings_per_step=1)
    _run(overnight.tick(NIGHT_TIME, search=s.search, complete=s.complete))
    first = (work.folder(gid) / "findings.md").read_text(encoding="utf-8")
    later = datetime(2026, 10, 17, 3, 0, tzinfo=clock.USER_TZ)
    _run(overnight.tick(later, search=s.search, complete=s.complete))
    second = (work.folder(gid) / "findings.md").read_text(encoding="utf-8")
    _check("the second PLAN prompt carries the stored next_action",
           "Suggested next thread: thread 3" in s.plan_users[3], s.plan_users[3][:300])
    _check("the first session had no hint", "Suggested next thread" not in s.plan_users[0])
    _check("the second tick continues the file", second.startswith(first))
    _check("with the next marker number",
           second.count("<!-- session 1 -->") == 1 and "<!-- session 2 -->" in second, second)
    tmp.cleanup()


def test_token_room_stops_loop_and_gates_write() -> None:
    overnight, goals, work, tmp, _ = _fresh()
    goals.add("Study autonomous systems", "research")
    used = {"n": 0}
    work.overnight_tokens = lambda: used["n"]

    s = _Stubs(findings_per_step=1)
    real = s.complete

    async def spending(system, user, max_tokens):
        used["n"] += 25000
        return await real(system, user, max_tokens)

    _run(overnight.tick(NIGHT_TIME, search=s.search, complete=spending))
    _check("the loop stops once the room is used", len(s.queries) == 2, str(len(s.queries)))
    tmp.cleanup()

    overnight, goals, work, tmp, _ = _fresh()
    goals.add("Study autonomous systems", "research")
    used = {"n": 0}
    work.overnight_tokens = lambda: used["n"]
    s = _Stubs(findings_per_step=7)
    real = s.complete

    async def spending2(system, user, max_tokens):
        used["n"] += 18000
        return await real(system, user, max_tokens)

    _run(overnight.tick(NIGHT_TIME, search=s.search, complete=spending2))
    _check("a triggered write is skipped when there is no room", s.writes == 0, str(s.writes))
    _check("the log says it waits", "waits for budget" in work._load()[-1]["summary"],
           work._load()[-1]["summary"])
    work.overnight_tokens = lambda: 0
    later = datetime(2026, 10, 18, 1, 0, tzinfo=clock.USER_TZ)
    _run(overnight.tick(later, search=s.search, complete=s.complete))
    _check("the trigger stays pending and writes later", s.writes == 1, str(s.writes))
    tmp.cleanup()


def test_empty_write_retries_once_per_night() -> None:
    overnight, goals, work, tmp, _ = _fresh()
    goals.add("Study autonomous systems", "research")
    s = _Stubs(findings_per_step=5)
    s.empty_write = True
    _run(overnight.tick(NIGHT_TIME, search=s.search, complete=s.complete))
    _run(overnight.tick(datetime(2026, 10, 17, 3, 0, tzinfo=clock.USER_TZ),
                        search=s.search, complete=s.complete))
    _check("only one write call tonight", s.writes == 1, str(s.writes))
    _run(overnight.tick(datetime(2026, 10, 18, 1, 0, tzinfo=clock.USER_TZ),
                        search=s.search, complete=s.complete))
    _check("it tries again the next night", s.writes == 2, str(s.writes))
    tmp.cleanup()


def test_tick_never_raises() -> None:
    overnight, goals, work, tmp, _ = _fresh()
    goals.add("Study autonomous systems", "research")
    s = _Stubs(raises=RuntimeError("boom"))
    try:
        out = _run(overnight.tick(NIGHT_TIME, search=s.search, complete=s.complete))
        raised = False
    except Exception as e:
        raised, out = True, str(e)
    _check("a RuntimeError does not escape", not raised, out)

    async def bad_search(query, max_results=5):
        raise RuntimeError("exa down")

    s2 = _Stubs()
    try:
        _run(overnight.tick(NIGHT_TIME, search=bad_search, complete=s2.complete))
        raised = False
    except Exception:
        raised = True
    _check("a failing search does not escape", not raised)
    tmp.cleanup()


def test_findings_rotate_after_finished_paper() -> None:
    overnight, goals, work, tmp, _ = _fresh()
    goals.add("Study autonomous systems", "research")
    gid = _first_id(goals)
    s = _Stubs(findings_per_step=5)
    _run(overnight.tick(NIGHT_TIME, search=s.search, complete=s.complete))
    folder = work.folder(gid)
    _check("findings moved aside", (folder / f"findings-{NIGHT}.md").exists())
    fresh = (folder / "findings.md")
    _check("a fresh working file starts", (not fresh.exists()) or "fact" not in fresh.read_text(encoding="utf-8"))
    kept = (folder / f"findings-{NIGHT}.md").read_text(encoding="utf-8")
    _check("the source material is kept", "fact 1-0" in kept)
    tmp.cleanup()


def test_self_improve_without_input_makes_no_call() -> None:
    overnight, goals, work, tmp, _ = _fresh()
    goals.add("Review recent mistakes", "self_improve")
    s = _Stubs()
    _run(overnight.tick(NIGHT_TIME, search=s.search, complete=s.complete))
    _check("no model call", s.systems == [] and s.queries == [])
    _check("the session is logged", "no new" in work._load()[-1]["summary"], str(work._load()))
    tmp.cleanup()


def test_prompts_carry_the_style_block() -> None:
    from agents import overnight
    block = ("Style:\n- No emojis\n- No em dashes - use plain hyphens\n"
             "- No exclamation points\n"
             "- No casual filler phrases (\"Sure!\", \"Of course!\", \"Happy to help!\")")
    _check("write prompt has the style block", block in overnight._WRITE_SYSTEM)
    _check("write prompt has no em dash", "—" not in overnight._WRITE_SYSTEM)


def _finished_paper(work, goals, name: str, title: str) -> str:
    gid = _first_id(goals)
    folder = work.folder(gid)
    folder.mkdir(parents=True, exist_ok=True)
    (folder / name).write_text(f"# {title}\n\nBody.\n", encoding="utf-8")
    return str((folder / name).relative_to(config.MEMORY_DIR))


def test_quiet_night_section_empty() -> None:
    overnight, goals, work, tmp, _ = _fresh()
    goals.add("Study autonomous systems", "research")
    _check("no sessions gives nothing", overnight.digest_section(NIGHT) == ("", []))
    work.log_session("g", "research", "0 new findings", 500, 3, [], [], now=NIGHT_TIME)
    _check("a session that produced nothing gives nothing",
           overnight.digest_section(NIGHT) == ("", []))
    _check("another night is not mixed in",
           overnight.digest_section("2026-10-16") == ("", []))
    tmp.cleanup()


def test_section_lists_finished_and_waiting() -> None:
    import proposals
    overnight, goals, work, tmp, _ = _fresh()
    goals.add("Study autonomous systems", "research")
    rel = _finished_paper(work, goals, f"paper-{NIGHT}.md", "Edge Agents Compared")
    gid = _first_id(goals)
    draft = work.folder(gid) / f"draft-{NIGHT}.md"
    draft.write_text("# Half\n", encoding="utf-8")
    work.log_session(gid, "research", "paper finished", 4200, 3,
                     [rel], [rel], now=NIGHT_TIME)
    work.log_session(gid, "research", "draft kept", 1800, 2,
                     [str(draft.relative_to(config.MEMORY_DIR))], [],
                     stopped="rate limited", now=NIGHT_TIME)
    proposals.add("preference", "p", "e", "Leave out tables.", "t")
    text, papers = overnight.digest_section(NIGHT)
    _check("the paper title is listed", "Edge Agents Compared" in text, text)
    _check("the paper is returned for attaching",
           [p.name for p in papers] == [f"paper-{NIGHT}.md"], str(papers))
    _check("the draft is listed", f"draft-{NIGHT}.md" in text, text)
    _check("waiting proposals are counted", "1 proposal" in text, text)
    _check("tokens and searches are shown", "6000" in text and "5" in text, text)
    _check("the stopped reason is shown", "rate limited" in text, text)
    _check("no em dash", "—" not in text)
    tmp.cleanup()


def test_digest_attaches_finished_papers() -> None:
    from agents import monitor
    overnight, goals, work, tmp, _ = _fresh()
    goals.add("Study autonomous systems", "research")
    rel = _finished_paper(work, goals, f"paper-{NIGHT}.md", "Edge Agents Compared")
    work.log_session(_first_id(goals), "research", "paper finished", 100, 1,
                     [rel], [rel, rel], now=NIGHT_TIME)
    (config.MEMORY_DIR / "digest_config.md").write_text("- overnight\n", encoding="utf-8")
    sent, files = [], []

    async def send(text):
        sent.append(text)

    async def send_file(text):
        files.append(text)

    morning = datetime(2026, 10, 17, 9, 0, tzinfo=clock.USER_TZ)
    monitor._send_fn, monitor._send_file_fn = send, send_file
    _run(monitor._morning_digest(now=morning))
    _check("the digest carries an overnight section",
           len(sent) == 1 and "Overnight" in sent[0] and "Edge Agents Compared" in sent[0],
           str(sent))
    _check("each paper is sent once", len(files) == 1 and files[0].startswith("# Edge Agents"),
           str(files))

    sent.clear(); files.clear()
    monitor._send_file_fn = None
    _run(monitor._morning_digest(now=morning))
    _check("without a file sender the paper is listed by name",
           files == [] and f"paper-{NIGHT}.md" in sent[0], str(sent))

    sent.clear(); files.clear()
    monitor._send_file_fn = send_file
    _run(monitor._morning_digest(now=datetime(2026, 10, 18, 9, 0, tzinfo=clock.USER_TZ)))
    _check("a quiet night adds no section and attaches nothing",
           files == [] and "Overnight" not in sent[0], str(sent))
    monitor._send_fn = None
    tmp.cleanup()


def test_failing_session_does_not_wedge_rotation() -> None:
    overnight, goals, work, tmp, _ = _fresh()
    goals.add("Alpha topic", "research")
    goals.add("Beta topic", "research")
    s = _Stubs()
    real = s.complete

    async def flaky(system, user, max_tokens, cap=None):
        if "Alpha topic" in user:
            raise RuntimeError("boom")
        return await real(system, user, max_tokens, cap)

    out1 = _run(overnight.tick(NIGHT_TIME, search=s.search, complete=flaky))
    later = datetime(2026, 10, 17, 3, 0, tzinfo=clock.USER_TZ)
    out2 = _run(overnight.tick(later, search=s.search, complete=flaky))
    _check("first tick fails on the first goal", out1.startswith("error"), out1)
    _check("the next tick works the other goal", out2.startswith("g2:"), out2)
    tmp.cleanup()


def test_overnight_model_is_one_pinned_name() -> None:
    import asyncio as _a
    from agents import evaluator, research_loop
    import llm
    overnight, goals, work, tmp, calls = _fresh()
    _check("not a fallback target",
           config.OVERNIGHT_MODEL not in config.FALLBACK_MODELS)
    _check("not the chat model", config.OVERNIGHT_MODEL != config.MODELS["chat"])
    seen = {}

    async def fake_complete(client, model, *a, **k):
        seen["model"] = model
        return "x"

    real_c, real_cl = llm.complete, research_loop._get_client
    llm.complete, research_loop._get_client = fake_complete, lambda: None
    try:
        _a.run(overnight._real_complete()("s", "u", 1024))
    finally:
        llm.complete, research_loop._get_client = real_c, real_cl
    _check("_real_complete targets it", seen.get("model") == config.OVERNIGHT_MODEL, str(seen))

    goals.add("Study autonomous systems", "research")
    _run(overnight.tick(NIGHT_TIME, search=_Stubs().search, complete=_Stubs(findings_per_step=5).complete))
    _check("the evaluator call targets it", calls.get("model") == config.OVERNIGHT_MODEL,
           str(calls))
    import usage
    real_u = usage.last_24h
    usage.last_24h = lambda: {config.OVERNIGHT_MODEL: {"Overnight": 77}}
    try:
        _check("overnight_tokens reads that model", work.overnight_tokens() == 77)
    finally:
        usage.last_24h = real_u
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
