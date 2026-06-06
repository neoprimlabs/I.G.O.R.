import logging
import re
from typing import Awaitable, Callable, Optional

import anthropic
from apscheduler.schedulers.asyncio import AsyncIOScheduler

import config

logger = logging.getLogger(__name__)

_scheduler: Optional[AsyncIOScheduler] = None
_send_fn: Optional[Callable[[str], Awaitable[None]]] = None
_client: Optional[anthropic.AsyncAnthropic] = None
_setup_done: bool = False

_last_notified_model: Optional[str] = None

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


def setup(send_fn: Callable[[str], Awaitable[None]]) -> None:
    global _scheduler, _send_fn, _client, _setup_done
    if _setup_done:
        return
    _setup_done = True

    _send_fn = send_fn
    _client = anthropic.AsyncAnthropic(api_key=config.ANTHROPIC_API_KEY)
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
        logger.error("Model update check skipped - config.MODEL '%s' doesn't match expected sonnet pattern", config.MODEL)
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


_AI_NEWS_SYNTHESIS_PROMPT = """Summarize the following search results into 3-5 bullet points for a morning digest. Each bullet covers one distinct AI development.

Format:
- [Topic]: One sentence summary. Source: [URL]

Rules:
- Cover distinct topics - do not repeat similar stories
- Lead with the most significant development
- No emojis
- No em dashes - use plain hyphens
- No exclamation points
- Factual and precise"""


_WEATHER_SYNTHESIS_PROMPT = """Summarize the following weather search results into a brief forecast for a morning digest.

Format:
Current conditions and today's forecast in 2-3 sentences. Include high/low temperatures if available.

Rules:
- No emojis
- No em dashes - use plain hyphens
- No exclamation points
- Factual and precise"""


def _get_user_location() -> str | None:
    path = config.MEMORY_DIR / "user.md"
    if not path.exists():
        return None
    for line in path.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if stripped.lower().startswith("location:"):
            location = stripped[9:].strip()
            return location if location else None
    return None


async def _fetch_and_synthesize_weather() -> str | None:
    if _client is None:
        return None
    location = _get_user_location()
    if not location:
        logger.warning("Weather skipped - no location found in user.md")
        return None
    try:
        from agents import research
        results = await research._run_search(f"weather forecast {location} today", max_results=3)
        if not results:
            return None

        formatted = research._format_results(results)
        system_param = [{"type": "text", "text": _WEATHER_SYNTHESIS_PROMPT, "cache_control": {"type": "ephemeral"}}]
        response = await _client.messages.create(
            model=config.MODEL,
            system=system_param,
            messages=[{"role": "user", "content": f"Location: {location}\n\nSearch results:\n\n{formatted}"}],
            max_tokens=150,
        )
        return response.content[0].text
    except Exception as e:
        logger.error("Weather fetch failed - %s: %s", type(e).__name__, e)
        return None


async def _fetch_and_synthesize_ai_news() -> str | None:
    if _client is None:
        return None
    try:
        from agents import research
        results = await research._run_search("artificial intelligence news", max_results=5)
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
        system_param = [{"type": "text", "text": _AI_NEWS_SYNTHESIS_PROMPT, "cache_control": {"type": "ephemeral"}}]
        response = await _client.messages.create(
            model=config.MODEL,
            system=system_param,
            messages=[{"role": "user", "content": f"Search results:\n\n{formatted}"}],
            max_tokens=512,
        )
        return response.content[0].text
    except Exception as e:
        logger.error("AI news fetch failed - %s: %s", type(e).__name__, e)
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
        weather = await _fetch_and_synthesize_weather()
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
