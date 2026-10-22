"""The MCP server is read-only and reaches no private or sealed file.

Offline: the tools are plain functions and the mcp package is imported only in
main(), so this runs without it. Run: python tests/test_mcp_server.py
"""
import asyncio
import inspect
import sys
import tempfile
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


def _fresh() -> Path:
    config.MEMORY_DIR = Path(tempfile.mkdtemp()) / "memory"
    config.MEMORY_DIR.mkdir()
    (config.MEMORY_DIR / "tasks.md").write_text("# Tasks\n- Send the Helfen application\n", encoding="utf-8")
    (config.MEMORY_DIR / "resume.md").write_text("Phone 555-0100 SECRETRESUME\n", encoding="utf-8")
    (config.MEMORY_DIR / "corrections.md").write_text("SECRETCORRECTION\n", encoding="utf-8")
    (config.MEMORY_DIR / "research.md").write_text("SECRETRESEARCH from the web\n", encoding="utf-8")
    (config.MEMORY_DIR / "private").mkdir()
    (config.MEMORY_DIR / "private" / "profile.md").write_text("SECRETPROFILE\n", encoding="utf-8")
    (config.MEMORY_DIR.parent / ".env").write_text("GROQ_API_KEY=SECRETKEY\n", encoding="utf-8")
    return config.MEMORY_DIR


def test_the_import_needs_no_mcp_package() -> None:
    """The deploy gate imports every module; the package is only needed to serve."""
    import mcp_server
    _check("importing the module does not import mcp", "mcp.server" not in sys.modules)
    _check("it offers six tools", len(mcp_server.TOOLS) == 6, str(mcp_server.TOOLS))


def test_nothing_writes_or_acts() -> None:
    """Every tool is a reader. Names that act in React must not appear here."""
    import mcp_server
    names = {tool.__name__ for tool in mcp_server.TOOLS}
    acting = {"shell", "python_run", "write_file", "patch_file", "restart_self", "memory_write",
              "send_message", "scheduled_message", "delegate_task", "task_update", "goal",
              "proposal", "fetch_url", "search"}
    _check("no acting or web tool is exposed", not names & acting, str(names & acting))
    _check("every tool has a description the client sees",
           all((tool.__doc__ or "").strip() for tool in mcp_server.TOOLS))
    source = inspect.getsource(mcp_server)
    for word in ("write_text", "unlink", "subprocess", "_notify_fn", "memory_entries"):
        _check(f"the module never calls {word}", word not in source)


def test_read_note() -> None:
    import mcp_server
    _fresh()
    _check("an allowlisted note is read", "Helfen" in mcp_server.read_note("tasks.md"))
    _check("a missing allowlisted note says so", "does not exist" in mcp_server.read_note("projects.md"))
    for name, secret in (("resume.md", "SECRETRESUME"), ("corrections.md", "SECRETCORRECTION"),
                         ("research.md", "SECRETRESEARCH"), ("private/profile.md", "SECRETPROFILE"),
                         ("../.env", "SECRETKEY"), ("..\\.env", "SECRETKEY")):
        out = mcp_server.read_note(name)
        _check(f"{name} is refused", secret not in out and "not readable" in out, out[:120])

    # An allowlisted name that is really a link out of memory/ resolves elsewhere.
    link = config.MEMORY_DIR / "user.md"
    try:
        link.symlink_to(config.MEMORY_DIR.parent / ".env")
    except (OSError, NotImplementedError):
        print("  skip  symlinks need privileges on this machine")
    else:
        out = mcp_server.read_note("user.md")
        _check("an allowlisted name linked outside memory is refused", "SECRETKEY" not in out, out[:120])


def test_search_notes_skips_private_files() -> None:
    import mcp_server
    _fresh()
    out = asyncio.run(mcp_server.search_notes("SECRET"))
    for secret in ("SECRETRESUME", "SECRETCORRECTION", "SECRETRESEARCH", "SECRETPROFILE"):
        _check(f"search does not return {secret}", secret not in out, out[:160])
    _check("an ordinary match comes back", "Helfen" in asyncio.run(mcp_server.search_notes("helfen")))
    _check("an empty query is answered, not run", "Give" in asyncio.run(mcp_server.search_notes("  ")))


def test_results_are_capped() -> None:
    import mcp_server
    _fresh()
    (config.MEMORY_DIR / "tasks.md").write_text("x" * 50000, encoding="utf-8")
    out = mcp_server.read_note("tasks.md")
    _check("a huge note is cut, and says so", len(out) < 21000 and "cut at" in out, str(len(out)))


if __name__ == "__main__":
    print("import")
    test_the_import_needs_no_mcp_package()
    print("\nread-only")
    test_nothing_writes_or_acts()
    print("\nread_note")
    test_read_note()
    print("\nsearch_notes")
    test_search_notes_skips_private_files()
    print("\ncaps")
    test_results_are_capped()

    if _failures:
        print(f"\n{len(_failures)} FAILED: {', '.join(_failures)}")
        sys.exit(1)
    print("\nall pass")
