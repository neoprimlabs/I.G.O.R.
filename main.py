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
    "skills_research.md": "# Research Skills\n",
    "skills_dev.md": "# Dev Skills\n",
    "skills_comms.md": "# Comms Skills\n",
    "skills_react.md": "# React Skills\n",
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


def main() -> None:
    _setup_logging()
    _ensure_memory_files()
    asyncio.run(run_bot())


if __name__ == "__main__":
    main()
