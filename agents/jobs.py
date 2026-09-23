"""Watching public job feeds for remote work, and telling the user what is new.

IGOR does not apply. It finds postings and, on request, drafts the application; the
user sends it. That is the rule this repo already runs on for advocacy posts, and
the evidence points the same way: a Robert Half survey of 2,000+ hiring managers
(fielded 2025-11, released 2026-03) found 67% said AI-generated applications had
slowed their hiring, and identical mass submissions get flagged by ATS filters.
Those are vendor-published figures, so directional - but it is the user's name on
every application, which settles it on its own.

Indeed is not a source and cannot be. Its Publisher API was retired in 2023, the
Job Search API is closed to new developers, and current access is an NDA-gated
partner programme with six-figure minimums. The remaining route is scraping against
their terms, through Cloudflare, from a 956 MB box. These four publish openly
instead, and each was confirmed reachable from the server on 2026-09-23:

  Remotive      JSON, remote-only
  RemoteOK      JSON, remote-only (asks for attribution if republished)
  We Work Remotely  RSS per category, remote-only
  Greenhouse    JSON per company board, the ATS most remote roles post through

Fetching costs no tokens. Only drafting an application calls a model.
"""

import json
import logging
import os
import re
import urllib.request
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone
from typing import Optional

import config

logger = logging.getLogger(__name__)

_SEEN_DAYS = 30
_TIMEOUT = 20
_MAX_REPORTED = 8

# Companies whose Greenhouse boards are worth watching. Kept short and editable:
# each is one HTTP call a day, and a long list buys noise rather than coverage.
_GREENHOUSE_BOARDS = ("anthropic", "openai", "scaleai", "surgehq", "invisibletech")

_WWR_FEEDS = (
    "remote-customer-support-jobs",
    "remote-programming-jobs",
    "remote-devops-sysadmin-jobs",
)

# Sources that only ever list remote work, so a blank location is not a red flag.
_REMOTE_ONLY_SOURCES = ("remotive", "remoteok", "wwr")

_REMOTE_WORDS = ("remote", "anywhere", "worldwide", "distributed", "work from home")


def _get(url: str, accept: str = "application/json") -> str:
    request = urllib.request.Request(url, headers={
        "User-Agent": "igor-jobwatch/1.0 (personal job search)",
        "Accept": accept,
    })
    with urllib.request.urlopen(request, timeout=_TIMEOUT) as response:
        return response.read().decode("utf-8", errors="replace")


def _criteria() -> tuple[list[str], list[str]]:
    """Wanted titles and exclusions from memory/job_search.md."""
    try:
        text = (config.MEMORY_DIR / "job_search.md").read_text(encoding="utf-8")
    except OSError:
        return [], []
    wanted, excluded, section = [], [], None
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.lower().startswith("## "):
            section = stripped[3:].strip().lower()
            continue
        if not stripped.startswith("- "):
            continue
        value = stripped[2:].strip().lower()
        if not value:
            continue
        if section == "titles":
            wanted.append(value)
        elif section == "exclude":
            excluded.append(value)
    return wanted, excluded


def _from_remotive(payload: str) -> list[dict]:
    try:
        data = json.loads(payload)
    except ValueError:
        return []
    out = []
    for job in data.get("jobs", []) if isinstance(data, dict) else []:
        out.append({"title": (job.get("title") or "").strip(),
                    "company": (job.get("company_name") or "").strip(),
                    "location": (job.get("candidate_required_location") or "").strip(),
                    "url": (job.get("url") or "").strip(),
                    "source": "remotive"})
    return out


def _from_remoteok(payload: str) -> list[dict]:
    try:
        data = json.loads(payload)
    except ValueError:
        return []
    out = []
    for job in data if isinstance(data, list) else []:
        # The first element is a legal notice, not a posting.
        if not isinstance(job, dict) or not job.get("position"):
            continue
        out.append({"title": (job.get("position") or "").strip(),
                    "company": (job.get("company") or "").strip(),
                    "location": (job.get("location") or "").strip(),
                    "url": (job.get("url") or "").strip(),
                    "source": "remoteok"})
    return out


def _from_greenhouse(payload: str, company: str) -> list[dict]:
    try:
        data = json.loads(payload)
    except ValueError:
        return []
    out = []
    for job in data.get("jobs", []) if isinstance(data, dict) else []:
        location = (job.get("location") or {})
        out.append({"title": (job.get("title") or "").strip(),
                    "company": company,
                    "location": (location.get("name") or "").strip() if isinstance(location, dict) else "",
                    "url": (job.get("absolute_url") or "").strip(),
                    "source": f"greenhouse:{company}"})
    return out


def _from_wwr(payload: str) -> list[dict]:
    try:
        root = ET.fromstring(payload)
    except ET.ParseError:
        return []
    out = []
    for item in root.iter("item"):
        raw = (item.findtext("title") or "").strip()
        company, _, title = raw.partition(":")
        out.append({"title": (title or raw).strip(),
                    "company": company.strip() if title else "",
                    "location": "",
                    "url": (item.findtext("link") or "").strip(),
                    "source": "wwr"})
    return out


def _matches(job: dict, wanted: list[str], excluded: list[str]) -> bool:
    title = (job.get("title") or "").lower()
    if not title or not job.get("url"):
        return False
    if any(term in title for term in excluded):
        return False
    if not any(term in title for term in wanted):
        return False

    location = (job.get("location") or "").lower()
    if not location:
        # Only trusted from feeds that carry nothing but remote work.
        return job.get("source", "").split(":")[0] in _REMOTE_ONLY_SOURCES
    return any(word in location for word in _REMOTE_WORDS)


def _seen_path():
    return config.MEMORY_DIR / "jobs_seen.json"


def _load_seen() -> dict:
    try:
        data = json.loads(_seen_path().read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def remember(found: list[dict], now: Optional[datetime] = None) -> None:
    now = now or datetime.now(timezone.utc)
    horizon = now - timedelta(days=_SEEN_DAYS)
    kept = {}
    for url, stamp in _load_seen().items():
        try:
            if datetime.fromisoformat(stamp) >= horizon:
                kept[url] = stamp
        except (TypeError, ValueError):
            continue
    for job in found:
        kept[job["url"]] = now.isoformat()
    try:
        path = _seen_path()
        tmp = path.with_name(path.name + ".tmp")
        tmp.write_text(json.dumps(kept, indent=2), encoding="utf-8")
        os.replace(tmp, path)
    except OSError as e:
        logger.error("Could not write jobs_seen.json - %s: %s", type(e).__name__, e)


def _live_fetchers():
    sources = [
        ("remotive", lambda: _from_remotive(_get("https://remotive.com/api/remote-jobs?limit=100"))),
        ("remoteok", lambda: _from_remoteok(_get("https://remoteok.com/api"))),
    ]
    for feed in _WWR_FEEDS:
        sources.append((f"wwr:{feed}", lambda f=feed: _from_wwr(
            _get(f"https://weworkremotely.com/categories/{f}.rss", accept="application/rss+xml"))))
    for board in _GREENHOUSE_BOARDS:
        sources.append((f"greenhouse:{board}", lambda b=board: _from_greenhouse(
            _get(f"https://boards-api.greenhouse.io/v1/boards/{b}/jobs"), b)))
    return sources


def collect(now: Optional[datetime] = None, fetchers=None) -> list[dict]:
    """New matching postings, newest source first. Never raises."""
    now = now or datetime.now(timezone.utc)
    wanted, excluded = _criteria()
    if not wanted:
        logger.info("Job watch: no titles in job_search.md, nothing to look for")
        return []

    seen = set(_load_seen())
    found, by_url = [], set()
    for name, fetch in (fetchers if fetchers is not None else _live_fetchers()):
        try:
            postings = fetch()
        except Exception as e:
            # One source being down is ordinary. The others still report.
            logger.warning("Job watch: %s unavailable - %s: %s", name, type(e).__name__, e)
            continue
        for job in postings:
            url = job.get("url", "")
            if not url or url in seen or url in by_url:
                continue
            if not _matches(job, wanted, excluded):
                continue
            by_url.add(url)
            found.append(job)
    logger.info("Job watch: %d new matching postings", len(found))
    return found


def format_for_discord(found: list[dict]) -> str:
    lines = ["**New remote postings**"]
    for job in found[:_MAX_REPORTED]:
        company = f" - {job['company']}" if job.get("company") else ""
        lines.append(f"{job['title']}{company}\n{job['url']}")
    if len(found) > _MAX_REPORTED:
        lines.append(f"...and {len(found) - _MAX_REPORTED} more.")
    lines.append("Say the word on any of these and I will draft the application for you to send.")
    return "\n\n".join(lines)
