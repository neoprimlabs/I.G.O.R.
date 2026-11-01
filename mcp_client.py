"""Other people's MCP servers as tools for React, the other half of mcp_server.py.

Hermes reads mcp_servers from its config, names each tool mcp_<server>_<tool> and holds the
connection open; a tool that writes can ask first, on a server marked untrusted. Here, for what
this host and IGOR's limits allow:
- Read-only. A server's tools reach React if it marks them readOnlyHint, or if the user lists them
  under include, which is the user vouching for them. IGOR does not act in the outside world.
- A result is outside text, and the server is outside: react.py treats these as web tools, so the
  quarantine closes what changes things after one, and private files and a server never share a turn.
- Deferred, so a schema costs nothing until load_tools opens it. On an 8000 TPM model that matters.
- A connection per call. Nothing stays resident on 956 MB, and a server down costs one failed call.
- Which servers exist is the user's choice alone, since it decides where data can go: the list is in
  memory/private, where no tool can read or write it. Keys stay in .env, named as ${VAR}.
- A remote server is checked by url_safety, and only https. A local command runs in the sandbox with
  no network and no secrets, and the host has no Node, so npx servers cannot run here.

memory/private/mcp_servers.json, read at startup, so a change needs a restart:
{"github": {"url": "https://example.com/mcp", "headers": {"Authorization": "Bearer ${GITHUB_TOKEN}"},
            "tools": {"include": ["search_code"], "exclude": []}, "enabled": true},
 "notes": {"command": "python", "args": ["/opt/tools/notes_server.py"]}}
"""

import asyncio
import contextlib
import json
import logging
import os
import re
from typing import Optional

import config
import memory_entries
import sanitize
import url_safety

logger = logging.getLogger(__name__)

_CONFIG = "mcp_servers.json"
_CACHE = "mcp_tools.json"
_SERVER_NAME_RE = re.compile(r"^[a-z0-9_-]{1,30}$")
_VARIABLE_RE = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)\}")
# Invisible to a person and plain to a model, as Hermes strips for the same reason.
_TAG_CHARS_RE = re.compile("[\U000e0000-\U000e007f]")
_NAME_CAP = 64
_MAX_TOOLS = 12
_DESCRIPTION_CAP = 300
_WHAT_CAP = 60
_SCHEMA_CAP = 1500
_RESULT_CAP = 6000
_CONNECT_TIMEOUT = 15
_CALL_TIMEOUT = 30


def _path(name: str):
    return config.MEMORY_DIR / "private" / name


def servers() -> dict:
    """The usable servers, each with its keys filled in. A bad entry is skipped with a warning."""
    try:
        data = json.loads(_path(_CONFIG).read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {}
    except (OSError, ValueError) as e:
        logger.warning("MCP config unreadable, no servers - %s", type(e).__name__)
        return {}
    if not isinstance(data, dict):
        logger.warning("MCP config is not an object, no servers")
        return {}
    usable = {}
    for name, entry in data.items():
        why = _unusable(name, entry)
        if why == "":
            usable[name] = _filled(entry)
        elif why:
            logger.warning("MCP server %r skipped: %s", name, why)
    return usable


def _unusable(name, entry) -> Optional[str]:
    """Why a server cannot be used, "" if it can, None if it is switched off."""
    if not isinstance(entry, dict) or not _SERVER_NAME_RE.match(str(name)):
        return "its name or its entry is not valid"
    if entry.get("enabled") is False:
        return None
    url, command = entry.get("url"), entry.get("command")
    if bool(url) == bool(command):
        return "it needs a url or a command, not both"
    if url:
        if not str(url).startswith("https://"):
            return "a remote server must use https"
        problem = url_safety.problem(str(url))
        if problem:
            return problem
    missing = [v for value in (entry.get("headers") or {}).values() for v in _VARIABLE_RE.findall(str(value))
               if not os.environ.get(v)]
    return f"{', '.join(missing)} is not set" if missing else ""


def _filled(entry: dict) -> dict:
    headers = {str(k): _VARIABLE_RE.sub(lambda m: os.environ[m.group(1)], str(v))
               for k, v in (entry.get("headers") or {}).items()}
    tools = entry.get("tools") if isinstance(entry.get("tools"), dict) else {}
    return {"url": entry.get("url"), "command": entry.get("command"),
            "args": [str(a) for a in entry.get("args") or []], "headers": headers,
            "include": tools.get("include"), "exclude": tools.get("exclude") or []}


def _clean(text) -> str:
    return sanitize.clean(_TAG_CHARS_RE.sub("", str(text or "")))


def _tool_name(server: str, tool: str) -> str:
    return re.sub(r"[^a-zA-Z0-9_]", "_", f"mcp_{server}_{tool}")[:_NAME_CAP]


def _chosen(name: str, server: dict, listed: list) -> list[dict]:
    """The tools React may have from what a server listed. Its descriptions land in a prompt, so
    they are treated like anything else from outside."""
    include, out = server["include"], []
    for tool in listed:
        hints = tool.annotations
        if tool.name in server["exclude"]:
            continue
        if isinstance(include, list):
            if tool.name not in include:
                continue
        elif not (hints and hints.read_only_hint is True):
            continue
        description = _clean(tool.description)
        schema = tool.input_schema if isinstance(tool.input_schema, dict) else {"type": "object", "properties": {}}
        wording = f"{description} {json.dumps(schema)}"
        why = memory_entries._scan(wording)
        if why or len(json.dumps(schema)) > _SCHEMA_CAP:
            logger.warning("MCP tool %s/%s refused: %s", name, tool.name, why or "its schema is too large")
            continue
        out.append({"name": _tool_name(name, tool.name), "server": name, "tool": tool.name,
                    "what": " ".join(description.split())[:_WHAT_CAP] or tool.name,
                    "description": description[:_DESCRIPTION_CAP], "schema": schema})
    return out


@contextlib.asynccontextmanager
async def _open(server: dict):
    """One live session. The SDK's 2.x API, pinned in requirements.txt."""
    from mcp import ClientSession
    if server["url"]:
        from mcp.client.streamable_http import create_mcp_http_client, streamable_http_client
        async with create_mcp_http_client(headers=server["headers"]) as client:
            async with streamable_http_client(server["url"], http_client=client) as streams:
                async with ClientSession(streams[0], streams[1]) as session:
                    await session.initialize()
                    yield session
    else:
        import sandbox
        from mcp import StdioServerParameters, stdio_client
        argv = sandbox.prepare([server["command"], *server["args"]], hide_memory=True)
        with open(os.devnull, "w", encoding="utf-8") as quiet:
            async with stdio_client(StdioServerParameters(command=argv[0], args=argv[1:]), errlog=quiet) as streams:
                async with ClientSession(streams[0], streams[1]) as session:
                    await session.initialize()
                    yield session


async def _list(server: dict) -> list:
    async with _open(server) as session:
        return list((await session.list_tools()).tools)


def _kind(error: BaseException) -> str:
    """The failure's type, never its message. The SDK wraps a refused connection in a group, and
    "ExceptionGroup" tells the model and the log nothing."""
    while getattr(error, "exceptions", None):
        error = error.exceptions[0]
    return type(error).__name__


def _read_cache() -> dict:
    try:
        data = json.loads(_path(_CACHE).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


async def discover() -> int:
    """List each server's tools at startup, and return how many React gets. A server that does not
    answer keeps what was known of it. Never raises, and with no servers does nothing at all."""
    try:
        configured = await asyncio.to_thread(servers)
        if not configured:
            return 0
        known = _read_cache()
        for name, server in configured.items():
            try:
                listed = await asyncio.wait_for(_list(server), _CONNECT_TIMEOUT)
                known[name] = _chosen(name, server, listed)
            except Exception as e:
                logger.warning("MCP server %s not reached, keeping what was known - %s", name, _kind(e))
        kept = {name: tools for name, tools in known.items() if name in configured}
        path = _path(_CACHE)
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_name(_CACHE + ".tmp")
        tmp.write_text(json.dumps(kept, indent=1), encoding="utf-8")
        os.replace(tmp, path)
        return len(registered())
    except Exception as e:
        logger.error("MCP discovery failed, no outside tools - %s: %s", type(e).__name__, e)
        return 0


def registered() -> list[dict]:
    """The tools React has, up to the cap, for servers still configured."""
    configured = servers()
    tools, seen = [], set()
    for server, listed in _read_cache().items():
        if server not in configured or not isinstance(listed, list):
            continue
        for tool in listed:
            if tool["name"] not in seen:
                seen.add(tool["name"])
                tools.append(tool)
    if len(tools) > _MAX_TOOLS:
        logger.warning("MCP: %d tools offered, keeping the first %d. Use include to choose.", len(tools), _MAX_TOOLS)
    return tools[:_MAX_TOOLS]


def _text(result, server: dict) -> str:
    parts = []
    for block in result.content or []:
        parts.append(block.text if getattr(block, "type", "") == "text" else f"[{getattr(block, 'type', 'other')} content left out]")
    if not parts and result.structured_content:
        parts.append(json.dumps(result.structured_content))
    text = sanitize.redact(_TAG_CHARS_RE.sub("", "\n".join(parts))) or "[the server returned nothing]"
    for secret in server["headers"].values():
        token = secret.split()[-1] if secret.split() else ""
        if len(token) >= 8:
            text = text.replace(token, "[REDACTED]")
    if len(text) > _RESULT_CAP:
        text = text[:_RESULT_CAP] + f"\n[cut at {_RESULT_CAP} characters]"
    return f"The server reported an error: {text}" if result.is_error else text


async def _call(server: dict, tool: str, arguments: dict):
    async with _open(server) as session:
        return await session.call_tool(tool, arguments)


async def call(name: str, arguments) -> str:
    """One tool call on its own connection. Whatever goes wrong comes back as text for the model,
    naming the kind of failure only: an exception's message could hold a key."""
    entry = next((t for t in await asyncio.to_thread(registered) if t["name"] == name), None)
    if entry is None:
        return f"[{name} is not an available tool]"
    server = (await asyncio.to_thread(servers)).get(entry["server"])
    if server is None:
        return f"[the MCP server {entry['server']} is not available]"
    try:
        result = await asyncio.wait_for(_call(server, entry["tool"], arguments if isinstance(arguments, dict) else {}),
                                        _CALL_TIMEOUT)
    except asyncio.TimeoutError:
        return f"[the MCP server {entry['server']} did not answer within {_CALL_TIMEOUT} seconds]"
    except Exception as e:
        logger.warning("MCP call %s failed - %s", name, _kind(e))
        return f"[the call to the MCP server {entry['server']} failed: {_kind(e)}]"
    return _text(result, server)
