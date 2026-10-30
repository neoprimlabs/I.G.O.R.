"""Secrets are scrubbed from tool output, outbound text and logs; scheduled jobs can stay silent.

Run: python tests/test_redact.py   (repo root, stdlib only, no API calls)

Hermes' security.redact_secrets is on by default. IGOR hid its keys from the sandbox but had
nothing behind that: a key in a page, a file or a log line went straight to the model, a memory
file or Discord. Hermes' cron [SILENT] lets a job report only when there is something to report.
"""

import asyncio
import json
import logging
import os
import sys
import tempfile
from datetime import datetime, timedelta, timezone
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


# Invented. Assembled here so the file itself holds no string a scanner would flag.
GROQ = "gsk_" + "a1B2c3D4" * 4
GITHUB = "ghp_" + "Z9y8X7w6" * 4
OPENAI = "sk-" + "proj" + "Q1w2E3r4" * 4
AWS = "AKIA" + "ABCDEFGHIJKLMNOP"
DISCORD_TOKEN = "M" + "T" * 23 + "." + "AbCdEf" + "." + "x" * 30
WEBHOOK = "https://discord.com/api/webhooks/" + "123456789/" + "abcDEF" * 8


def test_patterns() -> None:
    from sanitize import redact
    for name, secret in (("Groq", GROQ), ("GitHub", GITHUB), ("OpenAI", OPENAI), ("AWS", AWS),
                         ("a Discord token", DISCORD_TOKEN), ("a webhook", WEBHOOK)):
        out = redact(f"before {secret} after")
        _check(f"{name} key is removed", secret not in out and "[REDACTED]" in out and out.startswith("before"), out)
    for text in ("api_key=abcd1234efgh5678", "password: hunter2hunter2", "Authorization: Bearer abcd1234efgh5678ijkl",
                 'token = "abcd1234efgh5678"'):
        out = redact(text)
        _check(f"the value in {text.split('=')[0].split(':')[0]!r} is removed", "abcd1234" not in out and "hunter2" not in out, out)
    for text in ("task-management-software-platform-2026", "risk-assessment-framework-for-large-projects",
                 "max_tokens=2048", "the token budget is 200000", "password reset links expire in 24 hours",
                 "See https://discord.com/channels/123/456", "a skeleton-key approach to something long"):
        _check(f"left alone: {text!r}", redact(text) == text, redact(text))
    _check("empty text is fine", redact("") == "" and redact(None) is None)


def test_real_keys_by_value() -> None:
    from sanitize import redact
    value = "c0ffee00-1234-5678-9abc-def012345678"
    os.environ["EXA_API_KEY"] = value
    try:
        _check("a key of no known shape is removed by its value", value not in redact(f"key {value} end"), redact(f"key {value}"))
    finally:
        del os.environ["EXA_API_KEY"]
    _check("and once it is unset, nothing is", value in redact(f"key {value} end"))


def test_everywhere_it_matters() -> None:
    from sanitize import RedactingFilter, clean
    _check("text going to a person or a file", GROQ not in clean(f"key {GROQ}"), clean(f"key {GROQ}"))
    record = logging.LogRecord("x", logging.ERROR, __file__, 1, "failed with %s", (GROQ,), None)
    RedactingFilter().filter(record)
    _check("a log line", GROQ not in record.getMessage(), record.getMessage())
    plain = logging.LogRecord("x", logging.INFO, __file__, 1, "%d tokens", (5,), None)
    RedactingFilter().filter(plain)
    _check("an ordinary log line keeps its arguments", plain.args == (5,) and plain.getMessage() == "5 tokens")

    from agents import react
    calls = []

    async def create(model, **kwargs):
        calls.append(kwargs)
        if len(calls) == 1:
            call = SimpleNamespace(id="c1", type="function", function=SimpleNamespace(name="memory_read", arguments=json.dumps({"file": "tasks.md"})))
            return SimpleNamespace(choices=[SimpleNamespace(finish_reason="tool_calls", message=SimpleNamespace(content=None, tool_calls=[call]))], usage=None)
        return SimpleNamespace(choices=[SimpleNamespace(finish_reason="stop", message=SimpleNamespace(content="Done.", tool_calls=None))], usage=None)

    async def execute(name, inputs, allow_private=False):
        return f"the file says {GITHUB}"

    fake = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=create)))
    real_client, real_execute = react._get_client, react._execute_tool
    try:
        react._get_client, react._execute_tool = (lambda: fake), execute
        asyncio.run(react.handle("Read my tasks.", []))
    finally:
        react._get_client, react._execute_tool = real_client, real_execute
    result = [m for m in calls[1]["messages"] if m.get("role") == "tool"][0]["content"]
    _check("what a tool returns, before the model sees it", GITHUB not in result and "[REDACTED]" in result, result)


NOW = datetime(2026, 10, 30, 14, 0, tzinfo=timezone.utc)


def _deliver(reply, repeat=None):
    from agents import scheduled
    (config.MEMORY_DIR / "scheduled.json").unlink(missing_ok=True)
    entry = {"id": "abc123", "content": "", "brief": "Tell me only if the desk is on sale.", "due": (NOW - timedelta(minutes=1)).isoformat()}
    if repeat:
        entry["repeat"] = repeat
    scheduled._save([entry])
    sent = []

    async def send(text):
        sent.append(text)
        return True

    async def compose(brief, now, recent=None):
        if isinstance(reply, Exception):
            raise reply
        return reply

    count = asyncio.run(scheduled.deliver_due(send, now=NOW, compose=compose))
    return count, sent, scheduled._load()


def test_silent() -> None:
    count, sent, left = _deliver("[SILENT]")
    _check("nothing is sent", sent == [] and count == 0, f"{sent} {count}")
    _check("a one-off is done with", left == [], str(left))
    count, sent, left = _deliver("[SILENT]", repeat="daily")
    _check("a repeating one moves to its next day", sent == [] and len(left) == 1 and left[0]["due"] > NOW.isoformat(), str(left))
    _check("and a silent day is not an earlier message in the series", not left[0].get("recent"), str(left))
    count, sent, left = _deliver("The desk is on sale for 199 dollars.")
    _check("a real report goes out", sent == ["The desk is on sale for 199 dollars."] and count == 1, str(sent))
    count, sent, left = _deliver(RuntimeError("model down"))
    _check("a run that fails still reaches the user, as Hermes' does", len(sent) == 1 and "desk" in sent[0], str(sent))
    from agents import compose as compose_module, react
    _check("the writer is told how to stay silent", "[SILENT]" in compose_module._AGENT_SYSTEM)
    tool = next(t for t in react._TOOLS if t["name"] == "scheduled_message")
    _check("and React that a brief can ask for it", "only if" in json.dumps(tool["input_schema"]), json.dumps(tool["input_schema"])[:200])


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            print(name)
            fn()
    if _failures:
        print(f"\n{len(_failures)} FAILED: {', '.join(_failures)}")
        sys.exit(1)
    print("\nall pass")
