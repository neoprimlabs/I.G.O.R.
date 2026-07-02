import asyncio
import logging
from typing import Awaitable, Callable, Optional

import openai

import config

logger = logging.getLogger(__name__)

_client: Optional[openai.AsyncOpenAI] = None
_notify_fn: Optional[Callable[[str], Awaitable[None]]] = None

_MAX_ITERATIONS = 8
_THINKING_BUDGET = 8000
_TOOL_RESULT_CAP = 4000


def set_notify(fn: Callable[[str], Awaitable[None]]) -> None:
    global _notify_fn
    _notify_fn = fn

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
        "name": "search_memory",
        "description": "Search across all memory files for a keyword or phrase. Returns matching lines with context and file names. Use this before memory_read to find which file contains what you need.",
        "input_schema": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "Keyword or phrase to search for (case-insensitive)"},
            },
            "required": ["query"],
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
        "name": "patch_file",
        "description": "Make a targeted edit to a file by replacing an exact string. Safer than write_file for small changes - only modifies what you specify. old_string must appear exactly once in the file. Use this instead of write_file whenever possible.",
        "input_schema": {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "File path relative to IGOR root"},
                "old_string": {"type": "string", "description": "The exact string to replace - must appear exactly once in the file"},
                "new_string": {"type": "string", "description": "The string to replace it with"},
            },
            "required": ["path", "old_string", "new_string"],
        },
    },
    {
        "name": "write_file",
        "description": "Write a file to IGOR's codebase on the server. Use only for new files or complete rewrites - prefer patch_file for targeted edits. Only .py and .md files allowed. Changes take effect after restart.",
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
        "name": "send_message",
        "description": "Send a proactive Discord message to the user outside of the current response. Use to surface important findings, alerts, or updates the user should know about immediately.",
        "input_schema": {
            "type": "object",
            "properties": {
                "content": {"type": "string", "description": "The message to send"},
            },
            "required": ["content"],
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
                    "enum": ["tasks.md", "projects.md", "user.md", "agents.md", "digest_config.md", "watchlist.md", "research.md"],
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


def _get_client() -> openai.AsyncOpenAI:
    global _client
    if _client is None:
        _client = openai.AsyncOpenAI(
            api_key=config.GROQ_API_KEY,
            base_url="https://api.groq.com/openai/v1",
        )
    return _client


def _openai_tools() -> list:
    result = []
    for t in _TOOLS:
        result.append({
            "type": "function",
            "function": {
                "name": t["name"],
                "description": t["description"],
                "parameters": t["input_schema"],
            },
        })
    return result


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


async def _search_memory_files(query: str) -> str:
    results = []
    query_lower = query.lower()
    for path in sorted(config.MEMORY_DIR.glob("*.md")):
        try:
            lines = path.read_text(encoding="utf-8").splitlines()
            for i, line in enumerate(lines):
                if query_lower in line.lower():
                    start = max(0, i - 1)
                    end = min(len(lines), i + 3)
                    context = "\n".join(lines[start:end])
                    results.append(f"[{path.name}:{i + 1}]\n{context}")
        except Exception:
            continue
    if not results:
        return f"No matches for '{query}' in memory files."
    return "\n\n".join(results[:20])


async def _patch_server_file(path: str, old_string: str, new_string: str) -> str:
    from pathlib import Path
    resolved = _safe_path(path)
    if resolved is None:
        return "[access denied: path outside IGOR root]"
    if Path(path).suffix not in {".py", ".md"}:
        return "[access denied: only .py and .md files allowed]"
    if not resolved.exists():
        return f"[not found: {path}]"
    try:
        content = resolved.read_text(encoding="utf-8")
        count = content.count(old_string)
        if count == 0:
            return "[patch failed: old_string not found in file]"
        if count > 1:
            return f"[patch failed: old_string appears {count} times - add more context to make it unique]"
        new_content = content.replace(old_string, new_string, 1)
        resolved.write_text(new_content, encoding="utf-8")
        logger.info("ReAct patch_file: %s", path)
        return f"Patched: {path}. Restart required for changes to take effect."
    except Exception as e:
        return f"[patch error: {type(e).__name__}: {e}]"


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
        if _notify_fn:
            try:
                git_log = await _run_shell(
                    "git -C /opt/igor log -1 --pretty=format:'%h %s' --stat", timeout=5
                )
            except Exception:
                git_log = "(could not retrieve git log)"
            await _notify_fn(
                f"IGOR is restarting itself.\n\nReason: {reason}\n\nLast commit:\n{git_log}"
            )
        _write_sentinel(reason)
        return "Sentinel written. Watchdog will restart igor automatically within 5 seconds."

    if name == "search_memory":
        return await _search_memory_files(inputs.get("query", ""))

    if name == "read_file":
        return await _read_server_file(inputs.get("path", ""))

    if name == "patch_file":
        return await _patch_server_file(inputs.get("path", ""), inputs.get("old_string", ""), inputs.get("new_string", ""))

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

    if name == "send_message":
        content = inputs.get("content", "")
        if _notify_fn:
            await _notify_fn(content)
            return "Message sent."
        return "No notify function available."

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
    thinking: bool = True,
    max_iterations: int = _MAX_ITERATIONS,
) -> str:
    client = _get_client()

    from datetime import datetime, timezone
    current_dt = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")

    system_text = f"Current date and time: {current_dt}\n\n{_get_system_prompt()}"
    skills = _read_skills()
    if skills:
        system_text += f"\n\nLearned skills:\n{skills}"

    import json
    messages = [{"role": "system", "content": system_text}] + context + [{"role": "user", "content": message}]
    tools = _openai_tools()

    tool_failures = 0
    seen_calls: set = set()
    for i in range(max_iterations):
        try:
            response = await client.chat.completions.create(
                model=config.MODEL,
                messages=messages,
                tools=tools,
                max_tokens=max_tokens,
            )
        except openai.BadRequestError as e:
            if "tool_use_failed" in str(e) and tool_failures < 3:
                tool_failures += 1
                logger.warning("ReAct tool_use_failed (retry %d/3): %s", tool_failures, str(e)[:200])
                continue
            logger.error("ReAct iteration %d failed - %s: %s", i + 1, type(e).__name__, e)
            raise
        except Exception as e:
            logger.error("ReAct iteration %d failed - %s: %s", i + 1, type(e).__name__, e)
            raise

        choice = response.choices[0]

        if choice.finish_reason == "stop":
            return choice.message.content or ""

        if choice.finish_reason == "tool_calls":
            tool_calls = choice.message.tool_calls
            for tc in tool_calls:
                logger.info("ReAct tool: %s %s", tc.function.name, tc.function.arguments[:100])
            async def _run_tool(tc):
                call_key = (tc.function.name, tc.function.arguments)
                if call_key in seen_calls:
                    return "[you already made this exact call - use the earlier result and answer the user now]"
                seen_calls.add(call_key)
                try:
                    args = json.loads(tc.function.arguments)
                except json.JSONDecodeError as e:
                    return f"[tool argument parse error: {e} - retry the call with valid JSON]"
                result = await _execute_tool(tc.function.name, args)
                if len(result) > _TOOL_RESULT_CAP:
                    result = result[:_TOOL_RESULT_CAP] + "\n[truncated - Groq free tier is 8000 tokens/min; request smaller pieces]"
                return result

            results = await asyncio.gather(*[_run_tool(tc) for tc in tool_calls])
            messages = messages + [
                {
                    "role": "assistant",
                    "content": choice.message.content,
                    "tool_calls": [
                        {"id": tc.id, "type": "function", "function": {"name": tc.function.name, "arguments": tc.function.arguments}}
                        for tc in tool_calls
                    ],
                },
                *[
                    {"role": "tool", "tool_call_id": tc.id, "content": r}
                    for tc, r in zip(tool_calls, results)
                ],
            ]
            continue

        logger.warning("ReAct unexpected finish_reason: %s", choice.finish_reason)
        break

    logger.warning("ReAct hit max iterations (%d)", _MAX_ITERATIONS)
    return "Task incomplete - maximum iterations reached."
