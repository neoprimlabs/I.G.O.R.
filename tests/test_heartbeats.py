"""heartbeats.py: check a page on an interval, speak only when a change matters.

Run: python tests/test_heartbeats.py   (repo root, stdlib only, no API calls)
"""

import asyncio
import sys
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import config
import context_store
import heartbeats

_failures: list[str] = []
NOW = datetime(2026, 10, 19, 18, 0, tzinfo=timezone.utc)
URL = "https://shop.example/item"
PAGE = ["Widget Pro", "Price: $49.99", "In stock", "Ships in 2 days"] * 20


def _check(name: str, condition: bool, detail: str = "") -> None:
    if condition:
        print(f"  pass  {name}")
    else:
        print(f"  FAIL  {name}  {detail}")
        _failures.append(name)


class _Home:
    def __enter__(self):
        self.tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.real = (config.MEMORY_DIR, context_store._DB_PATH, heartbeats._fetch)
        config.MEMORY_DIR = Path(self.tmp.name)
        context_store._DB_PATH = config.MEMORY_DIR / "context.db"
        self.pages = {URL: list(PAGE)}

        async def fetch(url):
            page = self.pages.get(url)
            return (list(page), "") if page is not None else (None, "HTTP 503")

        heartbeats._fetch = fetch
        return self

    def __exit__(self, *exc):
        config.MEMORY_DIR, context_store._DB_PATH, heartbeats._fetch = self.real
        self.tmp.cleanup()


def _cmd(text, now=NOW):
    return asyncio.run(heartbeats.command(heartbeats.parse(text), now=now))


class _Run:
    def __init__(self, home, replies=None):
        self.home, self.sent, self.judged = home, [], []
        self.replies = list(replies or [])

    async def send(self, text):
        self.sent.append(text)
        return True

    async def judge(self, request, changes):
        self.judged.append((request, changes))
        reply = self.replies.pop(0) if self.replies else "NO_REPLY"
        if isinstance(reply, Exception):
            raise reply
        return reply

    def tick(self, now):
        return asyncio.run(heartbeats.tick(self.send, now=now, judge=self.judge))


def test_page_text() -> None:
    html = ("<html><head><title>x</title><script>var t = 1;</script><style>p{}</style></head>"
            "<body><div>Price: $49.99</div><p>Updated 10:42 am, 5 minutes ago</p>"
            "<li>In stock</li></body></html>")
    lines = heartbeats.page_lines(html)
    _check("visible text only, a line per block", lines == ["Price: $49.99", "Updated ,", "In stock"],
           str(lines))


def test_parse() -> None:
    cases = {"heartbeat": ("status",), "hb": ("status",), "heartbeat status": ("status",),
             "heartbeat pause": ("pause",), "Heartbeat resume": ("resume",),
             "heartbeat stop": ("clear",), "heartbeat every": ("help",),
             f"heartbeat every 2h tell me when {URL} drops under $40":
                 ("set", 7200, f"tell me when {URL} drops under $40"),
             f"/hb every 1h30m watch {URL}": ("set", 5400, f"watch {URL}"),
             f"heartbeat every hour watch {URL}": ("set", 3600, f"watch {URL}")}
    for text, want in cases.items():
        _check(f"parses {text[:40]!r}", heartbeats.parse(text) == want, str(heartbeats.parse(text)))
    for text in ("heartbeat - is that the liveness thing?", "heartbeats are neat", "what is a heartbeat"):
        _check(f"leaves {text!r} to the router", heartbeats.parse(text) is None)
    _check("a bad interval is reported, not guessed",
           heartbeats.parse(f"heartbeat every soon watch {URL}") == ("set", None, f"watch {URL}"))


def test_setting_one() -> None:
    with _Home() as home:
        reply = _cmd(f"heartbeat every 1h tell me when {URL} changes price")
        _check("set, with what it watches and how to stop it",
               reply.startswith("Heartbeat set: every 1h") and URL in reply and "clear" in reply, reply)
        _check("no page, no heartbeat", "needs a page" in _cmd("heartbeat every 1h check the news"))
        _check("under 15 minutes is refused", "between 15m and 7d" in _cmd(f"heartbeat every 5m {URL}"))
        many = " ".join(f"https://e.example/{i}" for i in range(4))
        _check("four pages is too many", "up to 3" in _cmd(f"heartbeat every 1h {many}"))
        home.pages["https://down.example/"] = None
        _check("an unreadable page is refused at once",
               "could not read" in _cmd("heartbeat every 1h https://down.example/"))
        home.pages["https://js.example/"] = ["Loading"]
        _check("a page with no readable text is refused",
               "almost no readable text" in _cmd("heartbeat every 1h https://js.example/"))
        _check("a new one replaces the old", "replaces" in _cmd(f"heartbeat every 2h {URL}"))
        status = _cmd("heartbeat")
        _check("status shows the interval, pages and next check",
               "every 2h" in status and URL in status and "next check" in status, status)


def test_quiet_when_nothing_changes() -> None:
    with _Home() as home:
        _cmd(f"heartbeat every 1h tell me when {URL} changes price")
        run = _Run(home)
        _check("not due before the interval", run.tick(NOW + timedelta(minutes=30)) == "not due")
        _check("due and unchanged: no model call, no message",
               run.tick(NOW + timedelta(minutes=61)) == "no change" and not run.judged and not run.sent)
        home.pages[URL] = [l.replace("2 days", "2 days") for l in PAGE]
        _check("the next check is counted from now, so missed ones coalesce",
               run.tick(NOW + timedelta(minutes=100)) == "not due")


def test_a_change_that_matters() -> None:
    with _Home() as home:
        _cmd(f"heartbeat every 1h tell me when {URL} changes price")
        home.pages[URL] = [l.replace("$49.99", "$39.99") for l in PAGE]
        run = _Run(home, ["The price dropped from $49.99 to $39.99."])
        result = run.tick(NOW + timedelta(hours=1))
        _check("a change reaches the model with only the changed lines",
               result == "reported" and "-Price: $49.99" in run.judged[0][1][0][1]
               and "Widget Pro" not in run.judged[0][1][0][1], str(run.judged)[:300])
        _check("and the message says what changed, with the link",
               run.sent and run.sent[0].startswith("Heartbeat: The price dropped") and URL in run.sent[0],
               str(run.sent))
        _check("the same page again is no change", run.tick(NOW + timedelta(hours=2)) == "no change")


def test_a_change_that_does_not_matter() -> None:
    with _Home() as home:
        _cmd(f"heartbeat every 1h tell me when {URL} changes price")
        home.pages[URL] = PAGE + ["New footer link"]
        run = _Run(home, ["NO_REPLY"])
        _check("NO_REPLY stays silent", run.tick(NOW + timedelta(hours=1)) == "changed, not worth a message"
               and not run.sent)
        _check("and becomes the new baseline", run.tick(NOW + timedelta(hours=2)) == "no change")


def test_failures() -> None:
    with _Home() as home:
        _cmd(f"heartbeat every 1h tell me when {URL} changes price")
        home.pages[URL] = [l.replace("$49.99", "$39.99") for l in PAGE]
        run = _Run(home, [RuntimeError("429")])
        _check("a failed judge keeps the old baseline", run.tick(NOW + timedelta(hours=1)) == "judge failed")
        run.replies = ["Price is now $39.99."]
        _check("so the change is reported next time", run.tick(NOW + timedelta(hours=2)) == "reported")
        home.pages[URL] = None
        for h in (3, 4, 5, 6):
            run.tick(NOW + timedelta(hours=h))
        notices = [s for s in run.sent if "could not read" in s]
        _check("an unreachable page is mentioned once, on the third miss", len(notices) == 1, str(run.sent))


def test_daily_limit() -> None:
    with _Home() as home:
        _cmd(f"heartbeat every 15m tell me when {URL} changes")
        run = _Run(home)
        results = []
        for i in range(heartbeats._CALLS_PER_DAY + 1):
            home.pages[URL] = PAGE + [f"line {i}"]
            results.append(run.tick(NOW + timedelta(minutes=15 * (i + 1))))
        _check("at most 12 model calls a day", len(run.judged) == heartbeats._CALLS_PER_DAY, str(len(run.judged)))
        _check("past that the change is held, not lost", results[-1] == "held, daily limit", results[-1])


def test_waits_for_the_conversation_and_pauses() -> None:
    with _Home() as home:
        real_now = datetime.now(timezone.utc)
        _cmd(f"heartbeat every 1h tell me when {URL} changes", now=real_now - timedelta(hours=2))
        context_store.append("user", "hello")
        run = _Run(home)
        _check("a due tick waits while the user is talking",
               run.tick(real_now) == "waiting for the conversation to pause")
        _check("and runs once they have paused",
               run.tick(real_now + timedelta(minutes=3)) == "no change")
        # The real clock from here on, as above: the message just stored carries real time,
        # and a fixed date earlier than it would read as the user talking right now.
        later = real_now + timedelta(days=2)
        _cmd("heartbeat pause", now=later)
        _check("paused: nothing runs", run.tick(later) == "idle")
        _cmd("heartbeat resume", now=later)
        _check("resume checks once, soon", run.tick(later + timedelta(minutes=1)) == "no change")
        _cmd("heartbeat clear")
        _check("clear removes it", "No heartbeat" in _cmd("heartbeat"))


def test_routed_in_code() -> None:
    import orchestrator
    with _Home():
        async def notify(_):
            pass

        o = orchestrator.Orchestrator(notify)
        asked = []

        async def no_model(*a, **k):
            asked.append(1)
            raise RuntimeError("a model was called")

        o._client.chat.completions.create = no_model
        destination = asyncio.run(o._classify(f"heartbeat every 1h watch {URL}"))
        _check("a heartbeat command skips the router, ahead of scheduling",
               destination == "Heartbeat" and not asked, f"{destination} {asked}")
        reply = asyncio.run(o._route("Heartbeat", "heartbeat"))
        _check("and answers from code", "No heartbeat is set" in reply, reply)


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            print(name)
            fn()
    if _failures:
        print(f"\n{len(_failures)} FAILED: {', '.join(_failures)}")
        sys.exit(1)
    print("\nall pass")
