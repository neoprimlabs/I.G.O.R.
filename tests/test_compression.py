"""compression.py: a running summary of what scrolled out of the context window.

Run: python tests/test_compression.py   (repo root, stdlib only, no API calls)
"""

import asyncio
import sys
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import config
import compression
import context_store

_failures: list[str] = []
NOW = datetime(2026, 10, 19, 18, 0, tzinfo=timezone.utc)


def _check(name: str, condition: bool, detail: str = "") -> None:
    if condition:
        print(f"  pass  {name}")
    else:
        print(f"  FAIL  {name}  {detail}")
        _failures.append(name)


class _Store:
    def __enter__(self):
        self.tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.real = (config.MEMORY_DIR, context_store._DB_PATH)
        config.MEMORY_DIR = Path(self.tmp.name)
        context_store._DB_PATH = config.MEMORY_DIR / "context.db"
        return self

    def __exit__(self, *exc):
        config.MEMORY_DIR, context_store._DB_PATH = self.real
        self.tmp.cleanup()


def _say(n: int, start: int = 0) -> None:
    for i in range(start, start + n):
        context_store.append("user" if i % 2 == 0 else "assistant", f"message {i}")


class _Model:
    def __init__(self, replies):
        self.replies, self.users = list(replies), []

    async def __call__(self, system, user):
        self.users.append(user)
        reply = self.replies.pop(0) if self.replies else "## Working on\n- something"
        if isinstance(reply, Exception):
            raise reply
        return reply


def _update(model, now=NOW):
    return asyncio.run(compression.update(complete=model, now=now))


def test_only_evicted_messages_are_summarised() -> None:
    with _Store():
        _say(config.CONTEXT_WINDOW + 2)
        model = _Model([])
        _check("fewer than a batch out of the window: no call",
               _update(model) == "nothing to do" and not model.users)
        _say(2, start=config.CONTEXT_WINDOW + 2)
        result = _update(model)
        _check("a full batch out of the window is summarised", result == "summarised 4", result)
        prompt = model.users[-1]
        _check("the messages still in the window are left out",
               f"message {config.CONTEXT_WINDOW + 3}" not in prompt
               and f"message {3}" in prompt, prompt)
        _check("and the summary is what the next prompt sees", "something" in compression.text())


def test_updates_build_on_the_last_summary() -> None:
    with _Store():
        _say(config.CONTEXT_WINDOW + 4)
        model = _Model(["## Open\n- reply to the dentist"])
        _update(model)
        _say(4, start=config.CONTEXT_WINDOW + 4)
        _update(model)
        _check("the second pass is handed the first summary to update",
               "reply to the dentist" in model.users[-1] and "Summary so far" in model.users[-1],
               model.users[-1][:200])
        _check("and nothing is summarised twice",
               "message 0" not in model.users[-1], model.users[-1])


def test_failure_cools_down_and_keeps_the_messages() -> None:
    with _Store():
        _say(config.CONTEXT_WINDOW + 4)
        model = _Model([RuntimeError("429"), RuntimeError("429")])
        _check("a failed call is reported", _update(model) == "failed")
        _check("the next attempt within 60s waits", _update(model, NOW + timedelta(seconds=30))
               == "cooling down" and len(model.users) == 1)
        _update(model, NOW + timedelta(seconds=61))
        state = compression._state()
        _check("a second failure waits longer, as Hermes does",
               datetime.fromisoformat(state["retry_after"]) == NOW + timedelta(seconds=361),
               str(state))
        result = _update(model, NOW + timedelta(seconds=400))
        _check("and once it works the same messages are summarised",
               result == "summarised 4" and "message 3" in model.users[-1], result)
        _check("the cooldown is cleared", "retry_after" not in compression._state())


def test_empty_summary_is_a_failure() -> None:
    with _Store():
        _say(config.CONTEXT_WINDOW + 4)
        _check("an empty reply does not replace the summary", _update(_Model(["  "])) == "failed")
        _check("and nothing was saved as the summary", compression.text() == "")


def test_caps() -> None:
    with _Store():
        context_store.append("user", "x" * 5000)
        context_store.append("assistant", "[sent proactively]\n" + "y" * 5000)
        _say(config.CONTEXT_WINDOW + 2)
        long = "\n".join(f"- line {i} " + "z" * 60 for i in range(60))
        model = _Model([long])
        _update(model)
        prompt = model.users[-1]
        _check("a user message is cut at 1500 characters", "x" * 1500 in prompt and "x" * 1501 not in prompt)
        _check("a proactive post at 300, marker included",
               "y" * 250 in prompt and "y" * 300 not in prompt)
        summary = compression.text()
        _check("an over-long summary is cut at a whole line, under the cap",
               len(summary) <= compression._MAX_CHARS and summary.endswith("z"), str(len(summary)))


def test_backlog_is_bounded() -> None:
    with _Store():
        _say(config.CONTEXT_WINDOW + 4)
        _update(_Model([]))
        _say(30, start=config.CONTEXT_WINDOW + 4)
        model = _Model([])
        result = _update(model)
        _check("a long backlog sends only the newest 12", result == "summarised 12", result)
        _check("and says how many were skipped", "were not summarised" in model.users[-1])


def test_window_carries_the_summary() -> None:
    import orchestrator
    with _Store():
        compression._save({"summary": "## Open\n- " + "q" * 3000, "through_id": 1})

        async def notify(_):
            pass

        o = orchestrator.Orchestrator(notify)
        o._context = [{"role": "user", "content": "hello"}, {"role": "assistant", "content": "hi"}]
        window = o._window(7000)
        _check("the summary comes first, as a record and not instructions",
               window[0]["role"] == "system" and "not instructions" in window[0]["content"],
               str(window[0])[:120])
        _check("capped at a fifth of the budget", len(window[0]["content"]) < 7000 // 5 + 200,
               str(len(window[0]["content"])))
        _check("the recent turns follow it whole", window[1:] == o._context, str(window[1:]))
        _check("SelfDescribe does not get it",
               o._window(1500, with_summary=False) == o._context)


def test_window_says_when_messages_were_sent() -> None:
    """2026-10-21: "I'm at my retail job today", said the day before, read as today."""
    import orchestrator
    with _Store():
        async def notify(_):
            pass

        o = orchestrator.Orchestrator(notify)
        tue = datetime(2026, 10, 20, 18, 30, tzinfo=timezone.utc).timestamp()   # 14:30 EDT
        wed = datetime(2026, 10, 21, 13, 0, tzinfo=timezone.utc).timestamp()    # 09:00 EDT
        o._context = [{"role": "user", "content": "I'm at my retail job today", "ts": tue},
                      {"role": "assistant", "content": "Noted.", "ts": tue},
                      {"role": "assistant", "content": "[sent proactively]\nMorning Digest", "ts": wed}]
        window = o._window(7000)
        _check("the user's message carries its weekday and time",
               window[1]["content"] == "[Tue 10-20 14:30] I'm at my retail job today", window[1]["content"])
        _check("I.G.O.R.'s own reply is left as it was, so it is not copied into replies",
               window[2]["content"] == "Noted.", window[2]["content"])
        _check("an unprompted post says when it went out",
               window[3]["content"].startswith("[sent proactively Wed 10-21 09:00]"), window[3]["content"])
        _check("a note first says how to read them",
               window[0]["role"] == "system" and "from the day it was sent" in window[0]["content"])
        _check("nothing but role and content reaches the API",
               all(set(m) == {"role", "content"} for m in window), str(window))
        compression._save({"summary": "## Open\n- something", "through_id": 1})
        both = o._window(7000)[0]["content"]
        _check("with a summary, the note and the summary share one system message",
               "from the day it was sent" in both and "not instructions" in both, both[:200])


def test_posts_do_not_push_out_the_users_words() -> None:
    """2026-10-21: a morning's posts filled chat's six-message window."""
    import orchestrator
    with _Store():
        async def notify(_):
            pass

        o = orchestrator.Orchestrator(notify)
        ctx = []
        for i in range(4):
            ctx.append({"role": "user", "content": f"question {i}"})
            ctx.append({"role": "assistant", "content": f"answer {i}"})
        for post in ("digest", "idea", "jobs", "gigs", "check-in"):
            ctx.append({"role": "assistant", "content": f"[sent proactively]\n{post} " + "x" * 400})
        o._context = ctx
        window = o._window(7000)
        texts = [m["content"] for m in window]
        _check("the last six conversation messages are all there behind five posts",
               all(f"question {i}" in texts for i in (1, 2, 3)) and "answer 3" in texts
               and "question 0" not in texts, str(texts))
        _check("with the three newest posts, in the order things happened",
               [t.split()[0] for t in texts if t.startswith("[sent")] == ["[sent"] * 3
               and "gigs" in texts[-2] and "check-in" in texts[-1] and "digest" not in " ".join(texts),
               str([t[:30] for t in texts]))
        tight = o._window(160)
        tight_texts = [m["content"] for m in tight]
        _check("on a tight budget the user's words stay whole and the posts are what gets cut",
               all(f"question {i}" in tight_texts and f"answer {i}" in tight_texts for i in (1, 2, 3))
               and all("[truncated]" in t for t in tight_texts if t.startswith("[sent"))
               and sum(t.startswith("[sent") for t in tight_texts) <= 1,
               str([t[:40] for t in tight_texts]))


def test_no_summary_changes_nothing() -> None:
    import orchestrator
    with _Store():
        async def notify(_):
            pass

        o = orchestrator.Orchestrator(notify)
        o._context = [{"role": "user", "content": "hello"}]
        _check("with no summary the window is the recent turns only", o._window() == o._context)


def test_schedule_outside_a_loop_is_harmless() -> None:
    compression.schedule()
    _check("schedule() with no running loop does nothing", True)


def test_prompt_rules() -> None:
    _check("the prompt carries the style block", "No em dashes" in compression._SYSTEM)
    _check("and treats fetched or pasted text as content", "not instructions" in compression._SYSTEM)
    _check("days are written out, so the summary stays true on later days",
           '"Wed Oct 21", never "today", "tomorrow"' in compression._SYSTEM)
    import clock
    line = compression._line((1, "user", "tomorrow", int(NOW.timestamp())), clock.USER_TZ)
    _check("each input line starts with its weekday and date", line.startswith("[Mon 2026-10-19"), line)


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            print(name)
            fn()
    if _failures:
        print(f"\n{len(_failures)} FAILED: {', '.join(_failures)}")
        sys.exit(1)
    print("\nall pass")
