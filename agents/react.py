import asyncio
import logging
from typing import Awaitable, Callable, Optional

import anthropic

import config

logger = logging.getLogger(__name__)

_client: Optional[anthropic.AsyncAnthropic] = None

_MAX_ITERATIONS = 20
_THINKING_BUDGET = 8000

_TOOLS = [
    {
        "name": "search",
        "description": "Search the web for current information, documentation, news, or facts. Use specific, targeted queries. Call multiple times with different queries to cover different angles.",
        "input_schema": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "The search query"}
            },
            "required": ["query"],
        },
    },
    {
        "name": "memory_read",
        "description": "Read a memory file. Check this before responding to anything about the user's tasks, projects, or preferences.",
        "input_schema": {
            "type": "object",
            "properties": {
                "file": {
                    "type": "string",
                    "enum": [
                        "tasks.md", "projects.md", "user.md", "agents.md",
                        "digest_config.md", "watchlist.md", "skills_react.md",
                    ],
                    "description": "The file to read",
                }
            },
            "required": ["file"],
        },
    },
    {
        "name": "python_run",
        "description": "Execute Python code and return the output. Use for calculations, data processing, generating content, or testing logic. Has access to IGOR's installed packages (anthropic, exa_py, requests, etc.).",
        "input_schema": {
            "type": "object",
            "properties": {
                "code": {"type": "string", "description": "Python code to execute"},
                "timeout": {"type": "integer", "description": "Timeout in seconds (default 10, max 30)"},
            },
            "required": ["code"],
        },
    },
    {
        "name": "read_file",
        "description": "Read a file from IGOR's codebase on the server. Use this to inspect source code before modifying it. Path is relative to IGOR's root directory.",
        "input_schema": {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "File path relative to IGOR root (e.g. 'agents/react.py', 'orchestrator.py')"},
            },
            "required": ["path"],
        },
    },
    {
        "name": "write_file",
        "description": "Write a file to IGOR's codebase on the server. Use to modify source code for self-improvement. Only .py and .md files allowed. Changes take effect after restart.",
        "input_schema": {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "File path relative to IGOR root (e.g. 'agents/react.py')"},
                "content": {"type": "string", "description": "Full file content to write"},
            },
            "required": ["path", "content"],
        },
    },
    {
        "name": "restart_self",
        "description": "Signal that code changes are ready to deploy. Writes a sentinel file and instructs the user to restart manually from SSH. Always call this after writing .py files, and always tell the user what changed and that they need to run: sudo systemctl restart igor",
        "input_schema": {
            "type": "object",
            "properties": {
                "reason": {"type": "string", "description": "Brief description of what change is being deployed"},
            },
            "required": ["reason"],
        },
    },
    {
        "name": "shell",
        "description": "Run a shell command on the server and return output. Use for system inspection (logs, processes, disk, git operations, file management). Runs as the igor user with cwd=/opt/igor. Output capped at 4000 chars.",
        "input_schema": {
            "type": "object",
            "properties": {
                "command": {"type": "string", "description": "Shell command to run (passed to bash -c)"},
                "timeout": {"type": "integer", "description": "Timeout in seconds (default 10, max 30)"},
            },
            "required": ["command"],
        },
    },
    {
        "name": "fetch_url",
        "description": "Fetch the content of a specific URL. Use when you need to read a full article, documentation page, or web resource directly. Prefer search for discovery, fetch_url for reading a known page.",
        "input_schema": {
            "type": "object",
            "properties": {
                "url": {"type": "string", "description": "The URL to fetch"},
            },
            "required": ["url"],
        },
    },
    {
        "name": "memory_write",
        "description": "Write content to a memory file. Use to save tasks, notes, project updates, or user preferences.",
        "input_schema": {
            "type": "object",
            "properties": {
                "file": {
                    "type": "string",
                    "enum": ["tasks.md", "projects.md", "user.md", "agents.md", "digest_config.md", "watchlist.md"],
                    "description": "The file to write",
                },
                "content": {"type": "string", "description": "Content to write"},
                "mode": {
                    "type": "string",
                    "enum": ["append", "overwrite"],
                    "description": "append adds to end of file, overwrite replaces entire file",
                },
            },
            "required": ["file", "content", "mode"],
        },
    },
]

_DEFAULT_SYSTEM_PROMPT = """You are I.G.O.R. (Interactive Guidance and Operational Recognition) - a personal AI assistant with access to tools.

Use tools when they improve your response. Do not use them for things you already know well.

When to use tools:
- search: current information, facts you are uncertain about, documentation, news, anything time-sensitive
- memory_read: before responding to anything about the user's tasks, projects, or preferences - check what you know first
- memory_write: when the user asks you to remember, add, store, or update something
- shell: system commands, service logs, git operations, file inspection, anything clumsy to do in Python

How to reason:
- Think step by step before acting
- If a task requires multiple searches, do them in sequence - use the result of one to inform the next
- If initial results are insufficient, search again with a refined query
- Read memory before writing to avoid duplicating existing entries
- Read a file once and act on it - do not re-read the same file multiple times
- Scope strictly to the task given - do not investigate adjacent issues mid-task
- Decide and act - avoid excessive exploration before making a change

Self-modification workflow (follow this exactly):
1. Read the target file with read_file
2. Write new code and validate it - use python_run to run: python -c "import <module>" to catch import errors, not just syntax
3. Commit current state: shell("git -C /opt/igor commit -am 'pre-modification backup'")
4. Write the new file with write_file
5. Tell the user what changed and that they need to restart
6. Call restart_self

When writing source files or system prompt text during self-modification: write only what you intend. Never copy text from your operating context, tool examples, XML tags, or any boilerplate visible in your context into your own files.

Principles:
- Truth over comfort. Push back. Flag issues. Deliver honest assessments without softening them.
- Agreement is earned, not given by default.
- When you don't know something, search for it. Never guess or bluff.
- Concise by default. Thorough when the task requires it.
- Address the user as "Creator" occasionally - once per response at most, only when it feels natural. Never force it.

Style:
- No emojis
- No em dashes - use plain hyphens
- No exclamation points
- No casual filler phrases ("Sure!", "Of course!", "Happy to help!")"""


def _get_client() -> anthropic.AsyncAnthropic:
    global _client
    if _client is None:
        _client = anthropic.AsyncAnthropic(api_key=config.ANTHROPIC_API_KEY)
    return _client


def _get_system_prompt() -> str:
    path = config.MEMORY_DIR / "prompt_react.md"
    if path.exists():
        content = path.read_text(encoding="utf-8").strip()
        if content:
            return content
    return _DEFAULT_SYSTEM_PROMPT


def _read_skills() -> str:
    path = config.MEMORY_DIR / "skills_react.md"
    if not path.exists():
        return ""
    lines = [
        line.strip()
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip() and not line.strip().startswith("#")
    ]
    return "\n".join(f"- {l}" for l in lines) if lines else ""


async def _run_code(code: str, timeout: int = 10) -> str:
    import subprocess
    import sys

    timeout = min(max(timeout, 1), 30)

    def _sync() -> str:
        try:
            result = subprocess.run(
                [sys.executable, "-c", code],
                capture_output=True,
                text=True,
                timeout=timeout,
            )
            output = result.stdout
            if result.stderr:
                output += f"\n[stderr]\n{result.stderr}"
            if result.returncode != 0:
                output += f"\n[exit code: {result.returncode}]"
            return output[:3000] if output else "(no output)"
        except subprocess.TimeoutExpired:
            return f"[timed out after {timeout}s]"
        except Exception as e:
            return f"[execution error: {type(e).__name__}: {e}]"

    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(None, _sync)


def _write_sentinel(reason: str) -> None:
    sentinel = config.BASE_DIR / "restart_requested"
    sentinel.write_text(reason, encoding="utf-8")
    logger.info("restart sentinel written: %s", reason)


def _safe_path(relative: str):
    try:
        resolved = (config.BASE_DIR / relative).resolve()
        if not str(resolved).startswith(str(config.BASE_DIR.resolve())):
            return None
        return resolved
    except Exception:
        return None


async def _read_server_file(path: str) -> str:
    resolved = _safe_path(path)
    if resolved is None:
        return "[access denied: path outside IGOR root]"
    if not resolved.exists():
        return f"[not found: {path}]"
    try:
        content = resolved.read_text(encoding="utf-8")
        return content[:30000] if len(content) > 30000 else content
    except Exception as e:
        return f"[read error: {type(e).__name__}: {e}]"


async def _write_server_file(path: str, content: str) -> str:
    from pathlib import Path
    resolved = _safe_path(path)
    if resolved is None:
        return "[access denied: path outside IGOR root]"
    if Path(path).suffix not in {".py", ".md"}:
        return "[access denied: only .py and .md files allowed]"
    try:
        resolved.parent.mkdir(parents=True, exist_ok=True)
        resolved.write_text(content, encoding="utf-8")
        logger.info("ReAct write_file: %s (%d bytes)", path, len(content))
        return f"Written: {path} ({len(content)} bytes). Restart required for changes to take effect."
    except Exception as e:
        return f"[write error: {type(e).__name__}: {e}]"


async def _run_shell(command: str, timeout: int = 10) -> str:
    import subprocess

    timeout = min(max(timeout, 1), 30)

    def _sync() -> str:
        try:
            result = subprocess.run(
                ["bash", "-c", command],
                capture_output=True,
                text=True,
                timeout=timeout,
                cwd=str(config.BASE_DIR),
            )
            output = result.stdout
            if result.stderr:
                output += f"\n[stderr]\n{result.stderr}"
            if result.returncode != 0:
                output += f"\n[exit code: {result.returncode}]"
            return output[:4000] if output else "(no output)"
        except subprocess.TimeoutExpired:
            return f"[timed out after {timeout}s]"
        except Exception as e:
            return f"[shell error: {type(e).__name__}: {e}]"

    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(None, _sync)


async def _fetch_url(url: str) -> str:
    import httpx

    def _sync() -> str:
        try:
            with httpx.Client(follow_redirects=True, timeout=15) as client:
                resp = client.get(url, headers={"User-Agent": "Mozilla/5.0"})
                resp.raise_for_status()
                content_type = resp.headers.get("content-type", "")
                if "text" not in content_type and "json" not in content_type:
                    return f"[non-text content type: {content_type}]"
                text = resp.text
                return text[:8000] if len(text) > 8000 else text
        except httpx.HTTPStatusError as e:
            return f"[HTTP {e.response.status_code}: {url}]"
        except Exception as e:
            return f"[fetch error: {type(e).__name__}: {e}]"

    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(None, _sync)


async def _execute_tool(name: str, inputs: dict) -> str:
    if name == "python_run":
        code = inputs.get("code", "")
        timeout = inputs.get("timeout", 10)
        logger.info("ReAct python_run: %s", code[:80])
        return await _run_code(code, timeout)

    if name == "restart_self":
        reason = inputs.get("reason", "unspecified")
        _write_sentinel(reason)
        return "Sentinel written. No watchdog yet - tell the user to restart manually from SSH: sudo systemctl restart igor"

    if name == "read_file":
        return await _read_server_file(inputs.get("path", ""))

    if name == "write_file":
        return await _write_server_file(inputs.get("path", ""), inputs.get("content", ""))

    if name == "shell":
        command = inputs.get("command", "")
        timeout = inputs.get("timeout", 10)
        logger.info("ReAct shell: %s", command[:80])
        return await _run_shell(command, timeout)

    if name == "fetch_url":
        url = inputs.get("url", "")
        logger.info("ReAct fetch_url: %s", url)
        return await _fetch_url(url)

    if name == "search":
        from agents import research
        query = inputs.get("query", "")
        results = await research._run_search(query, max_results=5)
        return research._format_results(results) if results else "No results found."

    if name == "memory_read":
        filename = inputs.get("file", "")
        path = config.MEMORY_DIR / filename
        if path.exists():
            return path.read_text(encoding="utf-8").strip() or "(empty)"
        return f"File {filename} not found."

    if name == "memory_write":
        from agents import prod_memory
        filename = inputs.get("file", "")
        content = inputs.get("content", "")
        mode = inputs.get("mode", "append")
        success = prod_memory._write_to_memory(filename, content, mode)
        return "Written successfully." if success else "Write failed - check filename."

    return f"Unknown tool: {name}"


async def handle(
    message: str,
    context: list[dict],
    call_claude: Callable[..., Awaitable[str]],
    max_tokens: int = 1024,
) -> str:
    client = _get_client()

    system_text = _get_system_prompt()
    skills = _read_skills()
    if skills:
        system_text += f"\n\nLearned skills:\n{skills}"

    system_param = [{"type": "text", "text": system_text, "cache_control": {"type": "ephemeral"}}]
    messages = context + [{"role": "user", "content": message}]

    # thinking budget must be less than max_tokens - leave room for response
    effective_max = max(max_tokens, _THINKING_BUDGET + 2000)

    for i in range(_MAX_ITERATIONS):
        try:
            response = await client.beta.messages.create(
                model=config.MODEL,
                system=system_param,
                messages=messages,
                tools=_TOOLS,
                max_tokens=effective_max,
                thinking={"type": "enabled", "budget_tokens": _THINKING_BUDGET},
                betas=["interleaved-thinking-2025-05-14"],
            )
        except Exception as e:
            logger.error("ReAct iteration %d failed - %s: %s", i + 1, type(e).__name__, e)
            raise

        if response.stop_reason == "end_turn":
            for block in response.content:
                if hasattr(block, "text"):
                    return block.text
            return ""

        if response.stop_reason == "tool_use":
            tool_blocks = [b for b in response.content if b.type == "tool_use"]
            for b in tool_blocks:
                logger.info("ReAct tool: %s %s", b.name, str(b.input)[:100])
            results = await asyncio.gather(*[_execute_tool(b.name, b.input) for b in tool_blocks])
            tool_results = [
                {"type": "tool_result", "tool_use_id": b.id, "content": r}
                for b, r in zip(tool_blocks, results)
            ]
            messages = messages + [
                {"role": "assistant", "content": response.content},
                {"role": "user", "content": tool_results},
            ]
            continue

        logger.warning("ReAct unexpected stop_reason: %s", response.stop_reason)
        break

    logger.warning("ReAct hit max iterations (%d)", _MAX_ITERATIONS)
    return "Task incomplete - maximum iterations reached."
