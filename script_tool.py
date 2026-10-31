"""Hermes' execute_code: a script calls tools, and only what it prints reaches the model.

Tool results in a React turn stay in the prompt, and every step resends the prompt, so a job
of twelve lookups costs twelve whole calls. A script does them in one step. Hermes uses a Unix
socket between the script and the agent; here it is the child's stdin and stdout, which also
works on the Windows machine the tests run on. The script's own print goes to a buffer.

The script runs in the same sandbox as python_run, with the memory folder hidden as well, so
what it can reach is decided by the caller's call_tool, under the same rules as any other turn.
"""

import asyncio
import json
import logging
import sys

import sandbox

logger = logging.getLogger(__name__)

_TIMEOUT_S = 60
_OUTPUT_CHARS = 3400

_BOOTSTRAP = r'''
import io, json, sys, traceback, types
_in, _out = sys.stdin, sys.stdout
_buf = io.StringIO()
sys.stdout = sys.stderr = _buf

def _call(tool, args):
    _out.write(json.dumps({"tool": tool, "args": args}) + "\n")
    _out.flush()
    line = _in.readline()
    if not line:
        raise RuntimeError("the tool channel closed")
    return json.loads(line)["result"]

def _stub(tool, first):
    def call(*a, **k):
        if a:
            k[first] = a[0]
        return _call(tool, k)
    call.__name__ = tool
    return call

_m = types.ModuleType("igor_tools")
for _tool, _first in json.loads(_in.readline()).items():
    setattr(_m, _tool, _stub(_tool, _first))
sys.modules["igor_tools"] = _m
_code = json.loads(_in.readline())
try:
    exec(compile(_code, "script", "exec"), {"__name__": "__main__"})
except SystemExit:
    pass
except BaseException:
    traceback.print_exc(limit=2)
_out.write(json.dumps({"done": _buf.getvalue()[:20000]}) + "\n")
_out.flush()
'''


async def run(code: str, tools: dict, call_tool, timeout: int = _TIMEOUT_S) -> str:
    """What the script printed. tools maps each tool name to its first argument's name.
    call_tool(name, args) is awaited for each call the script makes. Never raises."""
    try:
        argv = sandbox.prepare([sys.executable, "-c", _BOOTSTRAP], hide_memory=True)
    except sandbox.Unavailable as e:
        return str(e)
    try:
        proc = await asyncio.create_subprocess_exec(
            *argv, stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE, limit=2 ** 20)
    except Exception as e:
        return f"[execution error: {type(e).__name__}: {e}]"

    async def talk():
        proc.stdin.write((json.dumps(tools) + "\n" + json.dumps(code) + "\n").encode())
        await proc.stdin.drain()
        while True:
            line = await proc.stdout.readline()
            if not line:
                return None
            message = json.loads(line)
            if "done" in message:
                return message["done"]
            result = await call_tool(str(message.get("tool")), message.get("args") or {})
            proc.stdin.write((json.dumps({"result": result}) + "\n").encode())
            await proc.stdin.drain()

    try:
        output = await asyncio.wait_for(talk(), timeout)
    except asyncio.TimeoutError:
        proc.kill()
        await proc.wait()
        return f"[the script timed out after {timeout}s and was stopped. Nothing it printed is kept.]"
    except Exception as e:
        logger.warning("Script channel failed - %s: %s", type(e).__name__, e)
        proc.kill()
        await proc.wait()
        return f"[the script failed: {type(e).__name__}. Do not write to the real stdout or stdin.]"

    if output is None:
        await proc.wait()
        why = (await proc.stderr.read()).decode(errors="replace").strip()[:500]
        return f"[the script ended early, exit code {proc.returncode}] {why}".strip()
    proc.stdin.close()
    await proc.wait()
    if not output.strip():
        return "(the script printed nothing)"
    if len(output) > _OUTPUT_CHARS:
        return output[:_OUTPUT_CHARS] + "\n[output cut: print less]"
    return output
