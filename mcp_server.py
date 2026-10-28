"""I.G.O.R.'s read-only tools over MCP, for agents the user runs elsewhere.

Started per session by the client, over SSH, as igor, speaking MCP on stdio:

    ssh -i <key> <host> sudo -u igor /opt/igor/venv/bin/python /opt/igor/mcp_server.py

No port is opened. The SSH key is the credential, and it already grants far more than
this does, so the server adds no access that was not there.

Read-only by construction. React's injection rule works per turn: once web content
enters a turn, the tools that act are withdrawn. A client session has no turns IGOR
can see, and the client may have read the web before it calls here, so nothing in
this file writes, runs, sends or opens a private file. Every result is text the
client treats as data.

Hermes is an MCP client only; serving tools is IGOR's own direction (2026-10-22).
"""
import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import config

logger = logging.getLogger("igor.mcp")

# React's memory_read allowlist, less research.md, which is distilled from web pages.
# Private and sealed files are not in it, and are checked again by path below.
NOTES = (
    "tasks.md", "projects.md", "user.md", "agents.md", "digest_config.md",
    "watchlist.md", "presence_config.md", "job_search.md", "jobs_found.md",
    "gig_search.md", "gigs_found.md", "price_watch.md", "price_watch_status.md",
)

_MAX_CHARS = 20000

_INSTRUCTIONS = (
    "I.G.O.R. is the user's always-on personal agent. These tools read what it knows: "
    "its notes, the stored conversation, what its watchers saw, its token use today "
    "and its own architecture. All of them are read-only."
)


def _capped(text: str) -> str:
    if len(text) <= _MAX_CHARS:
        return text
    return text[:_MAX_CHARS] + f"\n\n[cut at {_MAX_CHARS} characters; search for something narrower]"


def read_note(file: str) -> str:
    """Read one of I.G.O.R.'s memory notes: tasks.md, projects.md, user.md, agents.md,
    digest_config.md, watchlist.md, presence_config.md, job_search.md, jobs_found.md
    (what the job watch found and its notes on fit), gig_search.md, gigs_found.md,
    price_watch.md or price_watch_status.md."""
    from agents import react
    logger.info("read_note %s", file)
    if file not in NOTES:
        return f"{file!r} is not readable here. Readable notes: {', '.join(NOTES)}."
    path = (config.MEMORY_DIR / file).resolve()
    if path.parent != config.MEMORY_DIR.resolve() or react._is_private(path):
        return f"{file!r} is not readable here."
    try:
        return _capped(path.read_text(encoding="utf-8").strip() or "(empty)")
    except OSError:
        return f"{file} does not exist yet."


async def search_notes(query: str) -> str:
    """Search I.G.O.R.'s memory notes for a word or phrase. Returns matching lines with
    the file name and line number. Private files and web-derived notes are skipped."""
    from agents import react
    logger.info("search_notes %r", query)
    if not query.strip():
        return "Give a word or phrase to search for."
    return _capped(await react._search_memory_files(query))


def search_conversation(query: str = "", around: int = 0) -> str:
    """Search the stored conversation between the user and I.G.O.R. (the last 90
    days) for messages containing every word of the query, or failing that the most
    of them. Returns numbered, dated excerpts, newest first. around: a #number from
    a search, to read that message with two either side."""
    from agents import react
    logger.info("search_conversation %r %s", query, around)
    if around:
        return _capped(react._search_history("", int(around)))
    if not query.strip():
        return "Give words to search for."
    return _capped(react._search_history(query).replace("search_history with around",
                                                         "search_conversation with around"))


def watch_history(item: str, days: int = 30) -> str:
    """What I.G.O.R.'s watchers recorded about an item - a product's price or stock, a
    job or gig posting - over the last `days` days (1 to 90)."""
    import history
    logger.info("watch_history %r %s", item, days)
    return _capped(history.describe(item, max(1, min(int(days), 90))))


def usage_today() -> str:
    """Model calls and tokens I.G.O.R. has spent in the last 24 hours, against each
    model's daily cap where it is known."""
    import usage
    logger.info("usage_today")
    return usage.summary()


def describe_igor() -> str:
    """I.G.O.R.'s own summary of how it is built: its agents, models, memory, tools
    and limits, verified against its source code."""
    from agents import self_describe
    logger.info("describe_igor")
    return self_describe._load_document() or "The architecture summary could not be read."


TOOLS = (read_note, search_notes, search_conversation, watch_history, usage_today, describe_igor)


def main() -> None:
    # stdout carries the protocol. Logging goes to stderr, which the client keeps.
    logging.basicConfig(stream=sys.stderr, level=logging.INFO,
                        format="%(asctime)s %(name)s: %(message)s")
    from mcp.server import MCPServer
    from mcp_types import ToolAnnotations

    server = MCPServer("igor", instructions=_INSTRUCTIONS)
    read_only = ToolAnnotations(read_only_hint=True, open_world_hint=False)
    for tool in TOOLS:
        server.tool(annotations=read_only)(tool)
    server.run("stdio")


if __name__ == "__main__":
    main()
