"""scripts/usage_report.py: per-agent calls, tokens and times from the journal.

Run: python tests/test_usage_report.py   (repo root, stdlib only, no API calls)
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

import usage_report

_failures: list[str] = []


def _check(name: str, condition: bool, detail: str = "") -> None:
    if condition:
        print(f"  pass  {name}")
    else:
        print(f"  FAIL  {name}  {detail}")
        _failures.append(name)


LOG = """\
2026-10-20 16:10:49,498 INFO orchestrator: Router: CHAT -> Direct
2026-10-20 16:10:50,100 INFO usage: Router used 400 tokens (390 in, 10 out) on qwen/qwen3.8-27b
2026-10-20 16:10:55,667 INFO usage: Chat used 5000 tokens (4400 in, 600 out) on openai/gpt-oss-120b
2026-10-21 09:00:01,001 INFO usage: Chat used 3000 tokens (2800 in, 200 out) on openai/gpt-oss-120b in 2000 ms
2026-10-21 09:05:01,001 INFO usage: Chat used 4000 tokens (3600 in, 400 out) on openai/gpt-oss-120b in 6000 ms
2026-10-21 09:05:02,001 INFO usage: Gig screening used 2000 tokens (1500 in, 500 out) on openai/gpt-oss-120b in 3000 ms
2026-10-21 09:05:03,001 WARNING usage: Token ledger not updated - OSError: disk
"""


def test_parse() -> None:
    calls = usage_report.parse(LOG.splitlines())
    _check("one record per usage line, nothing else", len(calls) == 5, str(len(calls)))
    _check("old lines have no time", calls[0]["ms"] is None and calls[1]["ms"] is None)
    _check("new lines carry it", calls[2]["ms"] == 2000 and calls[2]["model"] == "openai/gpt-oss-120b",
           str(calls[2]))
    _check("a label with a space is kept whole", calls[4]["label"] == "Gig screening", calls[4]["label"])


def test_report() -> None:
    text = usage_report.report(usage_report.parse(LOG.splitlines()), {"openai/gpt-oss-120b": 200000})
    chat = next(line for line in text.splitlines() if line.startswith("| Chat |"))
    _check("chat: 3 calls, 4000 tokens a call", "| Chat | 3 | 4,000 |" in chat, chat)
    _check("its times come from the two timed calls", "| 2.0s |" in chat or "| 6.0s |" in chat, chat)
    _check("the summary counts days, calls and tokens",
           "2 days with calls, 5 calls, 14,400 tokens" in text, text.splitlines()[0])
    _check("timed calls are reported as a share", "Times cover 3 of 5 calls" in text, text)
    big = next(line for line in text.splitlines() if line.startswith("| openai/gpt-oss-120b"))
    _check("the busiest day is shown against the measured cap", "9,000 (4%), 2026-10-21" in big, big)
    qwen = next(line for line in text.splitlines() if line.startswith("| qwen"))
    _check("a model with no measured cap says so", "not measured" in qwen, qwen)
    _check("no calls is said plainly", usage_report.report([], {}) == "No model calls found.")


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            print(name)
            fn()
    if _failures:
        print(f"\n{len(_failures)} FAILED: {', '.join(_failures)}")
        sys.exit(1)
    print("\nall pass")
