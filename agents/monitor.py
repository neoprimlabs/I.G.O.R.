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

# Tracks the last model ID we already notified about, so the user isn't
# pinged every week for the same available update.
_last_notified_model: Optional[str] = None

_SYSTEM_PROMPT = """You are I.G.O.R.'s Monitor agent - proactive system monitoring and scheduled reporting.

Your primary function is scheduled reports that run automatically, not reactive responses to queries.

When queried directly, report:
- Scheduler status (running / not running, next scheduled jobs)
- Any system health issues you're aware of
- What the monitor is currently watching

IMPORTANT CONSTRAINTS:
- You cannot reschedule, add, or modify jobs at runtime. Job schedules are defined in code. If asked to change a schedule, tell the user clearly that it requires a code change and cannot be done through conversation.
- Do not invent action formats like %%SCHEDULE%% or similar. You have no write capabilities.

Be direct and specific. If there's nothing to flag, say so."""


def setup(send_fn: Callable[[str], Awaitable[None]]) -> None:
    """Initialize the APScheduler and register scheduled jobs.

    Called once on Discord bot on_ready. Guard ensures jobs aren't duplicated
    on reconnect.
    """
    global _scheduler, _send_fn, _client, _setup_done
    if _setup_done:
        return
    _setup_done = True

    _send_fn = send_fn
    _client = anthropic.AsyncAnthropic(api_key=config.ANTHROPIC_API_KEY)
    _scheduler = AsyncIOScheduler()

    # Morning digest - 08:00 UTC daily
    _scheduler.add_job(_morning_digest, "cron", hour=8, minute=0, id="morning_digest")

    # Sonnet model update check - 09:00 UTC every Monday
    # One lightweight API call per week; new Sonnet releases are infrequent.
    _scheduler.add_job(_check_model_update, "cron", day_of_week="mon", hour=9, minute=0, id="model_update_check")

    _scheduler.start()
    logger.info("Monitor scheduler started - morning digest 08:00 UTC daily, model check 09:00 UTC Mondays")


def _parse_sonnet_version(model_id: str) -> Optional[tuple[int, int]]:
    """Return (major, minor) for claude-sonnet-X-Y model IDs, or None."""
    m = re.match(r"^claude-sonnet-(\d+)-(\d+)$", model_id)
    return (int(m.group(1)), int(m.group(2))) if m else None


async def _check_model_update() -> None:
    """Check Anthropic's model list for a newer Sonnet than config.MODEL.

    Notification only - never auto-updates. Fires at most once per newly
    discovered model ID to avoid repeat pings on the same available update.
    """
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
                f"Update `MODEL` in `config.py` when ready - I.G.O.R. will not update automatically."
            )

    except Exception as e:
        logger.error("Model update check failed - %s: %s", type(e).__name__, e)


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

    lines = ["**Morning Digest**", ""]

    tasks_path = config.MEMORY_DIR / "tasks.md"
    if tasks_path.exists():
        tasks = _parse_tasks(tasks_path.read_text(encoding="utf-8"))
        lines.append("**Open Tasks:**")
        if tasks:
            # Deduplicate while preserving order
            seen = set()
            for t in tasks:
                if t not in seen:
                    seen.add(t)
                    lines.append(t)
        else:
            lines.append("None")
        lines.append("")

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

    try:
        await _send_fn("\n".join(lines))
    except Exception as e:
        logger.error("Morning digest send failed - %s: %s", type(e).__name__, e)


async def handle(
    message: str,
    context: list[dict],
    call_claude: Callable[..., Awaitable[str]],
) -> str:
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

    status_block = "\n".join(status_lines)
    system = _SYSTEM_PROMPT + f"\n\nCurrent status:\n{status_block}"
    messages = context + [{"role": "user", "content": message}]
    return await call_claude(system, messages)
