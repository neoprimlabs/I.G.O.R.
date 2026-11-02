"""The web dashboard (Hermes' web dashboard): what it shows, what it hides, and who it answers.

Run: python tests/test_dashboard.py   (repo root, no API calls, needs aiohttp)
"""

import asyncio
import json
import os
import shutil
import sys
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import config

config.MEMORY_DIR = Path(tempfile.mkdtemp())
config.LOG_FILE = config.MEMORY_DIR / "igor.log"

import context_store
import dashboard
import http_server
import usage

_failures: list[str] = []


def _check(name: str, condition: bool, detail: str = "") -> None:
    print(f"  {'pass' if condition else 'FAIL'}  {name}" + ("" if condition else f"  {detail}"))
    if not condition:
        _failures.append(name)


def test_host_names():
    for host in ("127.0.0.1:8642", "localhost:8642", "localhost:9000", "[::1]:8642", "LOCALHOST", "127.0.0.1"):
        _check(f"answers {host}", dashboard.host_ok(host))
    for host in ("evil.example:8642", "127.0.0.1.evil.example", "localhost.evil.example:8642", "", "10.0.0.5:8642", "igor:8642"):
        _check(f"refuses {host!r}", not dashboard.host_ok(host))


def test_the_snapshot():
    now = datetime.now(timezone.utc)
    ledger = {usage._hour(now): {"openai/gpt-oss-120b": {"React": 5000, "Chat": 1000}},
              usage._hour(now - timedelta(hours=3)): {"openai/gpt-oss-20b": {"Board": 1200}}}
    (config.MEMORY_DIR / "token_ledger.json").write_text(json.dumps(ledger), encoding="utf-8")
    (config.MEMORY_DIR / "scheduled.json").write_text(json.dumps([
        {"id": "a", "due": "2026-11-03T16:00:00+00:00", "brief": "Practical idea for IGOR", "repeat": "weekdays"},
        {"id": "b", "due": "2026-11-02T17:00:55+00:00", "brief": "", "content": "", "script": "host_watch.py", "repeat": "hourly"}]), encoding="utf-8")
    (config.MEMORY_DIR / "heartbeat.txt").write_text("now", encoding="utf-8")
    secret = "gsk_" + "b" * 40
    config.LOG_FILE.write_text("Traceback line with no date\n" + "".join(
        f"2026-11-02 0{i}:00:00,000 ERROR x: failure {i} key={secret}\n" for i in range(20)), encoding="utf-8")
    data = dashboard.snapshot()
    json.dumps(data)
    print("  info ", json.dumps({k: (v if k != "tokens" else "...") for k, v in data.items()})[:600])
    react = data["tokens"]["models"]["openai/gpt-oss-120b"]
    _check("tokens per model, with the biggest label first", react["total"] == 6000 and list(react["by_label"]) == ["React", "Chat"], str(react))
    _check("headroom against the cap", react["cap"] == config.DAILY_TOKEN_CAPS["openai/gpt-oss-120b"] and react["headroom"] == react["cap"] - 6000, str(react))
    _check("24 hourly bars, the newest last", len(react["hourly"]) == 24 and react["hourly"][-1] == 6000, str(react["hourly"]))
    import clock
    _check("labelled in the user's own hours", data["tokens"]["hours"][-1] == datetime.now(timezone.utc).astimezone(clock.USER_TZ).strftime("%H"), str(data["tokens"]["hours"][-3:]))
    _check("the browser row says it once", not str(data["engines"].get("browser", "")).startswith("Browser"), str(data["engines"]))
    _check("the disk's size, for its gauge", "disk_total_mb" in data["resources"] or "unreadable" in data["resources"])
    _check("an uncapped model says so", data["tokens"]["models"]["openai/gpt-oss-20b"]["cap"] is None and data["tokens"]["models"]["openai/gpt-oss-20b"]["hourly"][-4] == 1200)
    _check("scheduled messages, soonest first, in local time", [s["what"] for s in data["scheduled"]] == ["script host_watch.py", "Practical idea for IGOR"], str(data["scheduled"]))
    _check("Discord connected by the heartbeat", data["process"]["discord"] == "connected")
    _check("newest errors first, only dated lines, at most 15", len(data["errors"]) == 15 and "failure 19" in data["errors"][0], str(data["errors"][:2]))
    _check("keys in errors are redacted", not any(secret in e for e in data["errors"]))
    _check("no proposals file is zero waiting, or says why", data["proposals"] == 0 or "unreadable" in data["proposals"], str(data["proposals"]))
    _check("the board and plugins answer", isinstance(data["board"], list) and isinstance(data["plugins"], list))
    if Path("/proc/meminfo").exists():
        _check("memory and disk with a level", data["resources"]["memory"] in ("ok", "elevated", "critical") and data["resources"]["disk_free_mb"] > 0)
    (config.MEMORY_DIR / "heartbeat.txt").unlink()
    _check("no heartbeat is not connected", dashboard.snapshot()["process"]["discord"] == "not connected")


def test_a_part_that_fails_fails_alone():
    held = dashboard._scheduled

    def _scheduled():
        raise ValueError("bad file")

    dashboard._scheduled = _scheduled
    try:
        data = dashboard.snapshot()
    finally:
        dashboard._scheduled = held
    _check("the broken part says so", data["scheduled"] == {"unreadable": "ValueError"}, str(data["scheduled"]))
    _check("the rest still comes back", "models" in data["tokens"])


def test_pressure_levels():
    _check("ok", next((lvl for mb, share, lvl in dashboard._MEMORY_LEVELS if 500 < mb or 500 / 956 < share), "ok") == "ok")
    _check("elevated below 128 MB", next((lvl for mb, share, lvl in dashboard._MEMORY_LEVELS if 100 < mb or 100 / 956 < share), "ok") == "elevated")
    _check("critical below 64 MB", next((lvl for mb, share, lvl in dashboard._MEMORY_LEVELS if 40 < mb or 40 / 956 < share), "ok") == "critical")


def test_over_http():
    async def go():
        from aiohttp import web, ClientSession
        app = http_server.app()
        runner = web.AppRunner(app)
        await runner.setup()
        site = web.TCPSite(runner, "127.0.0.1", 0)
        await site.start()
        port = site._server.sockets[0].getsockname()[1]
        base = f"http://127.0.0.1:{port}"
        try:
            async with ClientSession() as s:
                async with s.get(f"{base}/dashboard") as r:
                    page = await r.text()
                    _check("the page", r.status == 200 and "<title>I.G.O.R. Console</title>" in page and r.headers["Content-Type"].startswith("text/html"))
                    _check("a strict content policy", "default-src 'none'" in r.headers.get("Content-Security-Policy", "") and r.headers.get("X-Frame-Options") == "DENY")
                async with s.get(f"{base}/dashboard/app.js") as r:
                    _check("its script", r.status == 200 and "textContent" in await r.text())
                async with s.get(f"{base}/dashboard/app.css") as r:
                    _check("its style", r.status == 200 and r.headers["Content-Type"].startswith("text/css"))
                for name, kind in (("fonts/bebas-neue.woff2", "font/woff2"), ("fonts/dm-mono-400.woff2", "font/woff2"), ("igor.jpg", "image/jpeg"), ("npl.jpg", "image/jpeg")):
                    async with s.get(f"{base}/dashboard/{name}") as r:
                        body = await r.read()
                        _check(f"serves {name}, cached a day", r.status == 200 and r.headers["Content-Type"] == kind and len(body) > 1000
                               and r.headers.get("Cache-Control") == "max-age=86400", f"{r.status} {r.headers.get('Content-Type')}")
                async with s.get(f"{base}/dashboard/status.json") as r:
                    _check("data is never cached", r.headers.get("Cache-Control") == "no-store")
                async with s.get(f"{base}/dashboard/status.json") as r:
                    _check("its data", r.status == 200 and "tokens" in await r.json())
                for name in ("../dashboard.py", "..%2Fdashboard.py", "index.html", "x.js", "fonts/OFL-DMMono.txt", "fonts%2F..%2Fapp.js", "fonts%2F..%2F..%2Fdashboard.py"):
                    async with s.get(f"{base}/dashboard/{name}") as r:
                        _check(f"nothing else is served: {name}", r.status in (403, 404) and "import" not in await r.text())
                async with s.get(f"{base}/dashboard", headers={"Host": "attacker.example:8642"}) as r:
                    _check("a rebinding Host is refused", r.status == 403)
                async with s.get(f"{base}/dashboard/status.json", headers={"Host": "attacker.example"}) as r:
                    _check("and so is its data", r.status == 403)
                async with s.post(f"{base}/dashboard/status.json") as r:
                    _check("read-only", r.status == 405)
        finally:
            await runner.cleanup()

    asyncio.run(go())

    os.environ["IGOR_DASHBOARD"] = "off"
    try:
        routes = [r.resource.canonical for r in http_server.app().router.routes() if r.resource is not None]
        _check("off when switched off", not any(r.startswith("/dashboard") for r in routes), str(routes))
    finally:
        del os.environ["IGOR_DASHBOARD"]


class _Orchestrator:
    """The parts of the real one the chat touches. Stores what a real turn stores; a command's reply is not stored."""

    def __init__(self):
        self._stop_turn = None
        self._stop_follow = None
        self.gate = asyncio.Event()
        self.users = set()
        self.pending = False

    async def process(self, user_id, content, prefer_tools=False):
        self.users.add(user_id)
        if content in ("status", "stop"):
            return ("Nothing is running." if content == "status" else "Stopping at the next step."), False
        if content == "boom":
            raise RuntimeError("the model fell over")
        self._stop_turn = asyncio.Event()
        await self.gate.wait()
        self._stop_turn = None
        context_store.append("user", content)
        context_store.append("assistant", f"Answer to {content} with **bold**")
        self.pending = True
        return f"Answer to {content} with **bold**\n\n`[Monitor]`", False

    async def follow_through(self, send):
        if not self.pending:
            return 0
        self.pending = False
        context_store.append("assistant", "A further round")
        await send("A further round")
        await send("A notice the store never kept")
        return 1


def test_the_chat():
    async def go():
        from aiohttp import web, ClientSession
        fake = _Orchestrator()
        http_server.set_targets(lambda: fake, None)
        app = http_server.app()
        runner = web.AppRunner(app)
        await runner.setup()
        site = web.TCPSite(runner, "127.0.0.1", 0)
        await site.start()
        base = f"http://127.0.0.1:{site._server.sockets[0].getsockname()[1]}"
        key_file = config.MEMORY_DIR / "private" / "dashboard.key"
        key = key_file.read_text(encoding="utf-8").strip()
        good = {"X-IGOR-Key": key}

        async def done():
            for _ in range(200):
                if not dashboard._open_turns and not dashboard._tasks:
                    return
                await asyncio.sleep(0.02)

        try:
            _check("a key was made at start, long and private", len(key) >= 32 and (os.name != "posix" or key_file.stat().st_mode & 0o777 == 0o600))
            _check("kept, not remade", dashboard.chat_key() == key)
            async with ClientSession() as s:
                async with s.get(f"{base}/dashboard/chat.json") as r:
                    _check("no key, no conversation", r.status == 401 and "shortcut" in (await r.json())["error"])
                async with s.get(f"{base}/dashboard/chat.json", headers={"X-IGOR-Key": key[:-1] + "x"}) as r:
                    _check("a wrong key, no conversation", r.status == 401)
                async with s.get(f"{base}/dashboard/chat.json", headers={**good, "Host": "attacker.example"}) as r:
                    _check("the key does not get past the Host check", r.status == 403)
                async with s.post(f"{base}/dashboard/chat", json={"text": "hi"}) as r:
                    _check("no key, no message", r.status == 401)
                for origin in ("http://evil.example", "null", "http://127.0.0.1.evil.example:8642"):
                    async with s.post(f"{base}/dashboard/chat", json={"text": "hi"}, headers={**good, "Origin": origin}) as r:
                        _check(f"another page may not post: {origin}", r.status == 403)
                for body, why in (({"text": "  "}, "empty"), ({"text": "x" * 4001}, "too long"), (["hi"], "not an object")):
                    async with s.post(f"{base}/dashboard/chat", json=body, headers=good) as r:
                        _check(f"refused: {why}", r.status == 400)
                _check("nothing reached IGOR yet", not fake.users)

                async with s.get(f"{base}/dashboard/chat.json", headers=good) as r:
                    first = await r.json()
                _check("a first look at an empty store", first["rows"] == [] and first["notes"] == [] and first["waiting"] == [] and not first["working"], str(first))
                after, boot = first["last"], first["boot"]

                async with s.post(f"{base}/dashboard/chat", json={"text": "hello"}, headers={**good, "Origin": base}) as r:
                    turn = (await r.json())["turn"]
                    _check("the page's own post is taken", r.status == 202)
                await asyncio.sleep(0.05)
                async with s.get(f"{base}/dashboard/chat.json?after={after}&notes=0&boot={boot}", headers=good) as r:
                    mid = await r.json()
                _check("waiting while it works", mid["waiting"] == [turn] and mid["working"] and mid["rows"] == [], str(mid))
                fake.gate.set()
                await done()
                async with s.get(f"{base}/dashboard/chat.json?after={after}&notes=0&boot={boot}", headers=good) as r:
                    data = await r.json()
                texts = [(row["role"], row["text"]) for row in data["rows"]]
                _check("as the user", fake.users == {config.AUTHORIZED_USER_ID})
                _check("the exchange and the round come from the store", texts == [("you", "hello"), ("igor", "Answer to hello with **bold**"), ("igor", "A further round")], str(texts))
                _check("only what the store never kept is a note", [n["text"] for n in data["notes"]] == ["A notice the store never kept"], str(data["notes"]))
                _check("nothing waiting after", data["waiting"] == [] and not data["working"])
                after, seen = data["last"], data["notes_last"]

                for text in ("status", "boom"):
                    async with s.post(f"{base}/dashboard/chat", json={"text": text}, headers=good) as r:
                        _check(f"{text} taken", r.status == 202)
                    await done()
                async with s.get(f"{base}/dashboard/chat.json?after={after}&notes={seen}&boot={boot}", headers=good) as r:
                    data = await r.json()
                notes = [(n["role"], n["text"]) for n in data["notes"]]
                _check("a command's reply, which the store never keeps, comes with its question",
                       notes[:2] == [("you", "status"), ("igor", "Nothing is running.")], str(notes))
                _check("a failure says so, and nothing more", notes[2:] == [("you", "boom"), ("igor", "Something went wrong. Details have been logged.")], str(notes))
                _check("no rows for either", data["rows"] == [])
                async with s.get(f"{base}/dashboard/chat.json?after={data['last']}&notes={data['notes_last']}&boot=other", headers=good) as r:
                    _check("after a restart the notes start again", len((await r.json())["notes"]) == len(dashboard._notes))

                fake.gate.clear()
                for text in ("one", "two", "three"):
                    async with s.post(f"{base}/dashboard/chat", json={"text": text}, headers=good) as r:
                        _check(f"queued {text}", r.status == 202)
                async with s.post(f"{base}/dashboard/chat", json={"text": "four"}, headers=good) as r:
                    _check("three waiting is the most", r.status == 429)
                async with s.post(f"{base}/dashboard/chat", json={"text": "stop"}, headers=good) as r:
                    _check("but stop always gets through", r.status == 202)
                fake.gate.set()
                await done()

                context_store.append("assistant", "[sent proactively] The morning digest")
                async with s.get(f"{base}/dashboard/chat.json?after=-1", headers=good) as r:
                    data = await r.json()
                last = data["rows"][-1]
                _check("a post IGOR sent unprompted is marked, without the marker", last["unprompted"] and last["text"] == "The morning digest", str(last))
                _check("a first look is the newest few", len(data["rows"]) <= dashboard._CHAT_ROWS and data["notes"] == [])

                http_server.set_targets(lambda: None, None)
                async with s.post(f"{base}/dashboard/chat", json={"text": "early"}, headers=good) as r:
                    _check("taken while IGOR starts", r.status == 202)
                await done()
                _check("and told to send again", dashboard._notes[-1]["text"].startswith("I.G.O.R. is still starting"))
        finally:
            await runner.cleanup()
            http_server.set_targets(None, None)

    asyncio.run(go())


def test_the_page_never_writes_html_from_data():
    script = (Path(dashboard.__file__).parent / "dashboard_page" / "app.js").read_text(encoding="utf-8")
    _check("no innerHTML", "innerHTML" not in script and "insertAdjacentHTML" not in script and "document.write" not in script)
    page = (Path(dashboard.__file__).parent / "dashboard_page" / "index.html").read_text(encoding="utf-8")
    _check("no inline script, which the content policy would block", "<script>" not in page)
    for text in (script, page, (Path(dashboard.__file__).parent / "dashboard_page" / "app.css").read_text(encoding="utf-8")):
        _check("no em dashes", "—" not in text)


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
