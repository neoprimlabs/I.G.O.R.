"""Tests for agents/jobs.py - watching public job feeds for remote work.

Run: python tests/test_jobs.py   (repo root, stdlib only, no network)

Indeed is not a source and cannot be: the Publisher API was retired in 2023, the
Job Search API is closed to new developers, and current access is an NDA-gated
partner program. What is left is scraping against their terms, through Cloudflare,
on a 956 MB box. These four sources publish JSON or RSS openly and were each
confirmed reachable from the server on 2026-09-23.

IGOR does not apply. It finds and it drafts; a human sends. Same rule as the
advocacy posts, and the evidence agrees: a Robert Half survey of 2,000+ hiring
managers (fielded Nov 2025) found 67% said AI-generated applications had slowed
their hiring, and mass identical submissions get flagged.

Fixtures are trimmed copies of what each endpoint actually returned.
"""

import json
import sys
import tempfile
from datetime import datetime, timedelta, timezone
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


NOW = datetime(2026, 9, 23, 13, 0, tzinfo=timezone.utc)

CRITERIA = """# Job Search

## Titles
- AI trainer
- prompt engineer
- LLM evaluator
- customer support
- QA tester

## Exclude
- senior
- staff engineer
- security clearance
"""

REMOTIVE = json.dumps({"jobs": [
    {"title": "AI Trainer (Remote)", "company_name": "Scale Labs",
     "url": "https://remotive.com/remote-jobs/ai-trainer-1",
     "candidate_required_location": "Worldwide", "publication_date": "2026-09-23T09:00:00"},
    {"title": "Senior Backend Engineer", "company_name": "Acme",
     "url": "https://remotive.com/remote-jobs/senior-backend-2",
     "candidate_required_location": "USA", "publication_date": "2026-09-23T09:00:00"},
]})

GREENHOUSE = json.dumps({"jobs": [
    {"title": "Customer Support Specialist",
     "absolute_url": "https://job-boards.greenhouse.io/examplecorp/jobs/1",
     "location": {"name": "Remote - US"}, "updated_at": "2026-09-22T18:00:00-04:00"},
    {"title": "Prompt Engineer, Evaluation",
     "absolute_url": "https://job-boards.greenhouse.io/examplecorp/jobs/2",
     "location": {"name": "San Francisco, CA"}, "updated_at": "2026-09-22T18:00:00-04:00"},
]})

WWR_RSS = """<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0"><channel>
<item><title>Helpful Co: Customer Support Associate</title>
<link>https://weworkremotely.com/remote-jobs/helpful-co-support</link>
<pubDate>Tue, 22 Sep 2026 12:00:00 +0000</pubDate></item>
<item><title>Bank Inc: Staff Engineer, Payments</title>
<link>https://weworkremotely.com/remote-jobs/bank-inc-staff</link>
<pubDate>Tue, 22 Sep 2026 12:00:00 +0000</pubDate></item>
</channel></rss>"""


def _fresh():
    config.MEMORY_DIR = Path(tempfile.mkdtemp())
    (config.MEMORY_DIR / "job_search.md").write_text(CRITERIA, encoding="utf-8")
    return config.MEMORY_DIR


def test_criteria() -> None:
    from agents import jobs

    _fresh()
    wanted, excluded = jobs._criteria()
    _check("titles are read from the file", "ai trainer" in wanted and "qa tester" in wanted, str(wanted))
    _check("exclusions are read too", "senior" in excluded, str(excluded))

    config.MEMORY_DIR = Path(tempfile.mkdtemp())
    wanted, excluded = jobs._criteria()
    _check("a missing file means no keywords, not a crash", wanted == [] and excluded == [])


def test_normalisers() -> None:
    from agents import jobs

    remotive = jobs._from_remotive(REMOTIVE)
    _check("remotive returns both postings", len(remotive) == 2, str(remotive))
    _check("with title, company and url",
           remotive[0]["title"] == "AI Trainer (Remote)"
           and remotive[0]["company"] == "Scale Labs"
           and remotive[0]["url"].endswith("ai-trainer-1"), str(remotive[0]))

    gh = jobs._from_greenhouse(GREENHOUSE, "examplecorp")
    _check("greenhouse keeps the company it was asked for",
           all(j["company"] == "examplecorp" for j in gh), str(gh))
    _check("and carries the location, which decides remote",
           "Remote" in gh[0]["location"], str(gh[0]))

    wwr = jobs._from_wwr(WWR_RSS)
    _check("wwr splits company from title",
           wwr[0]["company"] == "Helpful Co" and wwr[0]["title"] == "Customer Support Associate",
           str(wwr[0]))

    _check("malformed json is no postings, not an exception", jobs._from_remotive("{not json") == [])
    _check("malformed xml is no postings either", jobs._from_wwr("<not xml") == [])


def test_matching() -> None:
    from agents import jobs

    _fresh()
    wanted, excluded = jobs._criteria()

    keep = {"title": "AI Trainer (Remote)", "company": "Scale Labs", "location": "Worldwide",
            "url": "https://x/1", "source": "remotive"}
    _check("a wanted title matches", jobs._matches(keep, wanted, excluded))

    drop = dict(keep, title="Senior Backend Engineer")
    _check("an unwanted title does not", not jobs._matches(drop, wanted, excluded))

    excluded_hit = dict(keep, title="Senior AI Trainer")
    _check("an exclusion beats a match", not jobs._matches(excluded_hit, wanted, excluded))

    onsite = dict(keep, title="Customer Support Specialist", location="San Francisco, CA")
    _check("an on-site role is dropped even when the title fits",
           not jobs._matches(onsite, wanted, excluded), str(onsite))

    remote_named = dict(onsite, location="Remote - US")
    _check("a remote location is kept", jobs._matches(remote_named, wanted, excluded))

    no_location = dict(keep, location="", source="wwr")
    _check("a source that only lists remote work needs no location",
           jobs._matches(no_location, wanted, excluded), str(no_location))


def test_seen_and_collect() -> None:
    from agents import jobs

    path = _fresh()

    def _fake_fetchers():
        return [("remotive", lambda: jobs._from_remotive(REMOTIVE)),
                ("greenhouse:examplecorp", lambda: jobs._from_greenhouse(GREENHOUSE, "examplecorp")),
                ("wwr", lambda: jobs._from_wwr(WWR_RSS))]

    found = jobs.collect(now=NOW, fetchers=_fake_fetchers())
    titles = sorted(j["title"] for j in found)
    _check("only the matching remote roles survive",
           titles == ["AI Trainer (Remote)", "Customer Support Associate",
                      "Customer Support Specialist"], str(titles))

    jobs.remember(found, now=NOW)
    again = jobs.collect(now=NOW, fetchers=_fake_fetchers())
    _check("nothing already seen is reported twice", again == [], str(again))

    old = NOW - timedelta(days=40)
    (path / "jobs_seen.json").write_text(
        json.dumps({"https://x/ancient": old.isoformat()}), encoding="utf-8")
    jobs.remember([], now=NOW)
    _check("entries older than the window are pruned",
           "https://x/ancient" not in jobs._load_seen(), str(jobs._load_seen()))

    (path / "jobs_seen.json").write_text("{not json", encoding="utf-8")
    _check("an unreadable store reads as empty", jobs._load_seen() == {})


def test_one_bad_source_does_not_stop_the_rest() -> None:
    """Four sources, each a separate network call. One being down is normal."""
    from agents import jobs

    _fresh()

    def _boom():
        raise RuntimeError("connection reset")

    found = jobs.collect(now=NOW, fetchers=[
        ("broken", _boom),
        ("remotive", lambda: jobs._from_remotive(REMOTIVE)),
    ])
    _check("the working source still reports",
           [j["title"] for j in found] == ["AI Trainer (Remote)"], str(found))


if __name__ == "__main__":
    print("criteria")
    test_criteria()
    print("\nnormalisers")
    test_normalisers()
    print("\nmatching")
    test_matching()
    print("\nseen store and collection")
    test_seen_and_collect()
    print("\na source being down")
    test_one_bad_source_does_not_stop_the_rest()

    if _failures:
        print(f"\n{len(_failures)} FAILED: {', '.join(_failures)}")
        sys.exit(1)
    print("\nall pass")
