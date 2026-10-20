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


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            print(name)
            fn()
    if _failures:
        print(f"\n{len(_failures)} FAILED: {', '.join(_failures)}")
        sys.exit(1)
    print("\nall pass")
