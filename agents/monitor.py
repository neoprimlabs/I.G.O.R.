import asyncio
import logging
import re
from typing import Awaitable, Callable, Optional

import openai
from apscheduler.schedulers.asyncio import AsyncIOScheduler

import config

logger = logging.getLogger(__name__)

_scheduler: Optional[AsyncIOScheduler] = None
_send_fn: Optional[Callable[[str], Awaitable[None]]] = None
_client: Optional[openai.AsyncOpenAI] = None
_setup_done: bool = False

_last_notified_model: Optional[str] = None

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


def _get_digest_schedule() -> tuple[int, int]:
    """Read morning_digest time from schedule_config.md. Returns (hour, minute) UTC."""
    path = config.MEMORY_DIR / "schedule_config.md"
    if not path.exists():
        return 13, 0
    current_section = None
    for line in path.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if stripped.startswith("## "):
            current_section = stripped[3:].strip()
        elif current_section == "morning_digest" and stripped.lower().startswith("time:"):
            time_str = stripped[5:].strip().split()[0]
            parts = time_str.split(":")
            if len(parts) == 2:
                try:
                    return int(parts[0]), int(parts[1])
                except ValueError:
                    pass
    return 13, 0


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
            response = await _client.chat.completions.create(
                model=config.MODEL,
                messages=[
                    {"role": "system", "content": _VIDEO_SUMMARY_PROMPT},
                    {"role": "user", "content": f"Video: {title}\n\nTranscript:\n{transcript}"},
                ],
                max_tokens=1024,
            )
            summary = response.choices[0].message.content or ""
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
    _scheduler = AsyncIOScheduler()

    digest_hour, digest_minute = _get_digest_schedule()
    _scheduler.add_job(_morning_digest, "cron", hour=digest_hour, minute=digest_minute, id="morning_digest")
    logger.info("Morning digest scheduled at %02d:%02d UTC", digest_hour, digest_minute)

    _scheduler.add_job(_check_model_update, "cron", day_of_week="mon", hour=9, minute=0, id="model_update_check")

    _scheduler.start()
    logger.info("Monitor scheduler started")


def _parse_sonnet_version(model_id: str) -> Optional[tuple[int, int]]:
    m = re.match(r"^claude-sonnet-(\d+)-(\d+)$", model_id)
    return (int(m.group(1)), int(m.group(2))) if m else None


async def _check_model_update() -> None:
    global _last_notified_model
    if _send_fn is None or _client is None:
        return

    current_version = _parse_sonnet_version(config.MODEL)
    if current_version is None:
        logger.info("Model update check skipped - config.MODEL '%s' doesn't match expected sonnet pattern", config.MODEL)
        return

    try:
        response = await _client.models.list()
        sonnet_candidates: list[tuple[tuple[int, int], str]] = []
        for model in response.data:
            version = _parse_sonnet_version(model.id)
            if version:
                sonnet_candidates.append((version, model.id))

        if not sonnet_candidates:
            return

        latest_version, latest_id = max(sonnet_candidates, key=lambda x: x[0])

        if latest_version > current_version and latest_id != _last_notified_model:
            _last_notified_model = latest_id
            await _send_fn(
                f"**Model Update Available**\n"
                f"Newer Sonnet available: `{latest_id}`\n"
                f"Current: `{config.MODEL}`\n"
                f"Update system_config.md via ProdMem and restart to apply."
            )

    except Exception as e:
        logger.error("Model update check failed - %s: %s", type(e).__name__, e)


_AI_NEWS_SYNTHESIS_PROMPT = """Summarize the following search results into exactly 3 bullet points for a morning digest. Each bullet is one sentence covering one distinct AI development.

Format:
- [Topic]: One sentence. Source: [URL]

Rules:
- Exactly 3 bullets - no more, no fewer
- Cover distinct topics - do not repeat similar stories
- Lead with the most significant development
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


async def _fetch_and_synthesize_ai_news() -> str | None:
    if _client is None:
        return None
    try:
        from datetime import datetime, timedelta
        from agents import research
        cutoff = (datetime.utcnow() - timedelta(days=7)).strftime("%Y-%m-%dT%H:%M:%SZ")
        results = await research._run_search("artificial intelligence news", max_results=5, start_published_date=cutoff)
        if not results:
            return None

        seen_domains: set[str] = set()
        unique_results = []
        for r in results:
            url = r.get("url", "")
            parts = url.split("/")
            domain = parts[2] if len(parts) > 2 else ""
            if domain and domain not in seen_domains:
                seen_domains.add(domain)
                unique_results.append(r)

        if not unique_results:
            return None

        formatted = research._format_results(unique_results)
        response = await _client.chat.completions.create(
            model=config.MODEL,
            messages=[
                {"role": "system", "content": _AI_NEWS_SYNTHESIS_PROMPT},
                {"role": "user", "content": f"Search results:\n\n{formatted}"},
            ],
            max_tokens=1536,
        )
        return response.choices[0].message.content or ""
    except Exception as e:
        logger.error("AI news fetch failed - %s: %s", type(e).__name__, e)
        return None


_UNREAL_NEWS_SYNTHESIS_PROMPT = """Summarize the following search results into exactly 1 bullet point covering the most relevant recent Unreal Engine news for a morning digest.

Format:
- [Topic]: One sentence. Source: [URL]

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
        results = await research._run_search("Unreal Engine news update", max_results=3, start_published_date=cutoff)
        if not results:
            return None

        formatted = research._format_results(results)
        response = await _client.chat.completions.create(
            model=config.MODEL,
            messages=[
                {"role": "system", "content": _UNREAL_NEWS_SYNTHESIS_PROMPT},
                {"role": "user", "content": f"Search results:\n\n{formatted}"},
            ],
            max_tokens=1024,
        )
        return response.choices[0].message.content or ""
    except Exception as e:
        logger.error("Unreal news fetch failed - %s: %s", type(e).__name__, e)
        return None


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
    return [line.strip() for line in content.splitlines() if line.strip().startswith("- [ ]")]


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
