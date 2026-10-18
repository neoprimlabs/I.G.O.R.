"""Tests for agents/gigs.py - watching public boards for paid AI and automation gigs.

Run: python tests/test_gigs.py   (repo root, stdlib only, no network)

The user, 2026-10-18: "I don't want anything red flagged coming my way at all. It
should be filtered out and IGOR should never interact with red flag accounts." Most of
these tests are about that: what is dropped, who is blocked, and that a failed
screening call holds the day's gigs instead of sending them half screened.

Fixtures are trimmed copies of what the n8n forum and Reddit returned on 2026-10-18.
"""

import asyncio
import json
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import config

_failures: list[str] = []


def _check(name: str, condition: bool, detail: str = "") -> None:
    if condition:
        print(f"  pass  {name}")
    else:
        print(f"  FAIL  {name}  {detail}")
        _failures.append(name)


NOW = datetime(2026, 10, 19, 14, 45, tzinfo=timezone.utc)

N8N_LIST = json.dumps({"topic_list": {"topics": [
    {"id": 1, "title": "About the Jobs category", "slug": "about-the-jobs-category",
     "created_at": "2020-03-05T10:00:00.000Z", "pinned": True},
    {"id": 322445, "title": "[HIRING] Shopify tracking + n8n specialist, part-time ongoing, $35/hr",
     "slug": "hiring-shopify-tracking-n8n-specialist", "created_at": "2026-10-18T09:00:00.000Z", "pinned": False},
    {"id": 322231, "title": "• [Available] Hardened n8n &amp; AI Engineer", "slug": "available-hardened",
     "created_at": "2026-10-17T08:00:00.000Z", "pinned": False},
    {"id": 321881, "title": "Free pilot: checking differences between two CSV exports", "slug": "free-pilot",
     "created_at": "2026-10-16T17:28:58.332Z", "pinned": False},
    {"id": 321496, "title": "Exploring n8n implementation partnerships for SaaS workflow plans",
     "slug": "exploring-partnerships", "created_at": "2026-10-14T12:00:00.000Z", "pinned": False},
    {"id": 300000, "title": "Seeking a reliable n8n developer for ongoing work", "slug": "seeking-reliable",
     "created_at": "2026-05-26T12:00:00.000Z", "pinned": False},
    {"id": 322016, "title": "N8n + MirrorFly Developer for Video Conferencing Automation", "slug": "n8n-mirrorfly",
     "created_at": "2026-10-16T10:00:00.000Z", "pinned": False},
]}})

BODIES = {
    322445: ("boston_young", "We fix Shopify tracking and build n8n automations. HubSpot later. Paid hourly."),
    321881: ("Vojtech_Minarik", "I'd like to help with one small case for free. Free pilot, no payment required."),
    321496: ("pony_weft", "To be clear: I'm validating the service and there is no active project yet."),
    322016: ("Shyamvijay", "Build a video conferencing automation with n8n, webhooks and HubSpot. Budget 800 GBP."),
}

REDDIT = """<?xml version="1.0" encoding="UTF-8"?><feed xmlns="http://www.w3.org/2005/Atom">
<entry><author><name>/u/dental_owner</name></author>
<title>[Hiring] AI chatbot for my dental clinic website &amp; booking reminders, $800 fixed</title>
<link href="https://www.reddit.com/r/forhire/comments/a1/chatbot/"/>
<published>2026-10-18T20:00:00+00:00</published>
<content type="html">&lt;p&gt;Need a Python or n8n chatbot that answers FAQs.&lt;/p&gt; submitted by /u/dental_owner [link]</content></entry>
<entry><author><name>/u/va_seeker</name></author>
<title>[Hiring] Virtual Assistant, Remote USA, $20/hour</title>
<link href="https://www.reddit.com/r/forhire/comments/a2/va/"/>
<published>2026-10-18T19:00:00+00:00</published>
<content type="html">&lt;p&gt;Inbox and calendar help, ten hours a week.&lt;/p&gt;</content></entry>
<entry><author><name>/u/fullstack_dev</name></author>
<title>[For Hire] Full Stack Developer, $20/hr</title>
<link href="https://www.reddit.com/r/forhire/comments/a3/fullstack/"/>
<published>2026-10-18T18:00:00+00:00</published>
<content type="html">&lt;p&gt;I build automation and AI apps.&lt;/p&gt;</content></entry>
<entry><author><name>/u/quick_cash</name></author>
<title>[Hiring] n8n automation expert needed fast</title>
<link href="https://www.reddit.com/r/forhire/comments/a4/fast/"/>
<published>2026-10-18T17:00:00+00:00</published>
<content type="html">&lt;p&gt;Big budget. Contact me on Telegram to start today.&lt;/p&gt;</content></entry>
</feed>"""

PROFILE = "Builds a production AI assistant in Python. Flutter apps. No contact details here.\n"


def _fresh(gig_search: str = ""):
    tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
    config.MEMORY_DIR = Path(tmp.name)
    (config.MEMORY_DIR / "private").mkdir()
    (config.MEMORY_DIR / "private" / "profile.md").write_text(PROFILE, encoding="utf-8")
    if gig_search:
        (config.MEMORY_DIR / "gig_search.md").write_text(gig_search, encoding="utf-8")
    from agents import gigs
    return gigs, tmp


def _fetchers(gigs, reddit=REDDIT, n8n=N8N_LIST):
    return [("n8n", lambda: gigs._from_n8n_list(n8n)), ("reddit", lambda: gigs._from_reddit(reddit))]


class _Topics:
    def __init__(self):
        self.calls = []

    def __call__(self, topic_id):
        self.calls.append(topic_id)
        return BODIES.get(topic_id, ("someone", "An n8n automation job."))


def test_parsers() -> None:
    gigs, tmp = _fresh()
    items = gigs._from_n8n_list(N8N_LIST)
    _check("the pinned category post is skipped", all(i["topic_id"] != 1 for i in items))
    _check("n8n urls are built from slug and id",
           items[0]["url"] == "https://community.n8n.io/t/hiring-shopify-tracking-n8n-specialist/322445", items[0]["url"])
    _check("n8n titles are unescaped", any("&" in i["title"] and "&amp;" not in i["title"] for i in items))
    author, body = gigs._from_n8n_topic(json.dumps(
        {"post_stream": {"posts": [{"username": "boston_young", "cooked": "<p>We fix &amp; build</p>"}]}}))
    _check("a topic gives author and plain body", (author, body) == ("boston_young", "We fix & build"), str((author, body)))
    _check("a broken topic gives nothing", gigs._from_n8n_topic("{}") == ("", ""))
    posts = gigs._from_reddit(REDDIT)
    first = posts[0]
    _check("reddit author loses its /u/", first["author"] == "dental_owner", first["author"])
    _check("reddit title is unescaped", "& booking" in first["title"], first["title"])
    _check("reddit body drops the 'submitted by' trailer",
           "submitted by" not in first["body"] and "FAQs" in first["body"], first["body"])
    _check("broken feeds parse to nothing", gigs._from_reddit("<html>") == [] and gigs._from_n8n_list("x") == [])
    tmp.cleanup()


def test_only_hiring_posts() -> None:
    gigs, tmp = _fresh()
    for title in ("• [Available] Hardened n8n engineer", "[For Hire] audits", "For hire: n8n builds",
                  "[OFFERING] workflow reviews"):
        _check(f"not hiring: {title!r}", not gigs._is_hiring({"title": title, "source": "n8n"}))
    _check("an ordinary n8n job title is hiring",
           gigs._is_hiring({"title": "N8n + MirrorFly Developer", "source": "n8n"}))
    _check("reddit needs the [Hiring] tag",
           not gigs._is_hiring({"title": "Need an n8n dev", "source": "reddit"})
           and gigs._is_hiring({"title": "[HIRING] n8n dev", "source": "reddit"}))
    tmp.cleanup()


def test_red_flags_are_specific() -> None:
    gigs, tmp = _fresh()
    for text in ("Automate reminders for unpaid invoices", "Build a paid Telegram bot with OCR",
                 "Budget is flexible", "Connect our WhatsApp Cloud API to HubSpot", "Use our account manager"):
        _check(f"not a red flag: {text!r}", gigs.red_flag(text) == "", gigs.red_flag(text))
    for text in ("This is unpaid but great exposure", "Free pilot to start", "Contact me on Telegram",
                 "There is no active project yet", "Send a deposit for the starter kit",
                 "We will send you a check to deposit", "We rent your Upwork account", "Paid in crypto only",
                 "Commission-only role", "Please send your passport",
                 "Join our founding vendor cohort. Earnings depend on actual sales.",
                 "Potential long-term partnership with revenue sharing"):
        _check(f"red flag: {text!r}", gigs.red_flag(text) != "", text)
    _check("a revenue share on top of stated pay is not a red flag",
           gigs.red_flag("Part-time ongoing, $35/hr guaranteed + revenue share") == "")
    _check("listed red flags add to the built-in ones",
           gigs.red_flag("Must use our proprietary SDK", ("proprietary sdk",)).startswith("listed"))
    tmp.cleanup()


def test_collect_filters_and_blocks() -> None:
    gigs, tmp = _fresh()
    topics = _Topics()
    found, status = gigs.collect(NOW, _fetchers(gigs), topics)
    urls = [g["url"] for g in found]
    _check("the paid gigs come through",
           any("322445" in u for u in urls) and any("322016" in u for u in urls)
           and any("chatbot" in u for u in urls), str(urls))
    _check("the free pilot and the no-project post are dropped",
           not any("321881" in u or "321496" in u for u in urls), str(urls))
    _check("self-advertising and off-topic posts are dropped",
           not any("322231" in u or "fullstack" in u or "/va/" in u for u in urls), str(urls))
    _check("the Telegram-contact post is dropped", not any("/fast/" in u for u in urls), str(urls))
    _check("old posts are not even opened", 300000 not in topics.calls and 1 not in topics.calls, str(topics.calls))
    _check("self-advertising posts are not opened", 322231 not in topics.calls, str(topics.calls))
    accounts = gigs.blocked()
    _check("red-flag accounts are blocked with the reason",
           {"n8n:vojtech_minarik", "n8n:pony_weft", "reddit:quick_cash"} <= set(accounts)
           and "private chat" in accounts["reddit:quick_cash"]["why"], str(accounts))
    _check("each source reports what it returned", status.get("n8n") == "6 posts" and status.get("reddit") == "4 posts",
           str(status))
    tmp.cleanup()


def test_blocked_account_stays_blocked() -> None:
    gigs, tmp = _fresh()
    gigs.collect(NOW, _fetchers(gigs), _Topics())
    clean = REDDIT.replace("Contact me on Telegram to start today.", "Paid n8n build, 600 USD.").replace(
        "/a4/fast/", "/a5/second/")
    found, _ = gigs.collect(NOW, _fetchers(gigs, reddit=clean, n8n=json.dumps({"topic_list": {"topics": []}})),
                            _Topics())
    _check("a clean later post from a blocked account is dropped",
           not any("/a5/" in g["url"] for g in found), str([g["url"] for g in found]))
    tmp.cleanup()


def test_exclude_drops_without_blocking() -> None:
    gigs, tmp = _fresh("# Gig Search\n\n## Exclude\n- mirrorfly\n")
    found, _ = gigs.collect(NOW, _fetchers(gigs), _Topics())
    _check("an excluded posting is dropped", not any("322016" in g["url"] for g in found))
    _check("and its poster is not blocked", "n8n:shyamvijay" not in gigs.blocked(), str(gigs.blocked()))
    tmp.cleanup()


def test_dropped_posts_are_not_reopened() -> None:
    gigs, tmp = _fresh()
    gigs.collect(NOW, _fetchers(gigs), _Topics())
    topics = _Topics()
    found, _ = gigs.collect(NOW, _fetchers(gigs), topics)
    _check("posts dropped after reading are remembered", 321881 not in topics.calls and 321496 not in topics.calls,
           str(topics.calls))
    _check("gigs that passed are not remembered until sent", 322445 in topics.calls, str(topics.calls))
    tmp.cleanup()


def test_body_fetches_are_capped() -> None:
    gigs, tmp = _fresh()
    many = json.dumps({"topic_list": {"topics": [
        {"id": 400000 + i, "title": f"n8n job {i}", "slug": f"job-{i}", "created_at": "2026-10-18T09:00:00.000Z"}
        for i in range(40)]}})
    topics = _Topics()
    gigs.collect(NOW, [("n8n", lambda: gigs._from_n8n_list(many))], topics)
    _check("at most the cap of bodies are read in one run", len(topics.calls) == gigs._MAX_BODIES, str(len(topics.calls)))
    tmp.cleanup()


def test_one_bad_source_does_not_stop_the_rest() -> None:
    gigs, tmp = _fresh()

    def down():
        raise OSError("HTTP Error 429: Too Many Requests")

    found, status = gigs.collect(NOW, [("reddit", down), ("n8n", lambda: gigs._from_n8n_list(N8N_LIST))], _Topics())
    _check("the n8n gigs still arrive", any("322445" in g["url"] for g in found))
    _check("the failure is in the status", status["reddit"].startswith("unavailable"), str(status))
    tmp.cleanup()


def test_parse() -> None:
    gigs, tmp = _fresh()
    picks, skips, red = gigs._parse(
        "RED 2: vendor advertising its product\n1. Uses the Python agent work https://x.example\n"
        "**SKIP 3**: locked to Singapore\n4. A stretch: needs Shopify tracking\n9. out of range\n"
        "2. also a note\n3. also a note", 4)
    _check("RED lines are read", red == {2: "vendor advertising its product"}, str(red))
    _check("SKIP lines are read", skips == {3: "locked to Singapore"}, str(skips))
    _check("picks keep their order, links stripped, RED and SKIP win",
           list(picks) == [1, 4] and "https" not in picks[1], str(picks))
    live = ("3. N. Build n8n + MirrorFly video conferencing automation; gap: MirrorFly API integration experience.\n"
            "1. N. Shopify tracking + n8n automation build; gap: Shopify API integration specifics.\n"
            "2. SKIP 2: QA role, not a build.\n"
            "4. SKIP 4: Requires Southeast Asia time zone, not US Eastern.")
    picks, skips, red = gigs._parse(live, 4)
    _check("20b's live 10-18 output: a numbered SKIP is a skip, not a pick",
           list(picks) == [3, 1] and set(skips) == {2, 4} and red == {}, f"{picks} {skips}")
    _check("and the copied 'N.' placeholder is stripped", picks[3].startswith("Build n8n"), picks[3])
    _check("a RED with the number in front is still red", gigs._parse("2. RED: scam", 3)[2] == {2: "scam"},
           str(gigs._parse("2. RED: scam", 3)))
    _check("a note that starts with N is not cut", gigs._parse("1. Needs Python", 1)[0] == {1: "Needs Python"})
    _check("unusable output is None, so the gigs are held", gigs._parse("I cannot help with that.", 3) is None)
    _check("NONE is not a verdict either", gigs._parse("NONE", 3) is None)
    _check("empty output is None", gigs._parse("", 3) is None)
    tmp.cleanup()


def _run(gigs, ask, send_ok=True, fetchers=None):
    sent = []

    async def send(text):
        sent.append(text)
        return send_ok

    result = asyncio.run(gigs.run(send, now=NOW, fetchers=fetchers or _fetchers(gigs), topic_fetch=_Topics(), ask=ask))
    return result, sent


def test_failed_screening_holds_the_gigs() -> None:
    gigs, tmp = _fresh()

    async def broken(profile, listing):
        raise RuntimeError("429")

    result, sent = _run(gigs, broken)
    _check("nothing is sent when screening fails", sent == [], str(sent))
    import watchers
    seen = watchers.load_seen(gigs._SEEN)
    _check("and the gigs are not remembered", not any("322445" in u for u in seen), str(list(seen)))
    status = (config.MEMORY_DIR / "gigs_found.md").read_text(encoding="utf-8")
    _check("the status file says they were held", "held" in status, status[:300])
    tmp.cleanup()


def test_screened_run_sends_and_blocks() -> None:
    gigs, tmp = _fresh()
    listings = []

    async def screen(profile, listing):
        listings.append((profile, listing))
        # In collect's order: 1 Shopify, 2 MirrorFly, 3 the dental chatbot.
        return ("SKIP 1: needs Shopify tracking experience\nRED 2: looks like a vendor advertising its own product\n"
                "3. Uses the Python agent work for a chatbot; new to n8n.")

    result, sent = _run(gigs, screen)
    _check("one message is sent", len(sent) == 1, str(sent))
    message = sent[0] if sent else ""
    _check("it lists only the pick, numbered", message.startswith("**New gigs worth a look**")
           and "1. [Hiring] AI chatbot" in message and "new to n8n" in message, message)
    _check("skipped and red-flagged gigs are not shown", "Shopify" not in message and "MirrorFly" not in message, message)
    _check("the red-flagged account is blocked", "n8n:shyamvijay" in gigs.blocked(), str(gigs.blocked()))
    _check("the screening call never sees contact details", "@" not in listings[0][0], listings[0][0])
    _check("the listing carries post bodies", "HubSpot" in listings[0][1], listings[0][1][:200])
    found_file = (config.MEMORY_DIR / "gigs_found.md").read_text(encoding="utf-8")
    _check("the pick is kept for questions later", "AI chatbot" in found_file and "Worth a look:" in found_file)
    _check("the skip is kept with its reason", "Skipped: needs Shopify tracking" in found_file, found_file[-600:])
    _check("red-flagged gigs never reach the file", "MirrorFly" not in found_file)
    _check("the log summary counts each verdict", result == "sent 1 picks, 1 skipped, 1 red flags", result)
    result, sent = _run(gigs, screen)
    _check("the next run has nothing new and sends nothing", sent == [] and result == "nothing new", f"{result} {sent}")
    tmp.cleanup()


def test_unjudged_gig_is_held() -> None:
    gigs, tmp = _fresh()

    async def screen(profile, listing):
        return "3. Uses the Python agent work."

    result, sent = _run(gigs, screen)
    _check("the judged pick is sent", len(sent) == 1 and "AI chatbot" in sent[0], str(sent))
    _check("the summary says two were held", result.endswith("2 held"), result)
    topics = _Topics()
    gigs.collect(NOW, _fetchers(gigs), topics)
    _check("gigs with no verdict are screened again next run", 322445 in topics.calls and 322016 in topics.calls,
           str(topics.calls))
    tmp.cleanup()


def test_failed_send_keeps_the_gigs() -> None:
    gigs, tmp = _fresh()

    async def screen(profile, listing):
        return "1. Fits.\nSKIP 2: not for them\nSKIP 3: not for them"

    _run(gigs, screen, send_ok=False)
    import watchers
    seen = watchers.load_seen(gigs._SEEN)
    _check("a gig that never reached the user is not remembered", not any("322445" in u for u in seen))
    _check("nor are the skips from that run", not any("322016" in u for u in seen), str(list(seen)))
    status = (config.MEMORY_DIR / "gigs_found.md").read_text(encoding="utf-8")
    _check("the status file says the send failed", "Sending failed" in status, status[:300])
    tmp.cleanup()


def test_tool_counts() -> None:
    gigs, tmp = _fresh()

    async def screen(profile, listing):
        return "RED 2: vendor"

    _run(gigs, screen)
    posts, counts = gigs.tool_counts()
    tally = dict(counts)
    _check("hiring posts are counted, red flags and screened-out posts are not", posts == 3, str((posts, counts)))
    _check("tools are named", tally.get("n8n", 0) >= 2 and tally.get("Shopify") == 1, str(counts))
    found_file = (config.MEMORY_DIR / "gigs_found.md").read_text(encoding="utf-8")
    _check("the counts are in the file IGOR reads", "## Tools named" in found_file and "- n8n" in found_file)
    tmp.cleanup()


def test_gig_context_reaches_the_profile() -> None:
    gigs, tmp = _fresh("# Gig Search\n\n## Context\n- Learning n8n now.\n- US Eastern time. English only.\n")
    seen = []

    async def screen(profile, listing):
        seen.append(profile)
        return "SKIP 1: not for them\nSKIP 2: not for them\nSKIP 3: not for them"

    result, sent = _run(gigs, screen)
    _check("context lines are added to the profile", seen and "Learning n8n now." in seen[0], str(seen))
    _check("a day with no picks sends nothing", sent == [] and result.startswith("no picks"), f"{result} {sent}")
    import watchers
    _check("but its skips are remembered, so they are not screened again",
           any("322445" in u for u in watchers.load_seen(gigs._SEEN)))
    tmp.cleanup()


def test_format_caps_the_list() -> None:
    gigs, tmp = _fresh()
    picks = [({"title": f"Gig {i}", "url": f"https://e.example/{i}", "source": "n8n"}, f"why {i}")
             for i in range(12)]
    message = gigs.format_for_discord(picks)
    _check("eight are shown, numbered", "8. Gig 7" in message and "9." not in message, message)
    _check("each with its reason", "why 0" in message and "why 7" in message, message)
    _check("the overflow is counted", "...and 4 more" in message, message)
    tmp.cleanup()


def test_status_written_when_nothing_new() -> None:
    gigs, tmp = _fresh()
    empty = json.dumps({"topic_list": {"topics": []}})
    result, sent = _run(gigs, None, fetchers=[("n8n", lambda: gigs._from_n8n_list(empty))])
    status = (config.MEMORY_DIR / "gigs_found.md").read_text(encoding="utf-8")
    _check("a quiet day sends nothing", sent == [] and result == "nothing new", result)
    _check("but the file says when it last checked", "Last checked: 2026-10-19" in status, status[:200])
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
