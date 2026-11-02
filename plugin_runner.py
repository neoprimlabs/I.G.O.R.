"""Inside the sandbox: loads one plugin in Hermes' format and does one thing with it. See plugins.py.

A fresh process for every call, so a plugin holds nothing between calls except what it writes to the folder
named by IGOR_STATE. Anything the plugin prints goes to stderr, so only this file's answer is on stdout.
"""

import asyncio
import importlib.util
import inspect
import json
import os
import sys


_TEXT_CAP = 30000


class _Context:
    """What register(ctx) is handed. Anything IGOR has no equivalent for is noted and does nothing, so the
    rest of a plugin can still load, and `plugins` can say what it left out."""

    def __init__(self):
        self.tools, self.commands, self.hooks = {}, {}, {}
        self.left_out = set()

    def register_tool(self, name, toolset=None, schema=None, handler=None, check_fn=None, is_async=False,
                      description="", **_):
        self.tools[name] = {"schema": schema or {}, "handler": handler, "check": check_fn, "description": description}

    def register_command(self, name, handler=None, description="", **_):
        self.commands[name] = {"handler": handler, "description": description}

    def register_hook(self, event, callback=None, **_):
        self.hooks.setdefault(event, []).append(callback)

    def __getattr__(self, name):
        def left_out(*_a, **_k):
            self.left_out.add(name)
        return left_out


def _load(folder: str) -> _Context:
    spec = importlib.util.spec_from_file_location("igor_plugin", os.path.join(folder, "__init__.py"),
                                                  submodule_search_locations=[folder])
    module = importlib.util.module_from_spec(spec)
    sys.modules["igor_plugin"] = module
    spec.loader.exec_module(module)
    ctx = _Context()
    module.register(ctx)
    return ctx


def _finished(value):
    return asyncio.run(value) if inspect.isawaitable(value) else value


def _offered(entry: dict) -> bool:
    check = entry["check"]
    if check is None:
        return True
    try:
        return bool(_finished(check()))
    except Exception:
        return False


def _as_text(value) -> str:
    if value is None:
        return ""
    return (value if isinstance(value, str) else json.dumps(value, default=str))[:_TEXT_CAP]


def _do(action: str, ctx: _Context, request: dict) -> dict:
    if action == "list":
        tools = [{"name": name, "description": entry["schema"].get("description") or entry["description"] or "",
                  "schema": entry["schema"].get("parameters") or {"type": "object", "properties": {}}}
                 for name, entry in ctx.tools.items() if _offered(entry)]
        return {"tools": tools,
                "commands": [{"name": name, "description": entry["description"]} for name, entry in ctx.commands.items()],
                "hooks": sorted(ctx.hooks), "left_out": sorted(ctx.left_out)}
    if action == "tool":
        entry = ctx.tools.get(request.get("name"))
        if entry is None or entry["handler"] is None:
            return {"error": "it has no such tool"}
        return {"result": _as_text(_finished(entry["handler"](request.get("args") or {})))}
    if action == "command":
        entry = ctx.commands.get(request.get("name"))
        if entry is None or entry["handler"] is None:
            return {"error": "it has no such command"}
        return {"result": _as_text(_finished(entry["handler"](request.get("args") or "")))}
    if action == "hook":
        results = [_finished(callback(**(request.get("fields") or {})))
                   for callback in ctx.hooks.get(request.get("event"), []) if callback is not None]
        return {"results": [r for r in results if isinstance(r, dict)]}
    return {"error": f"no action called {action}"}


def main() -> int:
    answer, sys.stdout = sys.stdout, sys.stderr
    # Python puts this file's folder first, and a plugin's `import config` must not find IGOR's.
    here = os.path.dirname(os.path.abspath(__file__))
    sys.path[:] = [p for p in sys.path if os.path.abspath(p or ".") != here]
    try:
        request = json.loads(sys.stdin.read() or "{}")
        result = _do(sys.argv[2], _load(sys.argv[1]), request)
    except (Exception, SystemExit) as e:
        # A plugin's message could hold what it was given, so only the kind of failure leaves here.
        result = {"error": f"{type(e).__name__} in the plugin"}
    answer.write(json.dumps(result, default=str))
    answer.flush()
    return 0


if __name__ == "__main__":
    sys.exit(main())
