"""The browser (Hermes' browser tools on Lightpanda): steps over CDP, what it refuses, and how it ends.

Run: python tests/test_browser.py   (repo root, no API calls, needs aiohttp)

A fake CDP server stands in for Lightpanda, so the protocol, the steps and every failure are tested anywhere.
What the page scripts do (refusing a password field or a POST form, the private-network block) needs the real
engine, so that half runs only where Lightpanda and bubblewrap are installed: the deploy gate, once it is there.
"""

import asyncio
import json
import os
import shutil
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import config

config.MEMORY_DIR = Path(tempfile.mkdtemp())

import browser
from agents import react

_failures: list[str] = []


def _check(name: str, condition: bool, detail: str = "") -> None:
    print(f"  {'pass' if condition else 'FAIL'}  {name}" + ("" if condition else f"  {detail}"))
    if not condition:
        _failures.append(name)


class _Process:
    def __init__(self):
        self.returncode, self.pid = None, os.getpid()

    def kill(self):
        self.returncode = -9

    async def wait(self):
        return self.returncode


class _Fake:
    """Answers the CDP calls browser.py makes, with one page whose links lead to other pages."""

    def __init__(self, hang_on=None, error_on=None):
        self.url, self.calls, self.hang_on, self.error_on = "about:blank", [], hang_on, error_on
        self.pages = {"https://example.com/": ("Example", "Hello from the page. GROQ_API_KEY=" + "gsk_" + "a" * 40,
                                               ["1. link: Next -> https://example.com/next", "2. input text: q"]),
                      "https://example.com/next": ("Next", "The second page.", [])}

    def answer(self, method, params):
        self.calls.append(method)
        if method == self.error_on:
            return {"error": {"message": "the engine does not do that"}}
        if method == "Target.createTarget":
            return {"result": {"targetId": "T1"}}
        if method == "Target.attachToTarget":
            return {"result": {"sessionId": "S1"}}
        if method == "Page.navigate":
            if params["url"].endswith("/missing"):
                return {"result": {"errorText": "net::ERR_NAME_NOT_RESOLVED"}}
            self.url = params["url"]
            return {"result": {"frameId": "F1"}}
        if method == "Runtime.evaluate":
            return {"result": self.evaluate(params["expression"])}
        return {"result": {}}

    def evaluate(self, expr):
        value = lambda v: {"result": {"type": "string", "value": v}}
        if "document.readyState" in expr:
            return value(json.dumps(["complete", self.url]))
        if expr == "JSON.stringify(location.href)":
            return value(json.dumps(self.url))
        if "data-igor-ref', String(n)" in expr:
            title, text, elements = self.pages.get(self.url, ("", "", []))
            return value(json.dumps({"url": self.url, "title": title, "text": text, "elements": elements}))
        if "__igorGetOnly" in expr:
            return {"result": {"type": "undefined"}}
        if "el.click()" in expr:
            if 'data-igor-ref="1"' in expr and self.url == "https://example.com/":
                self.url = "https://example.com/next"
            return value("OK")
        if "el.value =" in expr or "requestSubmit" in expr:
            return value("OK")
        if "textContent.replace" in expr:
            return value("a part of the page")
        if expr == "throw":
            return {"result": {"type": "object"}, "exceptionDetails": {"text": "Uncaught", "exception": {"description": "Error: boom"}}}
        if expr == "1 + 1":
            return {"result": {"type": "number", "value": 2}}
        return value("?")


async def _serve(fake: _Fake):
    from aiohttp import web

    async def handler(request):
        ws = web.WebSocketResponse()
        await ws.prepare(request)
        async for msg in ws:
            data = json.loads(msg.data)
            if data["method"] == fake.hang_on:
                await asyncio.sleep(30)
            reply = fake.answer(data["method"], data.get("params") or {})
            try:
                await ws.send_str(json.dumps({"id": data["id"], **reply}))
                await ws.send_str(json.dumps({"method": "Page.loadEventFired", "params": {}}))
            except ConnectionResetError:
                break
        return ws

    app = web.Application()
    app.router.add_get("/", handler)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "127.0.0.1", 0)
    await site.start()
    return runner, site._server.sockets[0].getsockname()[1]


def _with_fake(steps, fake=None, then=None, inputs=None):
    """One run_tool against the fake, in a fresh loop. Returns (what it said, the fake)."""
    fake = fake or _Fake()

    async def go():
        runner, port = await _serve(fake)
        browser._lock = asyncio.Lock()
        browser._session = None
        held = browser._spawn, browser.shutil.which

        async def spawn():
            return _Process(), port

        browser._spawn = spawn
        browser.shutil.which = lambda name: "/usr/bin/lightpanda"
        try:
            said = await browser.run_tool(inputs if inputs is not None else {"steps": steps})
            extra = await then() if then else None
        finally:
            browser._spawn, browser.shutil.which = held
            await browser.close()
            await runner.cleanup()
        return said, extra

    said, extra = asyncio.run(go())
    return said, fake, extra


def test_open_read_and_click():
    said, fake, _ = _with_fake([{"open": "https://example.com/"}])
    _check("it opened the page", "- opened https://example.com/" in said, said)
    _check("the text comes back", "Hello from the page." in said and "Page: Example" in said, said)
    _check("with what to click, by number", "1. link: Next -> https://example.com/next" in said, said)
    _check("a key on the page is redacted", "gsk_" not in said and "[REDACTED]" in said, said)
    _check("in the protocol's order", fake.calls[:4] == ["Target.createTarget", "Target.attachToTarget", "Page.enable", "Runtime.enable"], str(fake.calls[:5]))
    _check("reads are guarded before the page runs, where the engine allows", "Page.addScriptToEvaluateOnNewDocument" in fake.calls)
    said, _, _ = _with_fake([{"open": "https://example.com/"}, {"click": 1}, {"read": "main"}, {"js": "1 + 1"}, {"wait": 0}])
    _check("a click by number follows the link", "click 1: OK" in said and "At: https://example.com/next" in said, said)
    _check("read and js steps", "read main: a part of the page" in said and "js: 2" in said and "waited 0s" in said, said)
    said, _, _ = _with_fake([{"open": "https://example.com/"}, {"type": 2, "text": "kettles"}, {"submit": 2}])
    _check("type and submit", "type into 2: OK" in said and "submit 2: OK" in said, said)
    said, _, _ = _with_fake(None, inputs={"steps": {"open": "https://example.com/"}})
    _check("one step on its own still works", "opened https://example.com/" in said, said)


def test_what_is_refused_before_the_browser_acts():
    for url in ("http://127.0.0.1:8642/v1/models", "http://169.254.169.254/opc/v2/instance/", "file:///opt/igor/.env", "http://localhost/"):
        said, fake, _ = _with_fake([{"open": url}])
        _check(f"refused: {url}", "not opened:" in said and "Page.navigate" not in fake.calls, said[:200])
    said, _, _ = _with_fake([{"open": "https://example.com/missing"}])
    _check("an address that does not resolve says so", "net::ERR_NAME_NOT_RESOLVED" in said, said)
    for bad in ({"steps": []}, {"steps": "open it"}, {}):
        said, _, _ = _with_fake(None, inputs=bad)
        _check(f"no steps: {bad}", said.startswith("[browser needs steps"), said)
    said, _, _ = _with_fake([{"jump": 1}])
    _check("an unknown step is named", "unknown step 'jump'" in said, said)
    said, _, _ = _with_fake([1])
    _check("a step that is not an object", "a step must be an object" in said, said)


def test_failures_end_the_steps_but_not_the_answer():
    said, _, _ = _with_fake([{"open": "https://example.com/"}, {"js": "throw"}, {"click": 1}])
    _check("a script error stops the steps there", "failed: RuntimeError: Error: boom" in said and "click 1" not in said, said)
    _check("and the page still comes back", "Hello from the page." in said, said)
    said, _, _ = _with_fake([{"open": "https://example.com/"}], fake=_Fake(error_on="Page.navigate"))
    _check("an engine error is a failed step", "failed: RuntimeError: the engine does not do that" in said, said)
    many = [{"wait": 0}] * 20
    said, _, _ = _with_fake(many)
    _check("at most twelve steps", said.count("waited 0s") == 12 and "only the first 12 steps ran" in said, said[:300])

    held = browser._CALL_TIMEOUT_S, browser._STEP_TIMEOUT_S
    browser._CALL_TIMEOUT_S, browser._STEP_TIMEOUT_S = 2, 1
    try:
        said, _, _ = _with_fake([{"open": "https://example.com/"}], fake=_Fake(hang_on="Page.navigate"))
    finally:
        browser._CALL_TIMEOUT_S, browser._STEP_TIMEOUT_S = held
    _check("a step that hangs fails, and the page still answers", "failed: TimeoutError" in said or "ran past" in said, said)

    async def fails_to_start():
        raise RuntimeError("the browser did not start")

    async def go():
        held_spawn = browser._start
        browser._start, browser._session = fails_to_start, None
        browser._lock = asyncio.Lock()
        held_which = browser.shutil.which
        browser.shutil.which = lambda name: "/usr/bin/lightpanda"
        try:
            return await browser.run_tool({"steps": [{"open": "https://example.com/"}]})
        finally:
            browser._start, browser.shutil.which = held_spawn, held_which

    said = asyncio.run(go())
    _check("a browser that will not start says so", "the browser failed: RuntimeError: the browser did not start" in said, said)


def test_not_installed():
    held = browser.shutil.which
    browser.shutil.which = lambda name: None
    try:
        said = asyncio.run(browser.run_tool({"steps": [{"open": "https://example.com/"}]}))
        _check("it says so, and points at fetch_url", "not installed" in said and "fetch_url" in said, said)
        _check("status says so", browser.status() == "Browser: not installed.")
    finally:
        browser.shutil.which = held


def test_it_closes_when_idle_and_when_too_big():
    async def idle():
        await asyncio.sleep(0.5)
        return browser._session

    held = browser._IDLE_S, browser._WATCH_EVERY_S
    browser._IDLE_S, browser._WATCH_EVERY_S = 0, 0.1
    try:
        _, _, left = _with_fake([{"open": "https://example.com/"}], then=idle)
    finally:
        browser._IDLE_S, browser._WATCH_EVERY_S = held
    _check("closed after the idle time", left is None)

    if Path("/proc/self/status").exists():
        _check("it can see what a process holds", browser._descendants_rss_mb(os.getpid()) > 1)
        held = browser._MAX_RSS_MB, browser._WATCH_EVERY_S
        browser._MAX_RSS_MB, browser._WATCH_EVERY_S = 0, 0.1
        try:
            _, _, left = _with_fake([{"open": "https://example.com/"}], then=idle)
        finally:
            browser._MAX_RSS_MB, browser._WATCH_EVERY_S = held
        _check("killed past the memory limit", left is None)
    else:
        print("  skip  no /proc here")


def test_the_page_is_capped():
    fake = _Fake()
    fake.pages["https://example.com/"] = ("Long", "word " * 3000, [f"{i}. link: x" for i in range(1, 100)])
    said, _, _ = _with_fake([{"open": "https://example.com/"}], fake=fake)
    _check("long text is cut, with how to read a part", "cut at 5000 characters" in said, said[-400:])
    _check("so is the list of elements", "[and 59 more]" in said, said[-200:])
    _check("an unreadable snapshot is said plainly", browser._page("not json") == "[the page could not be read]")


def test_only_an_engine_that_blocks_private_networks():
    _check("the newer spelling", browser._flags("Options:\n  --block-private-networks  Block ...") == ["--block-private-networks"])
    _check("the older spelling", browser._flags("--block_private_networks") == ["--block_private_networks"])
    _check("none means it is not started", browser._flags("--host --port") is None)


def test_how_it_joins_react():
    _check("offered", any(t["name"] == "browser" for t in react._TOOLS))
    _check("deferred", "browser" in react._DEFERRED_TOOLS)
    _check("a web tool, so a private read closes it and it closes what changes things", "browser" in react._WEB_TOOLS)
    _check("not itself closed after a web read, as fetch_url is not", "browser" not in react._QUARANTINED_AFTER_WEB)
    held = browser.run_tool

    async def fake(inputs):
        return "Page: x"

    browser.run_tool = fake
    try:
        said = asyncio.run(react._execute_tool("browser", {"steps": [{"open": "https://example.com/"}]}))
    finally:
        browser.run_tool = held
    _check("its result is marked as outside text", said.startswith(react._UNTRUSTED_OPEN) and react._UNTRUSTED_CLOSE in said, said)
    for rule in ("never logs in", "password", "fetch_url is cheaper"):
        _check(f"the description says: {rule}", rule in next(t for t in react._TOOLS if t["name"] == "browser")["description"])


def test_with_the_real_engine():
    """Only where Lightpanda is installed: what the page scripts refuse, and the private-network block."""
    import sandbox
    if not (shutil.which("lightpanda") and shutil.which("bwrap")):
        print("  skip  Lightpanda or bubblewrap is not here")
        return
    from aiohttp import web

    async def go():
        hits = []

        async def local(request):
            hits.append(request.method)
            return web.Response(text="private")

        app = web.Application()
        app.router.add_route("*", "/", local)
        runner = web.AppRunner(app)
        await runner.setup()
        site = web.TCPSite(runner, "127.0.0.1", 0)
        await site.start()
        port = site._server.sockets[0].getsockname()[1]
        browser._lock, browser._session = asyncio.Lock(), None
        page = ("<h1>Kettles</h1><p>Two in stock.</p><a href='https://example.com/a'>First</a>"
                "<form method='get' action='#found'><input name='q'><button>Search</button></form>"
                "<form method='post' action='#sent'><input type='password' name='pw'><button type='submit'>Log in</button></form>")
        try:
            said = await browser.run_tool({"steps": [{"js": f"document.body.innerHTML = {json.dumps(page)}; 'set'"}]})
            _check("it starts and runs a script", "js: set" in said, said[:300])
            _check("the snapshot reads text and numbers elements", "Two in stock." in said and "link: First" in said, said)
            said = await browser.run_tool({"steps": [{"type": "input[name=pw]", "text": "hunter2"}, {"click": "form[method=post] button"},
                                                     {"submit": "form[method=post]"}]})
            _check("a password field is refused", "never types into a password field" in said, said)
            _check("a POST form's button is refused", "click form[method=post] button: REFUSED" in said, said)
            _check("and so is sending it", "submit form[method=post]: REFUSED" in said, said)
            said = await browser.run_tool({"steps": [{"js": "window.__igorGetOnly = false; (" + browser._GET_ONLY_JS + "); 'armed'"},
                                                     {"js": f"fetch('http://127.0.0.1:{port}/', {{method: 'POST'}}).then(r => 'sent').catch(e => 'refused')"},
                                                     {"js": f"fetch('http://127.0.0.1:{port}/').then(r => r.text()).catch(e => 'blocked')"}]})
            _check("a page script cannot POST", "js: refused" in said, said)
            _check("nor reach a private address", "js: blocked" in said and not hits, f"{hits} {said[:400]}")
            rss = browser._descendants_rss_mb(browser._session.process.pid)
            print(f"  info  the browser holds {rss:.0f} MB")
            _check("it fits the memory limit", 0 < rss < browser._MAX_RSS_MB, str(rss))
        finally:
            await browser.close()
            await runner.cleanup()

    asyncio.run(go())


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            print(name)
            fn()
    shutil.rmtree(config.MEMORY_DIR, ignore_errors=True)
    if _failures:
        print(f"\n{len(_failures)} FAILED: {', '.join(_failures)}")
        sys.exit(1)
    print("\nall pass")
