import asyncio
import logging
import sys

import config
from interfaces.discord_bot import run_bot

_MEMORY_TEMPLATES: dict[str, str] = {
    "user.md": "# User\n",
    "projects.md": "# Projects\n",
    "tasks.md": "# Tasks\n",
    "agents.md": "# Agents\n",
    "digest_config.md": "# Digest Config\n\n## Sections\n- tasks\n- daily_forecast\n- ai_news\n",
    "schedule_config.md": "# Schedule Config\n\n## morning_digest\ntime: 13:00 UTC\n",
    "watchlist.md": "# Monitor Watchlist\n\n- Morning digest delivery\n- Model update availability (weekly)\n- System health\n",
    "research.md": "# Research\n",
}


def _setup_logging() -> None:
    root = logging.getLogger()
    root.setLevel(logging.DEBUG)

    fmt = logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s")

    console = logging.StreamHandler(sys.stdout)
    console.setLevel(logging.INFO)
    console.setFormatter(fmt)

    # File handler: ERROR only - technical details, never conversation content or secrets
    file_handler = logging.FileHandler(config.LOG_FILE)
    file_handler.setLevel(logging.ERROR)
    file_handler.setFormatter(fmt)

    root.addHandler(console)
    root.addHandler(file_handler)


def _ensure_memory_files() -> None:
    """Create memory files with empty templates if they don't exist."""
    config.MEMORY_DIR.mkdir(exist_ok=True)
    for filename, template in _MEMORY_TEMPLATES.items():
        path = config.MEMORY_DIR / filename
        if not path.exists():
            path.write_text(template, encoding="utf-8")
            logging.getLogger(__name__).info("Created memory file: %s", filename)


def _smoke_test() -> None:
    """Fail loudly at boot instead of quietly at runtime.

    Config and pure logic only, no API calls. The bar for inclusion is: would this
    let IGOR start successfully and then be unable to do its job? An empty
    AUTHORIZED_USER_ID is the clearest case - the bot connects, looks healthy, and
    silently drops every message the user sends.

    On failure this exits 1 rather than raising, so start.sh's crash recovery
    restores the last good commit on the next boot.
    """
    log = logging.getLogger(__name__)
    problems: list[str] = []

    for role in ("router", "chat", "react", "research", "evaluator", "summary"):
        model = config.MODELS.get(role)
        if not isinstance(model, str) or not model.strip():
            problems.append(f"config.MODELS[{role!r}] is missing or empty")

    if not config.DISCORD_BOT_TOKEN:
        problems.append("DISCORD_BOT_TOKEN is empty - check .env")
    if not config.GROQ_API_KEY:
        problems.append("GROQ_API_KEY is empty - check .env")
    if not config.AUTHORIZED_USER_ID:
        problems.append("AUTHORIZED_DISCORD_USER_ID is unset - every message would be silently dropped")
    if config.CONTEXT_WINDOW < 1:
        problems.append(f"CONTEXT_WINDOW is {config.CONTEXT_WINDOW}, must be at least 1")

    # These four messages all resolve on routing fast paths, which return before
    # any model call. If one ever stops matching, this makes a real API call at
    # startup instead - so keep them exact.
    expected = {
        "deep research the history of the transistor": "ResearchLoop",
        "stop research": "StopResearch",
        "trigger digest": "Monitor",
        "synthesize research": "SynthesizeResearch",
    }

    try:
        from orchestrator import Orchestrator

        async def _noop(_: str) -> None:
            return None

        async def _check() -> dict:
            orch = Orchestrator(notify=_noop)
            return {msg: await orch._classify(msg) for msg in expected}

        for message, got in asyncio.run(_check()).items():
            if got != expected[message]:
                problems.append(f"routing: {message!r} went to {got}, expected {expected[message]}")
    except Exception as e:
        problems.append(f"routing check could not run - {type(e).__name__}: {e}")

    if problems:
        for problem in problems:
            log.critical("STARTUP CHECK FAILED: %s", problem)
        log.critical("Refusing to start. Fix the above, or crash recovery will restore the last good commit.")
        sys.exit(1)

    log.info("Startup checks passed: %d models, 4 routing fast paths, credentials present", len(config.MODELS))


def main() -> None:
    _setup_logging()
    _ensure_memory_files()
    _smoke_test()
    asyncio.run(run_bot())


if __name__ == "__main__":
    main()
