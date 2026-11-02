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
