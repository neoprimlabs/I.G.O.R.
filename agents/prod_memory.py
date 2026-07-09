import logging

import config

logger = logging.getLogger(__name__)

_ALLOWED_FILES = frozenset({
    "tasks.md", "projects.md", "user.md", "agents.md",
    "digest_config.md", "schedule_config.md", "watchlist.md",
    "prompt_prodmem.md", "prompt_monitor.md", "prompt_react.md", "prompt_evaluator.md",
    "skills_react.md", "research.md",
})

_OVERWRITABLE_FILES = frozenset({
    "tasks.md", "projects.md", "user.md", "agents.md",
    "digest_config.md", "schedule_config.md", "watchlist.md",
    "prompt_prodmem.md", "prompt_monitor.md", "prompt_react.md", "prompt_evaluator.md",
    "skills_react.md", "research.md",
})


def _write_to_memory(filename: str, content: str, mode: str = "append") -> bool:
    if filename not in _ALLOWED_FILES:
        logger.error("Memory write blocked - disallowed file: %s", filename)
        return False

    path = config.MEMORY_DIR / filename

    if mode == "overwrite":
        if filename not in _OVERWRITABLE_FILES:
            logger.error("Memory overwrite blocked - not an overwritable file: %s", filename)
            return False
        try:
            path.write_text(content + "\n", encoding="utf-8")
            return True
        except Exception as e:
            logger.error("Memory overwrite failed for %s - %s: %s", filename, type(e).__name__, e)
            return False

    if not path.exists():
        logger.error("Memory write blocked - file not found: %s", filename)
        return False

    try:
        with path.open("a", encoding="utf-8") as f:
            f.write("\n" + content + "\n")
        return True
    except Exception as e:
        logger.error("Memory write failed for %s - %s: %s", filename, type(e).__name__, e)
        return False
