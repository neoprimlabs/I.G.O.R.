"""The weekly advocacy draft: figures are checked against the source before sending.

2026-10-19 a draft went out with savings and debt percentages that only an AI
summary site stated, and the user, who signs these posts, has no time to check them.

Run: python tests/test_advocacy.py   (repo root, stdlib only, no API calls)
"""

import asyncio
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import config
import llm
from agents import monitor, research

_failures: list[str] = []

PAGE = ("Only 15% of recipients said they were going into debt, compared to 42% of the "
        "control group. The City paid $1,000 a month and allocated $4.6 million in total.")
RESULTS = [{"title": "Data from Alameda", "url": "https://example.org/alameda",
            "body": "Recipients: 15 percent in debt versus 42 percent of the control group."}]


def _check(name: str, condition: bool, detail: str = "") -> None:
    if condition:
        print(f"  pass  {name}")
    else:
        print(f"  FAIL  {name}  {detail}")
        _failures.append(name)


def test_figures_on_the_page_pass() -> None:
    draft = "Only 15 % went into debt versus 42% without the $1,000 a month and $4.6 million.\nSource: [1]"
    _check("figures the page states pass, however they are spaced",
           monitor._unsourced_figures(draft, PAGE) == [], str(monitor._unsourced_figures(draft, PAGE)))


def test_figures_not_on_the_page_are_named() -> None:
    draft = "35% had savings versus 12 percent, and 15% went into debt. $2,500 each.\nSource: [1]"
    missing = monitor._unsourced_figures(draft, PAGE)
    _check("each unstated figure is named once", missing == ["35%", "12 percent", "$2,500"], str(missing))
    _check("a digit inside a bigger number is not a match",
           monitor._unsourced_figures("5% of them", "15% of them") == ["5%"])
    _check("the Source line's index is not a figure",
           monitor._unsourced_figures("No figures here.\nSource: [3]", PAGE) == [])


def _run_draft(drafts: list[str], page: str = PAGE) -> tuple[list[str], list[str], list[str]]:
    """(messages sent, user prompts given to the model, pages fetched)."""
    sent, users, fetched = [], [], []
    tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
    real = (config.MEMORY_DIR, llm.complete, research._run_search, research._page_text,
            monitor._send_fn, monitor._client)
    queue = list(drafts)

    async def complete(client, model, system, user, **kw):
        users.append(user)
        return queue.pop(0) if queue else ""

    async def search(*a, **kw):
        return RESULTS

    async def page_text(url, max_chars=30000):
        fetched.append(url)
        return page

    async def send(text):
        sent.append(text)

    try:
        config.MEMORY_DIR = Path(tmp.name)
        llm.complete, research._run_search, research._page_text = complete, search, page_text
        monitor._send_fn, monitor._client = send, object()
        asyncio.run(monitor._weekly_advocacy_draft())
    finally:
        (config.MEMORY_DIR, llm.complete, research._run_search, research._page_text,
         monitor._send_fn, monitor._client) = real
        tmp.cleanup()
    return sent, users, fetched


def test_unsourced_draft_is_retried_once_told_why() -> None:
    sent, users, _ = _run_draft(["35% saved versus 12%.\nSource: [1]",
                                 "Only 15% went into debt, against 42% without it.\nSource: [1]"])
    _check("two drafting calls", len(users) == 2, str(len(users)))
    _check("the retry names the figures the source does not state",
           "35%, 12%" in users[-1], users[-1][-200:])
    _check("the fixed draft is sent, saying its figures were checked against the page",
           len(sent) == 1 and "15%" in sent[0] and "checked against the source page" in sent[0],
           str(sent))
    _check("no request to check the numbers is left in the message",
           sent and "Check the numbers" not in sent[0])


def test_still_unsourced_after_retry_is_not_sent() -> None:
    sent, users, _ = _run_draft(["35% saved.\nSource: [1]", "Still 35% saved.\nSource: [1]"])
    _check("nothing is sent when the retry is still unsourced", sent == [], str(sent))
    _check("and there is no third call", len(users) == 2, str(len(users)))


def test_unreadable_page_falls_back_to_the_summary() -> None:
    sent, _, _ = _run_draft(["15% went into debt.\nSource: [1]"], page="")
    _check("a figure in the search summary passes when the page cannot be read, and says so",
           len(sent) == 1 and "page itself could not be read" in sent[0], str(sent))


def test_no_figures_needs_no_fetch() -> None:
    sent, _, fetched = _run_draft(["Families met basic needs.\nSource: [1]"])
    _check("a draft with no figures is sent without fetching the page",
           len(sent) == 1 and fetched == [], f"{sent} {fetched}")


def test_figures_without_a_source_line_are_rejected() -> None:
    sent, users, _ = _run_draft(["15% went into debt.", "15% went into debt.\nSource: [1]"])
    _check("figures with no Source line are retried",
           len(users) == 2 and "no Source line" in users[-1], users[-1][-200:] if users else "")
    _check("and the sourced retry is sent", len(sent) == 1, str(sent))


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            print(name)
            fn()
    if _failures:
        print(f"\n{len(_failures)} FAILED: {', '.join(_failures)}")
        sys.exit(1)
    print("\nall pass")
