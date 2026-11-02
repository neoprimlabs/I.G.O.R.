"""Plugins in Hermes' format: a folder with plugin.yaml and __init__.py whose register(ctx) adds tools,
slash commands and hooks. Found on disk, off until enabled, as Hermes has it.

Hermes imports a plugin into the agent's own process, with its keys. Here each call is a fresh process in
the sandbox (plugin_runner.py): the network, a folder of its own that lasts (IGOR_STATE), and no memory
folder, no .env, no write to .git. Hermes' plugin API has dozens of register_ calls. Tools, commands and
hooks work; `plugins` names whatever else a plugin asked for, and what asks for Hermes' own modules fails
to load and says so.

- memory/private/cron_scripts/plugins/<name>/ is sealed from every tool, so only the user or Claude Code can
  place one. Plain memory/private is not enough: a turn can read and write there, and IGOR must not be able
  to write code and then run it this way.
- A tool's result is outside text: it counts as a web tool, the same as an MCP server's (react.py).
- A command's answer goes to the user with no model, and stays out of the conversation.
- requires_env in the manifest passes those variables in, unless they are IGOR's own keys. A value set in
  .env is the user's consent to that one plugin having it.
- Hook events: pre_tool_call, post_tool_call, post_llm_call, on_session_end. Hermes fails closed if a
  pre_tool_call callback raises or hangs, and so does this: a broken policy must not read as a passed one.

`enable plugin x` and `disable plugin x` are typed by the user and handled in code.
"""

import asyncio
import json
import logging
import os
import re
import sys
from pathlib import Path
from typing import Optional

import config
import memory_entries
import prerun
import sandbox
import sanitize

logger = logging.getLogger(__name__)

_NAME_RE = re.compile(r"^[A-Za-z0-9_-]{1,40}$")
_TOOL_NAME_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_-]{0,63}$")
_COMMAND_RE = re.compile(r"^\s*/([A-Za-z0-9_-]{1,32})(?:\s+(.*))?$", re.DOTALL)
_INSIDE = "/tmp/plugin"
_STATE_INSIDE = "/tmp/state"
_MAX_TOOLS = 12
_DESCRIPTION_CAP = 300
_WHAT_CAP = 60
_SCHEMA_CAP = 1500
_RESULT_CAP = 6000
_READ_CAP = 90000
_TIMEOUT_S = 30
_HOOK_TIMEOUT_S = 10
_LOAD_TIMEOUT_S = 15
_PAYLOAD_CAP = 20000

# What IGOR fires, and what a plugin registers for it in Hermes' names.
_EVENTS = {"pre_tool_call": ("pre_tool_call",), "post_tool_call": ("post_tool_call",),
           "agent_end": ("post_llm_call", "on_session_end")}

_known: dict = {}
_tasks: set = set()


def _private() -> Path:
    return config.MEMORY_DIR / "private"


def _folder() -> Path:
    return prerun._folder() / "plugins"


def _config_path() -> Path:
    return _private() / "plugins.json"


def _found() -> dict[str, Path]:
    try:
        return {p.name: p for p in sorted(_folder().iterdir())
                if _NAME_RE.match(p.name) and p.is_dir() and not p.is_symlink() and (p / "__init__.py").is_file()}
    except OSError:
        return {}


def manifest(folder: Path) -> dict:
    """The few keys used, read by hand: the host has no YAML library, and a manifest is this simple."""
    out: dict = {"requires_env": []}
    try:
        lines = (folder / "plugin.yaml").read_text(encoding="utf-8").splitlines()
    except (OSError, UnicodeDecodeError):
        return out
    current = None
    for line in lines:
        item = re.match(r"^\s+-\s+(.+)$", line)
        if item and current == "requires_env":
            out["requires_env"].append(item.group(1).split("#")[0].strip().strip("'\""))
            continue
        pair = re.match(r"^([A-Za-z_]+):\s*(.*)$", line)
        if not pair:
            continue
        current, value = pair.group(1), pair.group(2).split(" #")[0].strip()
        if current == "requires_env":
            if value.startswith("["):
                out["requires_env"] = [v.strip().strip("'\"") for v in value.strip("[]").split(",") if v.strip()]
        elif current in ("name", "version", "description"):
            out[current] = value.strip("'\"")
    out["requires_env"] = [v for v in out["requires_env"] if re.match(r"^[A-Za-z_][A-Za-z0-9_]*$", v)]
    return out


def enabled() -> list[str]:
    try:
        data = json.loads(_config_path().read_text(encoding="utf-8"))
    except FileNotFoundError:
        return []
    except (OSError, ValueError) as e:
        logger.warning("Plugin list unreadable, none enabled - %s", type(e).__name__)
        return []
    names = data.get("enabled") if isinstance(data, dict) else None
    return [str(n) for n in names] if isinstance(names, list) else []


def _set_enabled(names: list[str]) -> None:
    _private().mkdir(parents=True, exist_ok=True)
    tmp = _config_path().with_suffix(".tmp")
    tmp.write_text(json.dumps({"enabled": sorted(set(names))}, indent=1), encoding="utf-8")
    os.replace(tmp, _config_path())


def _env_problem(wanted: list[str]) -> str:
    """Why a plugin cannot have what it asks for, or "". Before any of its code runs."""
    own = [v for v in wanted if v in sanitize._KEY_VARIABLES]
    if own:
        return f"it asks for IGOR's own key {', '.join(own)}, which no plugin gets"
    missing = [v for v in wanted if not os.environ.get(v)]
    return f"{', '.join(missing)} is not set in .env" if missing else ""


async def _run(name: str, action: str, request: dict, timeout: int = _TIMEOUT_S) -> tuple[Optional[dict], str]:
    """(what the runner answered, "") or (None, why not). Never raises."""
    folder = _found().get(name)
    if folder is None:
        return None, "it is not on disk"
    wanted = manifest(folder)["requires_env"]
    why = _env_problem(wanted)
    if why:
        return None, why
    env = {v: os.environ[v] for v in wanted}
    state = _folder() / ".state" / name
    process = None
    try:
        state.mkdir(parents=True, exist_ok=True)
        argv = sandbox.prepare([sys.executable, str(config.BASE_DIR / "plugin_runner.py"), _INSIDE, action],
                               hide_memory=True, network=True,
                               mounts=(("--ro-bind", folder, _INSIDE), ("--bind", state, _STATE_INSIDE),
                                       *sandbox.git_read_only()),
                               env={"IGOR_STATE": _STATE_INSIDE, **env})
        process = await asyncio.create_subprocess_exec(*argv, stdin=asyncio.subprocess.PIPE,
                                                       stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL)
        printed, _ = await asyncio.wait_for(asyncio.gather(prerun._read(process.stdout, _READ_CAP),
                                                           prerun._feed(process.stdin, json.dumps(request, default=str))), timeout)
        await asyncio.wait_for(process.wait(), 5)
    except asyncio.TimeoutError:
        process.kill()
        await process.wait()
        logger.error("Plugin %s ran past %d seconds on %s and was killed", name, timeout, action)
        return None, f"it ran past {timeout} seconds"
    except Exception as e:
        logger.error("Plugin %s could not run - %s: %s", name, type(e).__name__, e)
        return None, f"it could not start ({type(e).__name__})"
    return _answer(printed, env)


def _answer(printed: str, env: dict) -> tuple[Optional[dict], str]:
    for value in env.values():
        if len(value) >= 8:
            printed = printed.replace(value, "[REDACTED]")
    try:
        data = json.loads(printed)
    except ValueError:
        return None, "it crashed before answering"
    if not isinstance(data, dict):
        return None, "it answered with something unreadable"
    return (None, str(data["error"])[:200]) if data.get("error") else (data, "")


def _chosen(plugin: str, listed: list, taken: set) -> list[dict]:
    """A plugin's words land in a prompt, so they are treated like anything else from outside."""
    out = []
    for tool in listed if isinstance(listed, list) else []:
        name = str(tool.get("name") or "")
        schema = tool.get("schema") if isinstance(tool.get("schema"), dict) else {"type": "object", "properties": {}}
        description = sanitize.clean(prerun._TAG_CHARS_RE.sub("", str(tool.get("description") or "")))
        why = ("its name is not usable" if not _TOOL_NAME_RE.match(name)
               else "a tool already has that name" if name in taken
               else memory_entries._scan(f"{description} {json.dumps(schema)}")
               or ("its schema is too large" if len(json.dumps(schema)) > _SCHEMA_CAP else ""))
        if why:
            logger.warning("Plugin tool %s/%s refused: %s", plugin, name, why)
            continue
        taken.add(name)
        out.append({"name": name, "plugin": plugin, "description": description[:_DESCRIPTION_CAP] or name,
                    "what": " ".join(description.split())[:_WHAT_CAP] or name, "schema": schema})
    return out


def _core_names() -> set:
    from agents import react
    return {t["name"] for t in react._TOOLS if t["name"] not in _plugin_tool_names()} | {"load_tools"}


def _plugin_tool_names() -> set:
    return {tool["name"] for entry in _known.values() for tool in entry.get("tools", [])}


async def discover() -> int:
    """Ask each enabled plugin what it offers, and return how many tools React gets. A plugin that does not
    load keeps nothing, and says why in `plugins`. With none enabled, reads one small file. Never raises."""
    global _known
    try:
        known: dict = {}
        on = enabled()
        found = _found()
        taken = _core_names()
        count = 0
        for name in on:
            if name not in found:
                known[name] = {"why": "it is not on disk"}
                continue
            data, why = await _run(name, "list", {}, _LOAD_TIMEOUT_S)
            if data is None:
                logger.warning("Plugin %s did not load - %s", name, why)
                known[name] = {"why": why}
                continue
            tools = _chosen(name, data.get("tools"), taken)[:max(0, _MAX_TOOLS - count)]
            count += len(tools)
            known[name] = {"tools": tools,
                           "commands": [c for c in data.get("commands") or [] if isinstance(c, dict) and c.get("name")],
                           "hooks": [h for h in data.get("hooks") or [] if isinstance(h, str)],
                           "left_out": [x for x in data.get("left_out") or [] if isinstance(x, str)]}
        _known = known
        return count
    except Exception as e:
        logger.error("Plugin discovery failed, no plugin tools - %s: %s", type(e).__name__, e)
        _known = {}
        return 0


def registered() -> list[dict]:
    return [tool for entry in _known.values() for tool in entry.get("tools", [])]


async def call(name: str, arguments) -> str:
    """One tool call in its own process. What goes wrong comes back as text for the model."""
    owner = next((plugin for plugin, entry in _known.items() if any(t["name"] == name for t in entry.get("tools", []))), None)
    if owner is None:
        return f"[{name} is not an available tool]"
    data, why = await _run(owner, "tool", {"name": name, "args": arguments if isinstance(arguments, dict) else {}})
    if data is None:
        return f"[the plugin {owner} failed: {why}]"
    text = sanitize.redact(prerun._TAG_CHARS_RE.sub("", str(data.get("result") or ""))) or "[the tool returned nothing]"
    return text[:_RESULT_CAP] + f"\n[cut at {_RESULT_CAP} characters]" if len(text) > _RESULT_CAP else text


async def command(text: str) -> Optional[str]:
    """What a plugin's slash command says, or None if this message is not one. Only with a slash, since a
    plugin's name could be any common word. A command IGOR already has keeps its meaning."""
    if not any(entry.get("commands") for entry in _known.values()):
        return None
    m = _COMMAND_RE.match(text or "")
    if not m:
        return None
    wanted = m.group(1).lower()
    import commands as builtin
    if builtin.parse(f"/{wanted}"):
        return None
    for plugin, entry in _known.items():
        if any(str(c.get("name")).lower() == wanted for c in entry.get("commands", [])):
            data, why = await _run(plugin, "command", {"name": wanted, "args": (m.group(2) or "").strip()})
            if data is None:
                return f"The plugin {plugin} failed: {why}."
            said = sanitize.clean(sanitize.redact(prerun._TAG_CHARS_RE.sub("", str(data.get("result") or "")))).strip()
            return said[:_RESULT_CAP] or f"/{wanted} ran and had nothing to say."
    return None


def hooked(event: str) -> list[str]:
    names = _EVENTS.get(event, ())
    return [plugin for plugin, entry in _known.items() if set(names) & set(entry.get("hooks", []))]


def _small(fields: dict) -> dict:
    return fields if len(json.dumps(fields, default=str)) <= _PAYLOAD_CAP else {**{k: "" for k in fields}, "tool_name": fields.get("tool_name", "")}


async def pre_tool(tool_name: str, tool_input: dict) -> Optional[str]:
    """What to tell the model if a plugin blocks this call, or None. A plugin that fails blocks too."""
    for plugin in hooked("pre_tool_call"):
        data, why = await _run(plugin, "hook", {"event": "pre_tool_call", "fields": _small(
            {"tool_name": tool_name, "args": tool_input, "task_id": ""})}, _HOOK_TIMEOUT_S)
        if data is None:
            logger.error("Plugin %s failed on pre_tool_call, so %s is blocked - %s", plugin, tool_name, why)
            return (f"[blocked: the plugin {plugin} checks tool calls and failed: {why}. Nothing ran. Tell the user, "
                    f"who can turn it off with: disable plugin {plugin}]")
        for said in data.get("results") or []:
            if "block" in (said.get("action"), said.get("decision")):
                reason = str(said.get("message") or said.get("reason") or "Blocked by a plugin.")[:400]
                logger.info("Plugin %s blocked %s - %s", plugin, tool_name, reason[:120])
                return f"[blocked by the user's plugin {plugin}: {reason}. Nothing ran. Do not retry it. Tell the user what you wanted to do.]"
    return None


async def _observe(plugin: str, events: tuple, fields: dict) -> None:
    for event in events:
        if event in _known.get(plugin, {}).get("hooks", []):
            data, why = await _run(plugin, "hook", {"event": event, "fields": _small(fields)}, _HOOK_TIMEOUT_S)
            if data is None:
                logger.warning("Plugin %s failed on %s - %s", plugin, event, why)


def fire(event: str, **fields) -> None:
    """Not waited for, like hooks.fire. Never raises."""
    names = hooked(event)
    if not names:
        return
    if event == "post_tool_call":
        fields = {"tool_name": fields.get("tool_name", ""), "args": fields.get("tool_input", {}),
                  "result": fields.get("tool_result", ""), "task_id": ""}
    elif event == "agent_end":
        fields = {"session_id": "", "task_id": "", "turn_id": "", "user_message": fields.get("message", ""),
                  "assistant_response": fields.get("response", ""), "conversation_history": [],
                  "model": "", "platform": "discord", "completed": True, "failed": False, "interrupted": False}
    try:
        loop = asyncio.get_running_loop()
        for plugin in names:
            task = loop.create_task(_observe(plugin, _EVENTS[event], fields))
            _tasks.add(task)
            task.add_done_callback(_tasks.discard)
    except RuntimeError:
        return


async def drain() -> None:
    """For tests: what fire started."""
    while _tasks:
        await asyncio.gather(*list(_tasks))


def listing() -> str:
    found, on = _found(), enabled()
    if not found and not on:
        return ("No plugins are installed. One goes in memory/private/cron_scripts/plugins/<name>/ on the server, "
                "in Hermes' format, and only you or Claude Code can place it.")
    lines = []
    for name in sorted(set(found) | set(on)):
        info = manifest(found[name]) if name in found else {}
        title = f"{name} {info.get('version', '')}".strip()
        if name not in on:
            lines.append(f"{title}: off. {info.get('description', '')}".strip())
            continue
        entry = _known.get(name, {})
        if "why" in entry or not entry:
            lines.append(f"{title}: on, but not loaded: {entry.get('why', 'it loads at the next start')}.")
            continue
        parts = [f"tools {', '.join(t['name'] for t in entry['tools'])}" if entry["tools"] else "",
                 f"commands {', '.join('/' + str(c['name']) for c in entry['commands'])}" if entry["commands"] else "",
                 f"hooks {', '.join(entry['hooks'])}" if entry["hooks"] else ""]
        lines.append(f"{title}: on. {'; '.join(p for p in parts if p) or 'it offers nothing IGOR can use'}.")
        if entry["left_out"]:
            lines.append(f"  It also asked for {', '.join(entry['left_out'])}, which IGOR does not have.")
    return "\n".join(lines)


async def act(action: str, name: str) -> str:
    """The user's enable or disable, in force at once."""
    on = enabled()
    found = _found()
    match = next((n for n in {*found, *on} if n.lower() == name.lower()), None)
    if match is None:
        return f"There is no plugin called {name}. Installed: {', '.join(sorted(found)) or 'none'}."
    if action == "disable":
        if match not in on:
            return f"{match} is already off."
        _set_enabled([n for n in on if n != match])
    else:
        if match in on:
            return f"{match} is already on."
        why = _env_problem(manifest(found[match])["requires_env"]) if match in found else "it is not on disk"
        if why:
            return f"{match} stays off: {why}."
        _set_enabled([*on, match])
    from agents import react
    await discover()
    react.sync_plugins()
    if action == "disable":
        return f"{match} is off."
    entry = _known.get(match, {})
    if "why" in entry:
        _set_enabled([n for n in enabled() if n != match])
        await discover()
        react.sync_plugins()
        return f"{match} stays off, because it did not load: {entry['why']}."
    tools, cmds = entry.get("tools", []), entry.get("commands", [])
    # It has the network, and these are the user's words and what tools return. Hermes' sees them too.
    watching = " It also sees your messages or what tools return, through hooks, so keep it only if you trust it." if entry.get("hooks") else ""
    return (f"{match} is on. It adds {len(tools)} tool{'' if len(tools) == 1 else 's'} and "
            f"{len(cmds)} command{'' if len(cmds) == 1 else 's'}, all run in the sandbox.{watching}")
