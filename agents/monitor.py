import asyncio
import json
import logging
import os
import re
from datetime import datetime, timedelta, timezone
from typing import Awaitable, Callable, Optional

import openai
from apscheduler.schedulers.asyncio import AsyncIOScheduler

import config
import llm
import sanitize

logger = logging.getLogger(__name__)

_scheduler: Optional[AsyncIOScheduler] = None
_send_fn: Optional[Callable[[str], Awaitable[None]]] = None
_client: Optional[openai.AsyncOpenAI] = None
_setup_done: bool = False

_BRIDGEMIND_CHANNEL_ID = "UCwaTGE53GLGC3fDClVl_7TA"
_BRIDGEMIND_RSS = f"https://www.youtube.com/feeds/videos.xml?channel_id={_BRIDGEMIND_CHANNEL_ID}"

_VIDEO_SUMMARY_PROMPT = """Summarize the following YouTube video transcript for a developer's morning briefing.

Format:
- 3-5 bullet points covering the key ideas, tools, or techniques discussed
- One sentence at the end on why it's worth watching

Rules:
- No emojis
- No em dashes - use plain hyphens
- No exclamation points
- Factual and precise"""

_DEFAULT_SYSTEM_PROMPT = """You are I.G.O.R.'s Monitor agent - proactive system monitoring and scheduled reporting.

Your primary function is scheduled reports that run automatically, not reactive responses to queries.

When queried directly, report:
- Scheduler status (running / not running, next scheduled jobs)
- Current watchlist (what is being monitored)
- Any system health issues you're aware of

IMPORTANT CONSTRAINTS:
- You cannot reschedule jobs at runtime. Schedules are read from schedule_config.md at startup. To change a schedule, tell the user to update schedule_config.md via ProdMem, then restart I.G.O.R.
- Do not invent action formats. You have no write capabilities.

Be direct and specific. If there's nothing to flag, say so.

Style:
- No emojis
- No em dashes - use plain hyphens
- No exclamation points
- No casual filler phrases ("Sure!", "Of course!", "Happy to help!")"""


def _get_system_prompt() -> str:
    path = config.MEMORY_DIR / "prompt_monitor.md"
    if path.exists():
        content = path.read_text(encoding="utf-8").strip()
        if content:
            return content
    return _DEFAULT_SYSTEM_PROMPT


def _parse_digest_schedule(content: str) -> Optional[tuple[int, int]]:
    """Extract (hour, minute) UTC from schedule_config.md content, or None.

    Split out from _get_digest_schedule so ConfigEdit can check a proposed file
    against the real parser before writing it. Validating against a second copy of
    these rules would just create two things that can drift apart.
    """
    current_section = None
    for line in content.splitlines():
        stripped = line.strip()
        if stripped.startswith("## "):
            current_section = stripped[3:].strip()
        elif current_section == "morning_digest" and stripped.lower().startswith("time:"):
            tokens = stripped[5:].strip().split()
            if not tokens:
                continue
            parts = tokens[0].split(":")
            if len(parts) != 2:
                continue
            try:
                hour, minute = int(parts[0]), int(parts[1])
            except ValueError:
                continue
            if 0 <= hour <= 23 and 0 <= minute <= 59:
                return hour, minute
    return None


def _get_digest_schedule() -> tuple[int, int]:
    """Read morning_digest time from schedule_config.md. Returns (hour, minute) UTC."""
    path = config.MEMORY_DIR / "schedule_config.md"
    if not path.exists():
        return 13, 0
    return _parse_digest_schedule(path.read_text(encoding="utf-8")) or (13, 0)


def _get_watchlist() -> list[str]:
    path = config.MEMORY_DIR / "watchlist.md"
    if not path.exists():
        return ["Morning digest delivery", "Model update availability (weekly)", "System health"]
    return [
        line.strip()[2:].strip()
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip().startswith("- ")
    ]


async def _check_bridgemind_videos() -> None:
    if _send_fn is None or _client is None:
        return
    try:
        import urllib.request
        import xml.etree.ElementTree as ET

        loop = asyncio.get_running_loop()

        def _fetch_rss() -> bytes:
            with urllib.request.urlopen(_BRIDGEMIND_RSS, timeout=10) as r:
                return r.read()

        data = await loop.run_in_executor(None, _fetch_rss)
        root = ET.fromstring(data)
        ns = {
            "atom": "http://www.w3.org/2005/Atom",
            "yt": "http://www.youtube.com/xml/schemas/2015",
        }

        entries = root.findall("atom:entry", ns)
        if not entries:
            return

        latest = entries[0]
        video_id = latest.find("yt:videoId", ns).text
        title = latest.find("atom:title", ns).text
        video_url = f"https://www.youtube.com/watch?v={video_id}"

        seen_path = config.MEMORY_DIR / "bridgemind_seen.txt"
        if not seen_path.exists():
            seen_path.write_text(video_id, encoding="utf-8")
            return

        if seen_path.read_text(encoding="utf-8").strip() == video_id:
            return

        seen_path.write_text(video_id, encoding="utf-8")

        def _get_transcript() -> str:
            from youtube_transcript_api import YouTubeTranscriptApi
            entries = YouTubeTranscriptApi.get_transcript(video_id)
            return " ".join(e["text"] for e in entries)[:4000]

        try:
            transcript = await loop.run_in_executor(None, _get_transcript)
            summary = await llm.complete(
                _client,
                config.MODELS["summary"],
                _VIDEO_SUMMARY_PROMPT,
                f"Video: {title}\n\nTranscript:\n{transcript}",
                max_tokens=1024,
                label="Video summary",
            )
            await _send_fn(f"**New BridgeMind Video**\n{title}\n{video_url}\n\n{summary}")
        except Exception as e:
            logger.error("BridgeMind transcript failed for %s - %s: %s", video_id, type(e).__name__, e)
            await _send_fn(f"**New BridgeMind Video**\n{title}\n{video_url}")

    except Exception as e:
        logger.error("BridgeMind check failed - %s: %s", type(e).__name__, e)


def setup(send_fn: Callable[[str], Awaitable[None]]) -> None:
    global _scheduler, _send_fn, _client, _setup_done
    if _setup_done:
        return
    _setup_done = True

    _send_fn = send_fn
    _client = openai.AsyncOpenAI(
        api_key=config.GROQ_API_KEY,
        base_url="https://api.groq.com/openai/v1",
    )
    # APScheduler drops a job more than misfire_grace_time late, default 1 second,
    # with only a WARNING. Measured on this host 2026-09-12: a live tick was missed
    # by 1.198s after a restart. Nothing has been lost yet - 25 of 25 digests ran
    # since 2026-08-18 - but a stall at 13:00 would cost that day's digest silently.
    # Five minutes late beats not at all for every job here.
    _scheduler = AsyncIOScheduler(job_defaults={"misfire_grace_time": 300})

    digest_hour, digest_minute = _get_digest_schedule()
    _scheduler.add_job(_morning_digest, "cron", hour=digest_hour, minute=digest_minute, id="morning_digest")
    logger.info("Morning digest scheduled at %02d:%02d UTC", digest_hour, digest_minute)

    # Daily, not weekly, and once on every start. Groq removed the whole Llama
    # family on 2026-08-17 and the weekly check meant four configured roles were
    # 404ing for a day before anything said so. The startup run is the one that
    # matters: it turns a deprecation into an alert on the next deploy or restart
    # rather than whenever the cron next comes round. One models.list call, no
    # token cost, and it is the cheapest check in the system.
    _scheduler.add_job(_check_model_update, "cron", hour=9, minute=0, id="model_update_check")
    _scheduler.add_job(
        _check_model_update,
        "date",
        run_date=datetime.now(timezone.utc) + timedelta(seconds=60),
        id="model_update_startup",
    )

    # Polled rather than one date job per message: APScheduler drops a date job more
    # than a second late, so anything due during a restart would be lost. No tokens.
    _scheduler.add_job(_deliver_scheduled, "interval", seconds=60, id="scheduled_messages")

    # Presence decides whether to speak on its own. Ten minutes rather than sixty
    # because a lull is a 25-90 minute window and a coarser tick would step over it.
    # The gates and the daily call budget bound the cost, not the tick rate: most
    # ticks return before any model call, and while it is switched off all of them do.
    _scheduler.add_job(_presence_tick, "interval", minutes=10, id="presence")

    # 15:00 UTC keeps it clear of the 13:00 digest, and it runs on the chat model's
    # 12000 bucket rather than the digest's 6000, so the two cannot contend.
    _scheduler.add_job(_weekly_advocacy_draft, "cron", day_of_week="mon", hour=15, minute=0, id="advocacy_draft")
    logger.info("Advocacy draft scheduled Mondays at 15:00 UTC (%s)", _ADVOCACY["topic"])

    _scheduler.start()
    logger.info("Monitor scheduler started")


async def _deliver_scheduled() -> None:
    if _send_fn is None:
        return
    from agents import scheduled
    await scheduled.deliver_due(_send_fn)


async def _presence_tick() -> None:
    if _send_fn is None:
        return
    from agents import presence
    await presence.tick(_send_fn)


async def _check_model_update() -> None:
    if _send_fn is None or _client is None:
        return
    try:
        response = await _client.models.list()
        available = {m.id for m in response.data}
        missing = sorted({m for m in config.MODELS.values() if m not in available})
        if missing:
            await _send_fn(
                f"**Model Alert**\n"
                f"These configured models are no longer available on Groq: "
                f"{', '.join(f'`{m}`' for m in missing)}. They may have been deprecated. "
                f"Pick replacements at https://console.groq.com/docs/models, update "
                f"config.MODELS in config.py, then restart."
            )
    except Exception as e:
        logger.error("Model availability check failed - %s: %s", type(e).__name__, e)


# 500 chars of an article is page chrome, not prose. Measured on a real result:
# title, byline, "Skip to content", "Toggle Navigation", a nav link, and nothing
# else. 1500 gets past the furniture into the lede. Budget: 5 results x 1500 =
# ~2150 tokens on the 6000 TPM summary bucket, leaving room for max_tokens 1536.
_NEWS_SNIPPET_CHARS = 1500

_AI_NEWS_SYNTHESIS_PROMPT = """Summarize the following search results into exactly 3 bullet points for a morning digest. Each bullet is one sentence covering one distinct AI development.

Format:
- [Topic]: One sentence. Source: [2]

End each bullet with "Source: [n]", where n is the bracketed number of the result you used. Just the number. Do not write the URL, the site name, or the article title.

Rules:
- Exactly 3 bullets - no more, no fewer
- Cover distinct topics - do not repeat similar stories
- Lead with the most significant development
- Stay inside what the result actually says. Do not add detail it does not contain, and do not resolve an unfamiliar or technical claim into a more familiar one - keep each story in the field its source puts it in.
- Reuse the source's own wording for the key nouns rather than substituting your own
- If a result is too thin to summarise honestly, use its title rather than inventing detail
- No emojis
- No em dashes - use plain hyphens
- No exclamation points
- Factual and precise"""


_WMO_CONDITIONS: dict[int, str] = {
    0: "Clear", 1: "Mostly clear", 2: "Partly cloudy", 3: "Overcast",
    45: "Foggy", 48: "Foggy",
    51: "Light drizzle", 53: "Drizzle", 55: "Heavy drizzle",
    61: "Light rain", 63: "Rain", 65: "Heavy rain",
    71: "Light snow", 73: "Snow", 75: "Heavy snow",
    80: "Rain showers", 81: "Rain showers", 82: "Heavy showers",
    95: "Thunderstorms", 96: "Thunderstorms", 99: "Thunderstorms",
}


def _get_user_location() -> str | None:
    path = config.MEMORY_DIR / "user.md"
    if not path.exists():
        return None
    in_location_section = False
    for line in path.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if stripped.lower().startswith("location:"):
            location = stripped[9:].strip()
            return location if location else None
        if stripped.lower() == "## location":
            in_location_section = True
            continue
        if in_location_section:
            if stripped.startswith("## "):
                break
            if stripped.startswith("- "):
                location = stripped[2:].strip()
                return location if location else None
    return None


async def _fetch_weather() -> str | None:
    location = _get_user_location()
    if not location:
        logger.warning("Weather skipped - no location found in user.md")
        return None

    loop = asyncio.get_running_loop()

    def _sync() -> str | None:
        import json
        import urllib.parse
        import urllib.request

        city = location.split(",")[0].strip()
        geo_url = (
            f"https://geocoding-api.open-meteo.com/v1/search"
            f"?name={urllib.parse.quote(city)}&count=1"
        )
        with urllib.request.urlopen(geo_url, timeout=10) as r:
            geo = json.loads(r.read())
        if not geo.get("results"):
            return None

        lat = geo["results"][0]["latitude"]
        lon = geo["results"][0]["longitude"]

        weather_url = (
            f"https://api.open-meteo.com/v1/forecast"
            f"?latitude={lat}&longitude={lon}"
            f"&daily=weathercode,temperature_2m_max,temperature_2m_min"
            f"&temperature_unit=fahrenheit"
            f"&forecast_days=2"
            f"&timezone=America%2FNew_York"
        )
        with urllib.request.urlopen(weather_url, timeout=10) as r:
            data = json.loads(r.read())

        daily = data["daily"]
        codes = daily["weathercode"]
        highs = daily["temperature_2m_max"]
        lows = daily["temperature_2m_min"]

        def _line(code: int, high: float, low: float) -> str:
            condition = _WMO_CONDITIONS.get(code, "Mixed conditions")
            return f"{condition}, {round(high)}F / {round(low)}F"

        today = _line(codes[0], highs[0], lows[0])
        tomorrow = _line(codes[1], highs[1], lows[1])
        return f"Today: {today}\nTomorrow: {tomorrow}"

    try:
        return await loop.run_in_executor(None, _sync)
    except Exception as e:
        logger.error("Weather fetch failed - %s: %s", type(e).__name__, e)
        return None


_SOURCE_INDEX_RE = re.compile(r"Source:\s*\[?(\d+)\]?\.?\s*$", re.IGNORECASE | re.MULTILINE)


def _attach_sources(text: str, results: list[dict]) -> str:
    """Turn the model's source index into the real URL.

    The model is asked for "Source: [n]" against the numbered results it was handed,
    and code substitutes the URL. Asking it to copy the URL itself worked until
    2026-08-10, when llama-3.1-8b silently began writing publication names instead -
    same prompt, same input, same code path, reproducible. Nothing verified the copy,
    so the digest shipped with no links at all.

    A URL is data. Retyping it was never the model's job; picking which result a
    sentence came from is. One digit is the smallest thing that can be asked for and
    the only part code cannot infer.
    """
    urls = [r.get("url", "") for r in results]

    def _swap(m: re.Match) -> str:
        i = int(m.group(1)) - 1
        if not 0 <= i < len(urls):
            logger.warning("Digest cited result %s but only %d were given", m.group(1), len(urls))
        elif not urls[i]:
            logger.warning("Digest cited result %s, which has no URL", m.group(1))
        else:
            return f"Source: {urls[i]}"
        return ""

    out = _SOURCE_INDEX_RE.sub(_swap, text)

    # Checked per bullet, not per line: the model often puts "Source:" on its own
    # line, and a per-line check called every healthy bullet a failure. A warning
    # that fires every morning is one nobody reads on the morning it is real.
    for block in re.split(r"\n(?=\s*-\s)", out):
        if block.strip().startswith("-") and "http" not in block:
            logger.warning("Digest bullet came back with no usable source: %s", block.strip()[:90])
    return out


# "artificial intelligence news" returns whatever the cycle is chewing on. Measured
# over the six digests in context.db on 2026-09-17: five of six were dominated by
# safety discourse - slowdown calls, a hoax claim, a rogue-agent warning. The user:
# "I'm sick of seeing 3 headlines on ai safety every morning. They are not
# productive." This asks for the things they can act on instead.
_NEWS_QUERY = "new AI model releases, developer tools, and technical research results"

# Long enough that a story cannot come back while it is still the same cycle, short
# enough that the file does not grow forever or block a genuine follow-up.
_NEWS_SEEN_DAYS = 14

# Words that would match every story ever fetched, so they cannot serve as filters.
_GENERIC_EXCLUSION_WORDS = frozenset({
    "news", "story", "stories", "article", "articles", "coverage", "content",
    "focused", "commentary", "calls", "about", "and", "the", "for", "with",
    "ai", "artificial", "intelligence", "model", "models",
})


def _parse_exclusions(text: str) -> list[str]:
    """Terms from digest_config.md's Exclusions section.

    That section has existed since July annotated "pending code implementation", so
    everything listed in it was silently ignored. This is the implementation.
    """
    terms: list[str] = []
    in_section = False
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.lower().startswith("## "):
            in_section = stripped.lower().startswith("## exclusion")
            continue
        if not in_section or not stripped.startswith("- "):
            continue
        body = re.sub(r"\([^)]*\)", " ", stripped[2:])
        for word in re.findall(r"[A-Za-z]+", body.lower()):
            if len(word) > 2 and word not in _GENERIC_EXCLUSION_WORDS and word not in terms:
                terms.append(word)
    return terms


def _digest_exclusions() -> list[str]:
    """Exclusion terms from digest_config.md, or none if it cannot be read.

    Reads the file here rather than borrowing prod_memory._read_config: that name
    does not exist in this module, and py_compile cannot see a missing name inside a
    function, so the first version of this raised NameError only when the digest ran.
    """
    try:
        text = (config.MEMORY_DIR / "digest_config.md").read_text(encoding="utf-8")
    except OSError:
        return []
    return _parse_exclusions(text)


def _seen_path():
    return config.MEMORY_DIR / "news_seen.json"


def _load_seen_news() -> dict:
    try:
        data = json.loads(_seen_path().read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def _remember_news(urls, now=None) -> None:
    from datetime import datetime, timedelta, timezone as _tz
    now = now or datetime.now(_tz.utc)
    horizon = now - timedelta(days=_NEWS_SEEN_DAYS)
    seen = _load_seen_news()
    kept = {}
    for url, stamp in seen.items():
        try:
            if datetime.fromisoformat(stamp) >= horizon:
                kept[url] = stamp
        except (TypeError, ValueError):
            continue
    for url in urls:
        kept[url] = now.isoformat()
    try:
        path = _seen_path()
        tmp = path.with_name(path.name + ".tmp")
        tmp.write_text(json.dumps(kept, indent=2), encoding="utf-8")
        os.replace(tmp, path)
    except OSError as e:
        logger.error("Could not write news_seen.json - %s: %s", type(e).__name__, e)


def _filter_news_results(results, seen_urls, exclusions):
    """Drop what was already sent, what the user excluded, and repeat domains."""
    kept = []
    domains: set[str] = set()
    for r in results:
        url = r.get("url", "")
        if not url or url in seen_urls:
            continue
        # Title and summary only. Matching the full article text would drop a model
        # release that mentions safety once in passing, which is a story the user
        # does want.
        haystack = " ".join(str(r.get(k, "") or "") for k in ("title", "summary")).lower()
        if any(re.search(rf"\b{re.escape(term)}\b", haystack) for term in exclusions):
            continue
        parts = url.split("/")
        domain = parts[2] if len(parts) > 2 else ""
        if domain and domain in domains:
            continue
        if domain:
            domains.add(domain)
        kept.append(r)
    return kept


async def _fetch_and_synthesize_ai_news() -> str | None:
    if _client is None:
        return None
    try:
        from datetime import datetime, timedelta
        from agents import research
        cutoff = (datetime.utcnow() - timedelta(days=7)).strftime("%Y-%m-%dT%H:%M:%SZ")
        # Eight, not five: dedup and exclusions need material to work with, and only
        # the surviving five reach the model, so the token budget is unchanged.
        results = await research._run_search(_NEWS_QUERY, max_results=8,
                                             start_published_date=cutoff, use_summary=True)
        if not results:
            return None

        exclusions = _digest_exclusions()
        unique_results = _filter_news_results(results, set(_load_seen_news()), exclusions)[:5]

        if not unique_results:
            # Better a digest with no AI section than the same stories again.
            logger.info("AI news: every result was already sent or excluded (%d fetched, "
                        "exclusions: %s)", len(results), ", ".join(exclusions) or "none")
            return None

        formatted = research._format_results(unique_results)
        synthesis = await llm.complete(
            _client,
            config.MODELS["summary"],
            _AI_NEWS_SYNTHESIS_PROMPT,
            f"Search results:\n\n{formatted}",
            max_tokens=1536,
            label="AI news",
        )
        _remember_news([r.get("url", "") for r in unique_results if r.get("url")])
        return _attach_sources(synthesis, unique_results)
    except Exception as e:
        logger.error("AI news fetch failed - %s: %s", type(e).__name__, e)
        return None


_UNREAL_NEWS_SYNTHESIS_PROMPT = """Summarize the following search results into exactly 1 bullet point covering the most relevant recent Unreal Engine news for a morning digest.

Format:
- [Topic]: One sentence. Source: [2]

End the bullet with "Source: [n]", where n is the bracketed number of the result you used. Just the number. Do not write the URL, the site name, or the article title.

Rules:
- Exactly 1 bullet - the single most relevant update
- Focus on engine updates, new features, or notable releases
- No emojis
- No em dashes - use plain hyphens
- No exclamation points
- Factual and precise"""


async def _fetch_and_synthesize_unreal_news() -> str | None:
    if _client is None:
        return None
    try:
        from datetime import datetime, timedelta
        from agents import research
        cutoff = (datetime.utcnow() - timedelta(days=7)).strftime("%Y-%m-%dT%H:%M:%SZ")
        results = await research._run_search("Unreal Engine news update", max_results=3,
                                             start_published_date=cutoff, use_summary=True)
        if not results:
            return None

        formatted = research._format_results(results)
        synthesis = await llm.complete(
            _client,
            config.MODELS["summary"],
            _UNREAL_NEWS_SYNTHESIS_PROMPT,
            f"Search results:\n\n{formatted}",
            max_tokens=1024,
            label="Unreal news",
        )
        return _attach_sources(synthesis, results)
    except Exception as e:
        logger.error("Unreal news fetch failed - %s: %s", type(e).__name__, e)
        return None


# Topic and stance live here, in code, because that is genuinely how scheduled work
# is configured - jobs are registered in monitor.setup(), and changing one is a code
# change and a deploy. IGOR once told the user this was editable from a scheduler.yaml
# that has never existed. Changing the topic means editing this block.
_ADVOCACY = {
    "topic": "Universal Basic Income",
    "query": "universal basic income pilot results study poverty",
    "stance": (
        "Basic needs are going unmet in the United States, and a guaranteed income "
        "floor is a serious policy response to that, not a fringe idea."
    ),
}

_ADVOCACY_DRAFT_PROMPT = """You draft a short advocacy post for a human to review, edit, and publish under their own name.

Write ONE post of roughly 150 words that:
- opens with the specific finding or event in the results, not a generalisation
- makes the case for the position below in plain language a non-specialist can follow
- ends with one concrete call to action
- ends with a final line reading "Source: [n]", where n is the bracketed number of the result you drew on

Position: {stance}

Rules:
- Every factual claim, number, place, date and study in the post must come from the results in front of you. If it is not there, do not write it.
- Do not invent studies, statistics, pilot outcomes, or quotes. Do not round a number up. A weaker post that is true beats a stronger one that is not, because a human is going to put their name on this.
- Argue for the position, but let the sourced facts carry the argument.
- If the results do not support a post worth publishing, reply with exactly: NOTHING WORTH DRAFTING
- Do not write the URL or the publication name anywhere. Just the number.

Style:
- No emojis
- No em dashes - use plain hyphens
- No exclamation points
- No casual filler phrases ("Sure!", "Of course!", "Happy to help!")"""


def _append_draft(text: str) -> None:
    """Keep a dated record of every draft on disk.

    Deliberately NOT added to react's memory_read allowlist, for the same reason
    corrections.md is not: this file holds model output derived from web search
    results, and letting an agent pull it back into a tool-bearing context is a
    stored injection path. The user reads drafts in Discord or in the synced file.
    """
    from datetime import datetime, timezone
    stamp = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    path = config.MEMORY_DIR / "drafts.md"
    with path.open("a", encoding="utf-8") as handle:
        handle.write(f"\n## {_ADVOCACY['topic']} - {stamp}\n\n{text}\n")


async def _weekly_advocacy_draft() -> None:
    if _send_fn is None or _client is None:
        return
    try:
        from datetime import datetime, timedelta
        from agents import research
        cutoff = (datetime.utcnow() - timedelta(days=14)).strftime("%Y-%m-%dT%H:%M:%SZ")
        results = await research._run_search(_ADVOCACY["query"], max_results=5,
                                             start_published_date=cutoff, use_summary=True)
        if not results:
            logger.info("Advocacy draft: no recent results for %s", _ADVOCACY["topic"])
            return

        formatted = research._format_results(results)
        draft = await llm.complete(
            _client,
            config.MODELS["chat"],
            _ADVOCACY_DRAFT_PROMPT.format(stance=_ADVOCACY["stance"]),
            f"Search results:\n\n{formatted}",
            max_tokens=1024,
            label="Advocacy draft",
        )
        if not draft or "NOTHING WORTH DRAFTING" in draft.upper():
            logger.info("Advocacy draft: nothing worth drafting from this week's results")
            return

        draft = sanitize.clean(_attach_sources(draft, results))
        _append_draft(draft)
        await _send_fn(
            f"**Draft for review - {_ADVOCACY['topic']}**\n\n{draft}\n\n"
            "Nothing has been posted anywhere. IGOR has no publishing accounts. "
            "Edit this however you want and post it yourself."
        )
    except Exception as e:
        logger.error("Advocacy draft failed - %s: %s", type(e).__name__, e)


def _get_digest_sections() -> list[str]:
    path = config.MEMORY_DIR / "digest_config.md"
    if not path.exists():
        return ["tasks"]
    sections = [
        line.strip()[2:].strip()
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip().startswith("- ")
    ]
    return sections if sections else ["tasks"]


def _parse_tasks(content: str) -> list[str]:
    """Any bullet is a task, checkbox or not.

    This matched only "- [ ]" while memory_write writes plain "- " bullets, so every
    digest reported "Open Tasks: None" with three tasks sitting in the file. The
    writer and the reader disagreed and nothing could notice.
    """
    tasks = []
    for line in content.splitlines():
        stripped = line.strip()
        if not stripped.startswith("- "):
            continue
        body = stripped[2:].strip()
        if body[:3].lower() == "[x]":
            continue
        if body.startswith("[ ]"):
            body = body[3:].strip()
        if body:
            tasks.append("- " + body)
    return tasks


def _parse_projects(content: str) -> list[str]:
    projects = []
    current_name = None
    for line in content.splitlines():
        stripped = line.strip()
        if stripped.startswith("## "):
            current_name = stripped[3:]
        elif current_name and stripped and not stripped.startswith("#"):
            projects.append(f"{current_name}: {stripped.lstrip('- ')}")
            current_name = None
    return projects


async def _morning_digest() -> None:
    if _send_fn is None:
        return

    # Presence may follow up on the digest later, so it needs to know one went out.
    from agents import presence

    sections = _get_digest_sections()
    lines = ["**Morning Digest**", ""]

    if "tasks" in sections:
        tasks_path = config.MEMORY_DIR / "tasks.md"
        if tasks_path.exists():
            tasks = _parse_tasks(tasks_path.read_text(encoding="utf-8"))
            lines.append("**Open Tasks:**")
            if tasks:
                seen = set()
                for t in tasks:
                    if t not in seen:
                        seen.add(t)
                        lines.append(t)
            else:
                lines.append("None")
            lines.append("")

    if "projects" in sections:
        projects_path = config.MEMORY_DIR / "projects.md"
        if projects_path.exists():
            projects = _parse_projects(projects_path.read_text(encoding="utf-8"))
            lines.append("**Active Projects:**")
            if projects:
                seen = set()
                for p in projects:
                    if p not in seen:
                        seen.add(p)
                        lines.append(f"- {p}")
            else:
                lines.append("None")
            lines.append("")

    if "daily_forecast" in sections:
        lines.append("**Weather:**")
        weather = await _fetch_weather()
        if weather:
            lines.append(weather)
        else:
            lines.append("No weather data available.")
        lines.append("")

    if "ai_news" in sections:
        lines.append("**AI News:**")
        ai_news = await _fetch_and_synthesize_ai_news()
        if ai_news:
            lines.append(ai_news)
        else:
            lines.append("No results available.")
        lines.append("")

    if "unreal_news" in sections:
        lines.append("**Unreal Engine:**")
        unreal_news = await _fetch_and_synthesize_unreal_news()
        if unreal_news:
            lines.append(unreal_news)
        else:
            lines.append("No results available.")
        lines.append("")

    try:
        await _send_fn("\n".join(lines))
    except Exception as e:
        logger.error("Morning digest send failed - %s: %s", type(e).__name__, e)


async def handle(
    message: str,
    context: list[dict],
    call_claude: Callable[..., Awaitable[str]],
) -> str:
    if any(word in message.lower() for word in ("trigger", "run digest", "send digest")):
        await _morning_digest()
        return "Morning digest triggered."

    status_lines = []

    if _scheduler and _scheduler.running:
        jobs = _scheduler.get_jobs()
        if jobs:
            job_lines = [f"  - {j.id}: next run {j.next_run_time}" for j in jobs]
            status_lines.append("Scheduler: running")
            status_lines.append("Scheduled jobs:\n" + "\n".join(job_lines))
        else:
            status_lines.append("Scheduler: running (no jobs registered)")
    else:
        status_lines.append("Scheduler: not running")

    watchlist = _get_watchlist()
    status_lines.append("Watchlist:\n" + "\n".join(f"  - {w}" for w in watchlist))

    status_block = "\n".join(status_lines)
    system = _get_system_prompt() + f"\n\nCurrent status:\n{status_block}"
    messages = context + [{"role": "user", "content": message}]
    return await call_claude(system, messages)
