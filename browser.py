"""A real browser for pages that need clicking, typing or JavaScript: Hermes' browser toolset on its Lightpanda engine.

Hermes (checked 2026-11-02) drives a browser over the Chrome DevTools Protocol, one browser per session, closed after
120 idle seconds. On a small machine it can use Lightpanda, a headless engine with no renderer, so no screenshots
and one page at a time. Its browser_exec runs model-written Python so a whole sub-procedure is one call. Here the
same batching is a list of steps, which costs the same one model call and runs no model-written code on the host.

Lightpanda is the only engine that fits 956 MB: Hermes' own docs put it at a sixteenth of Chrome's memory.
- It runs in the sandbox: the network, but no keys, no memory folder, nothing writable. --block-private-networks
  stops a page's own requests and redirects reaching the host's loopback services or the cloud metadata address,
  after DNS. A Lightpanda without that switch is not started.
- IGOR never logs in, buys or posts. A password field is refused, and so is any form that does not use GET, and
  page scripts lose the ability to send anything but GET. That last is best effort: a page's own code runs first.
  What it can do is open, read, search, click through and filter.
- What it returns is outside text, so it is a web tool: react.py closes what changes things after it.
- A watchdog kills it above _MAX_RSS_MB, since an address-space limit would stop V8 starting at all.
"""

import asyncio
import json
import logging
import os
import re
import shutil
import socket
import time
from pathlib import Path
from typing import Optional

import config
import sandbox
import sanitize
import url_safety

logger = logging.getLogger(__name__)

_IDLE_S = 120
_START_TIMEOUT_S = 15
_STEP_TIMEOUT_S = 20
_CALL_TIMEOUT_S = 90
_MAX_STEPS = 12
_MAX_RSS_MB = 400
_TEXT_CAP = 5000
_ELEMENTS_CAP = 40
# Lightpanda 1.0.2 on the host, 2026-11-06: under 64 GB its V8 dies at start ("Oilpan: CagedHeap
# reservation"); at 64 GB and up it starts holding 16 MB. Reserved, not used: _MAX_RSS_MB is the limit.
_ADDRESS_SPACE_MB = 131072
_SETTLE_PAUSE_S = 0.3
_WATCH_EVERY_S = 5
_STILL_S = 2

_session: Optional["_Browser"] = None
_lock = asyncio.Lock()
_watchers: set = set()

# Run in the page. Elements get a number the model can click by; text skips scripts and styles, since with no
# layout engine innerText may not be there.
_SNAPSHOT_JS = r"""(() => {
  const skip = new Set(['SCRIPT', 'STYLE', 'NOSCRIPT', 'TEMPLATE', 'SVG']);
  const parts = [];
  const walk = (node) => {
    if (node.nodeType === 3) { const t = node.textContent.replace(/\s+/g, ' ').trim(); if (t) parts.push(t); return; }
    if (node.nodeType !== 1 || skip.has(node.tagName)) return;
    for (const child of node.childNodes) walk(child);
    if (/^(P|DIV|LI|TR|H[1-6]|SECTION|ARTICLE|BR|TABLE|UL|OL)$/.test(node.tagName)) parts.push('\n');
  };
  if (document.body) walk(document.body);
  const text = parts.join(' ').replace(/ *\n[\n ]*/g, '\n').trim();
  const found = [];
  let n = 0;
  for (const el of document.querySelectorAll('a[href], button, input, select, textarea, [role=button], [onclick]')) {
    if (el.type === 'hidden') continue;
    n += 1;
    el.setAttribute('data-igor-ref', String(n));
    const label = (el.getAttribute('aria-label') || el.textContent || el.getAttribute('placeholder') || el.getAttribute('name')
                   || el.getAttribute('value') || el.getAttribute('title') || '').replace(/\s+/g, ' ').trim().slice(0, 80);
    const kind = el.tagName === 'A' ? 'link' : el.tagName === 'INPUT' ? 'input ' + (el.type || 'text') : el.tagName.toLowerCase();
    const where = el.tagName === 'A' ? ' -> ' + (el.getAttribute('href') || '').slice(0, 120) : '';
    found.push(n + '. ' + kind + ': ' + (label || '(no label)') + where);
  }
  return JSON.stringify({url: location.href, title: document.title, text, elements: found});
})()"""

# Only GET leaves the page from here on. A page's own script can undo it, which is why the docstring says best effort.
_GET_ONLY_JS = r"""(() => {
  if (window.__igorGetOnly) return;
  window.__igorGetOnly = true;
  const refuse = (m) => { throw new Error('IGOR only reads: ' + m + ' refused'); };
  const realFetch = window.fetch;
  // A refused fetch rejects, as a failed one does, so the page's own catch sees it: thrown, it
  // escaped the promise, and on Lightpanda 1.0.2 ended the step before the next one ran.
  if (realFetch) window.fetch = function (input, init) {
    const method = ((init && init.method) || (input && input.method) || 'GET').toUpperCase();
    if (method !== 'GET' && method !== 'HEAD') return Promise.reject(new TypeError('IGOR only reads: ' + method + ' refused'));
    return realFetch.apply(this, arguments);
  };
  const X = window.XMLHttpRequest;
  if (X && X.prototype) { const open = X.prototype.open;
    X.prototype.open = function (method) { if (String(method).toUpperCase() !== 'GET') refuse(method); return open.apply(this, arguments); }; }
  if (navigator.sendBeacon) navigator.sendBeacon = () => false;
})()"""


def _element(ref) -> str:
    """A JS expression for the element a step names: its number from the last look, else a CSS selector."""
    if isinstance(ref, int) or (isinstance(ref, str) and ref.strip().isdigit()):
        return f"document.querySelector('[data-igor-ref=\"{int(ref)}\"]')"
    return f"document.querySelector({json.dumps(str(ref))})"


def _click_js(ref) -> str:
    return f"""(() => {{
  const el = {_element(ref)};
  if (!el) return 'NOT FOUND';
  const form = el.form || el.closest('form');
  const sends = (el.type === 'submit' || el.tagName === 'BUTTON' && el.type !== 'button') && form;
  if (sends && (form.method || 'get').toLowerCase() !== 'get') return 'REFUSED: it sends a form that changes something';
  el.click();
  return 'OK';
}})()"""


def _type_js(ref, text: str) -> str:
    return f"""(() => {{
  const el = {_element(ref)};
  if (!el) return 'NOT FOUND';
  if ((el.type || '').toLowerCase() === 'password') return 'REFUSED: IGOR never types into a password field';
  el.focus && el.focus();
  el.value = {json.dumps(text)};
  el.dispatchEvent(new Event('input', {{bubbles: true}}));
  el.dispatchEvent(new Event('change', {{bubbles: true}}));
  return 'OK';
}})()"""


def _submit_js(ref) -> str:
    return f"""(() => {{
  const el = {_element(ref)};
  if (!el) return 'NOT FOUND';
  const form = el.tagName === 'FORM' ? el : (el.form || el.closest('form'));
  if (!form) return 'NOT FOUND: it is not in a form';
  if ((form.method || 'get').toLowerCase() !== 'get') return 'REFUSED: the form changes something, so it is not sent';
  if (form.requestSubmit) form.requestSubmit(); else form.submit();
  return 'OK';
}})()"""


def available() -> str:
    """"" when the browser can start, otherwise why not, for the model and `status`."""
    if not shutil.which("lightpanda"):
        return "the browser is not installed on the server (Lightpanda)"
    return ""


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _descendants_rss_mb(pid: int) -> float:
    """What the sandbox and everything under it hold. The host sees the real process ids, as a parent does."""
    total, stack = 0.0, [pid]
    while stack:
        current = stack.pop()
        try:
            for line in Path(f"/proc/{current}/status").read_text().splitlines():
                if line.startswith("VmRSS:"):
                    total += int(line.split()[1]) / 1024
            for task in Path(f"/proc/{current}/task").iterdir():
                stack.extend(int(c) for c in (task / "children").read_text().split())
        except (OSError, ValueError):
            continue
    return total


class _Browser:
    def __init__(self, process, port: int):
        self.process, self.port = process, port
        self.ws = self.http = None
        self.session_id: Optional[str] = None
        self.last_used = time.monotonic()
        self._id = 0

    async def connect(self) -> None:
        import aiohttp
        self.http = aiohttp.ClientSession()
        deadline = time.monotonic() + _START_TIMEOUT_S
        while True:
            try:
                self.ws = await self.http.ws_connect(f"ws://127.0.0.1:{self.port}", max_msg_size=8 * 1024 * 1024)
                break
            except Exception:
                if time.monotonic() > deadline or self.process.returncode is not None:
                    raise RuntimeError("the browser did not start")
                await asyncio.sleep(0.3)
        target = await self.send("Target.createTarget", {"url": "about:blank"}, session=False)
        attached = await self.send("Target.attachToTarget", {"targetId": target["targetId"], "flatten": True}, session=False)
        self.session_id = attached["sessionId"]
        await self.send("Page.enable")
        await self.send("Runtime.enable")
        try:
            # Before the page's own scripts, where the engine allows it; each step adds it again regardless.
            await self.send("Page.addScriptToEvaluateOnNewDocument", {"source": _GET_ONLY_JS})
        except RuntimeError:
            pass

    async def send(self, method: str, params: Optional[dict] = None, session: bool = True) -> dict:
        self._id += 1
        message = {"id": self._id, "method": method, "params": params or {}}
        if session and self.session_id:
            message["sessionId"] = self.session_id
        await self.ws.send_str(json.dumps(message))
        while True:
            reply = await asyncio.wait_for(self.ws.receive_json(), _STEP_TIMEOUT_S)
            if reply.get("id") == self._id:
                if "error" in reply:
                    raise RuntimeError(str(reply["error"].get("message", "CDP error"))[:200])
                return reply.get("result") or {}

    async def js(self, expression: str):
        result = await self.send("Runtime.evaluate", {"expression": expression, "returnByValue": True, "awaitPromise": True})
        if result.get("exceptionDetails"):
            detail = result["exceptionDetails"]
            raise RuntimeError(str((detail.get("exception") or {}).get("description") or detail.get("text") or "a script error")[:200])
        return (result.get("result") or {}).get("value")

    async def settle(self, before_url: Optional[str] = None) -> None:
        """Until the page has loaded, or the limit. After a click that went nowhere, two seconds."""
        started = time.monotonic()
        await asyncio.sleep(_SETTLE_PAUSE_S)
        while time.monotonic() - started < _STEP_TIMEOUT_S:
            try:
                ready, url = json.loads(await self.js("JSON.stringify([document.readyState, location.href])"))
            except Exception:
                ready, url = "", ""
            if ready == "complete" and (before_url is None or url != before_url):
                return
            if before_url is not None and url == before_url and time.monotonic() - started > _STILL_S:
                return
            await asyncio.sleep(_SETTLE_PAUSE_S)

    async def close(self) -> None:
        try:
            if self.ws is not None:
                await self.ws.close()
        except Exception:
            pass
        try:
            if self.http is not None:
                await self.http.close()
        except Exception:
            pass
        if self.process.returncode is None:
            self.process.kill()
            await self.process.wait()


def _flags(help_text: str) -> Optional[list[str]]:
    """The switch as this Lightpanda spells it, or None if it has none. Its docs use both hyphens and
    underscores across versions."""
    for spelling in ("--block-private-networks", "--block_private_networks"):
        if spelling in help_text:
            return [spelling]
    return None


async def _help(binary: str, *topic: str) -> str:
    probe = await asyncio.create_subprocess_exec(binary, "help", *topic, stdout=asyncio.subprocess.PIPE,
                                                 stderr=asyncio.subprocess.STDOUT)
    printed, _ = await asyncio.wait_for(probe.communicate(), 10)
    return printed.decode("utf-8", "replace")


async def _spawn() -> tuple:
    """(the sandboxed Lightpanda, its port)."""
    binary = shutil.which("lightpanda")
    # 1.0 lists serve's options only under "help serve"; the nightly before it listed them in "help".
    flags = _flags(await _help(binary, "serve")) or _flags(await _help(binary))
    if flags is None:
        raise RuntimeError("this Lightpanda cannot block private networks, so it is not started")
    port = _free_port()
    argv = sandbox.prepare([binary, "serve", "--host", "127.0.0.1", "--port", str(port), *flags],
                           hide_memory=True, network=True, mounts=sandbox.git_read_only(),
                           env={"LIGHTPANDA_DISABLE_TELEMETRY": "true"}, address_space_mb=_ADDRESS_SPACE_MB)
    process = await asyncio.create_subprocess_exec(*argv, stdin=asyncio.subprocess.DEVNULL,
                                                   stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.DEVNULL)
    return process, port


async def _start() -> _Browser:
    process, port = await _spawn()
    browser = _Browser(process, port)
    try:
        await browser.connect()
    except Exception:
        await browser.close()
        raise
    logger.info("Browser started on port %d", port)
    watcher = asyncio.get_running_loop().create_task(_watch(browser))
    _watchers.add(watcher)
    watcher.add_done_callback(_watchers.discard)
    return browser


async def _watch(browser: _Browser) -> None:
    """Close it when idle, and kill it if it grows past the limit."""
    global _session
    while browser.process.returncode is None:
        await asyncio.sleep(_WATCH_EVERY_S)
        idle = time.monotonic() - browser.last_used > _IDLE_S
        rss = _descendants_rss_mb(browser.process.pid)
        if idle or rss > _MAX_RSS_MB:
            if rss > _MAX_RSS_MB:
                logger.error("Browser at %.0f MB, past %d, so it was killed", rss, _MAX_RSS_MB)
            async with _lock:
                if _session is browser:
                    _session = None
                await browser.close()
            return
    if _session is browser:
        _session = None


def _page(snapshot: str) -> str:
    try:
        data = json.loads(snapshot)
    except (TypeError, ValueError):
        return "[the page could not be read]"
    text = sanitize.redact(str(data.get("text") or ""))
    if len(text) > _TEXT_CAP:
        text = text[:_TEXT_CAP] + f"\n[cut at {_TEXT_CAP} characters; use a read step with a selector for one part]"
    elements = data.get("elements") or []
    listing = "\n".join(elements[:_ELEMENTS_CAP]) + (f"\n[and {len(elements) - _ELEMENTS_CAP} more]" if len(elements) > _ELEMENTS_CAP else "")
    return (f"Page: {data.get('title') or '(no title)'}\nAt: {data.get('url')}\n\n{text or '(no text)'}\n\n"
            f"Things to click or fill, by number:\n{listing or '(none)'}")


async def _step(browser: _Browser, step: dict) -> str:
    if not isinstance(step, dict) or len(step) == 0:
        return "a step must be an object such as {\"open\": url}"
    if "open" in step:
        url = str(step["open"]).strip()
        why = url_safety.problem(url)
        if why:
            return f"not opened: {why}"
        result = await browser.send("Page.navigate", {"url": url})
        if result.get("errorText"):
            return f"not opened: {result['errorText']}"
        await browser.js(_GET_ONLY_JS)
        await browser.settle()
        await browser.js(_GET_ONLY_JS)
        return f"opened {url}"
    if "click" in step:
        before = json.loads(await browser.js("JSON.stringify(location.href)"))
        said = await browser.js(_click_js(step["click"]))
        if said == "OK":
            await browser.settle(before)
            await browser.js(_GET_ONLY_JS)
        return f"click {step['click']}: {said}"
    if "type" in step:
        return f"type into {step['type']}: {await browser.js(_type_js(step['type'], str(step.get('text', ''))))}"
    if "submit" in step:
        before = json.loads(await browser.js("JSON.stringify(location.href)"))
        said = await browser.js(_submit_js(step["submit"]))
        if said == "OK":
            await browser.settle(before)
            await browser.js(_GET_ONLY_JS)
        return f"submit {step['submit']}: {said}"
    if "read" in step:
        expr = f"(() => {{ const el = {_element(step['read'])}; return el ? el.textContent.replace(/\\s+/g, ' ').trim() : 'NOT FOUND'; }})()"
        return f"read {step['read']}: {sanitize.redact(str(await browser.js(expr)))[:_TEXT_CAP]}"
    if "js" in step:
        value = await browser.js(str(step["js"]))
        return f"js: {sanitize.redact(json.dumps(value, default=str) if not isinstance(value, str) else value)[:_TEXT_CAP]}"
    if "wait" in step:
        seconds = step["wait"] if isinstance(step["wait"], (int, float)) else 1
        await asyncio.sleep(max(0, min(5, seconds)))
        return f"waited {max(0, min(5, seconds))}s"
    return f"unknown step {sorted(step)[0]!r}: use open, click, type, submit, read, js or wait"


async def _run(steps: list) -> str:
    global _session
    async with _lock:
        if _session is None or _session.process.returncode is not None:
            _session = await _start()
        browser = _session
        browser.last_used = time.monotonic()
        done = []
        for step in steps[:_MAX_STEPS]:
            try:
                done.append(await _step(browser, step))
            except Exception as e:
                done.append(f"{json.dumps(step)[:100]} failed: {type(e).__name__}: {str(e)[:160]}")
                break
        page = _page(await browser.js(_SNAPSHOT_JS))
        browser.last_used = time.monotonic()
    cut = f"\n[only the first {_MAX_STEPS} steps ran]" if len(steps) > _MAX_STEPS else ""
    return "Steps:\n" + "\n".join(f"- {d}" for d in done) + cut + "\n\n" + page


async def run_tool(inputs: dict) -> str:
    """The browser tool. What goes wrong comes back as text for the model. Never raises."""
    steps = inputs.get("steps")
    if isinstance(steps, dict):
        steps = [steps]
    if not isinstance(steps, list) or not steps:
        return "[browser needs steps, such as [{\"open\": \"https://example.com\"}]]"
    why = available()
    if why:
        return f"[the browser is unavailable: {why}. Use fetch_url for a page that needs no clicking]"
    try:
        return await asyncio.wait_for(_run(steps), _CALL_TIMEOUT_S)
    except asyncio.TimeoutError:
        await close()
        return f"[the browser ran past {_CALL_TIMEOUT_S} seconds and was closed. Try fewer steps]"
    except sandbox.Unavailable as e:
        return f"[{e}]"
    except Exception as e:
        logger.error("Browser failed - %s: %s", type(e).__name__, e)
        await close()
        return f"[the browser failed: {type(e).__name__}: {str(e)[:160]}. It was closed; the next call starts a fresh one]"


async def close() -> None:
    global _session
    browser, _session = _session, None
    if browser is not None:
        await browser.close()


def status() -> str:
    if available():
        return "Browser: not installed."
    return "Browser: open." if _session is not None and _session.process.returncode is None else "Browser: installed, closed until needed."
