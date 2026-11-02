"""IGOR's state on a web page: Hermes' web dashboard, at /dashboard on IGOR's HTTP server.

Hermes' runs on 127.0.0.1 with no login, because only the machine itself can reach a loopback port, and
checks the Host header so a page in someone's browser cannot reach it by DNS rebinding. This does both.
The user reaches it through an SSH tunnel on the key they already hold; nothing is opened on the firewall.

Read-only for now. Hermes' pages also edit config, keys and skills; IGOR keeps those in files no tool can
reach, and a page that wrote them would be a new way in. What it shows is what `status` shows and more:
the commit, Discord's heartbeat, memory and disk with Hermes' pressure levels, tokens by the hour with the
headroom left, scheduled messages and jobs, the board, plugins, background commands, proposals waiting,
the engines installed and the newest errors. No file contents and no keys: errors are redacted.

A process with the network on this host could read it, as with Hermes': here that is a plugin or a script
the user placed. Lightpanda's private-network block keeps a web page in IGOR's browser out.
"""

import json
import logging
import os
import shutil
import subprocess
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import config
import sanitize

logger = logging.getLogger(__name__)

_STARTED = time.time()
_HERE = Path(__file__).resolve().parent / "dashboard_page"
_ERROR_LINES = 15
_HEARTBEAT_STALE_S = 180
# Hermes' resource banner: elevated below 128 MiB or 15% free, critical below 64 MiB or 5%; disk below 512 and 256 MB.
_MEMORY_LEVELS = ((64, 0.05, "critical"), (128, 0.15, "elevated"))
_DISK_LEVELS = ((256, "critical"), (512, "elevated"))
_HEADERS = {
    "Content-Security-Policy": "default-src 'none'; script-src 'self'; style-src 'self'; connect-src 'self'; img-src 'self'",
    "X-Frame-Options": "DENY", "Referrer-Policy": "no-referrer", "Cache-Control": "no-store",
    "X-Content-Type-Options": "nosniff",
}


def on() -> bool:
    return os.getenv("IGOR_DASHBOARD", "on").lower() not in ("off", "0", "false", "no")


def host_ok(host: str) -> bool:
    """Only the names a browser at the end of a tunnel sends, on whatever port it chose. Any other name is a
    rebinding attempt: a domain the attacker points at 127.0.0.1 still arrives with its own name."""
    name = (host or "").strip().lower()
    name = name.rsplit(":", 1)[0] if name.count(":") == 1 or name.startswith("[") and "]:" in name else name
    return name in ("127.0.0.1", "localhost", "[::1]")


def _safe(fn):
    try:
        return fn()
    except Exception as e:
        logger.warning("Dashboard could not read %s - %s: %s", fn.__name__, type(e).__name__, e)
        return {"unreadable": f"{type(e).__name__}"}


def _commit():
    done = subprocess.run(["git", "-C", str(config.BASE_DIR), "log", "-1", "--format=%h%x09%s%x09%cI"],
                          capture_output=True, text=True, timeout=5)
    sha, subject, when = (done.stdout.strip().split("\t") + ["", "", ""])[:3]
    return {"sha": sha, "subject": subject, "when": when}


def _process():
    beat = config.MEMORY_DIR / "heartbeat.txt"
    age = time.time() - beat.stat().st_mtime if beat.exists() else None
    return {"up_seconds": round(time.time() - _STARTED),
            "discord": "connected" if age is not None and age < _HEARTBEAT_STALE_S else "not connected",
            "heartbeat_age_seconds": None if age is None else round(age)}


def _resources():
    info = {}
    for line in Path("/proc/meminfo").read_text().splitlines():
        name, value = line.split(":", 1)
        info[name] = int(value.split()[0]) // 1024
    total, available = info.get("MemTotal", 0), info.get("MemAvailable", 0)
    memory = next((level for mb, share, level in _MEMORY_LEVELS if available < mb or (total and available / total < share)), "ok")
    free_mb = shutil.disk_usage(config.MEMORY_DIR).free // (1024 * 1024)
    disk = next((level for mb, level in _DISK_LEVELS if free_mb < mb), "ok")
    return {"memory_total_mb": total, "memory_available_mb": available, "memory": memory,
            "swap_used_mb": info.get("SwapTotal", 0) - info.get("SwapFree", 0), "disk_free_mb": free_mb, "disk": disk}


def _tokens():
    import clock
    import usage
    now = datetime.now(timezone.utc)
    ledger = usage._load()
    moments = [now - timedelta(hours=back) for back in range(23, -1, -1)]
    hours = [usage._hour(moment) for moment in moments]
    models = {}
    for model, labels in usage.last_24h(now).items():
        models[model] = {"total": sum(labels.values()), "by_label": dict(sorted(labels.items(), key=lambda kv: -kv[1])),
                         "cap": config.DAILY_TOKEN_CAPS.get(model), "headroom": usage.headroom(model, now),
                         "hourly": [sum((ledger.get(h, {}).get(model) or {}).values()) for h in hours]}
    return {"hours": [moment.astimezone(clock.USER_TZ).strftime("%H") for moment in moments], "models": models}


def _scheduled():
    import clock
    path = config.MEMORY_DIR / "scheduled.json"
    entries = json.loads(path.read_text(encoding="utf-8")) if path.exists() else []
    rows = []
    for entry in sorted(entries, key=lambda e: e.get("due") or ""):
        due = datetime.fromisoformat(entry["due"]).astimezone(clock.USER_TZ) if entry.get("due") else None
        rows.append({"due": due.strftime("%a %d %b, %H:%M") if due else "", "repeat": entry.get("repeat") or "once",
                     "what": (entry.get("brief") or entry.get("content") or (f"script {entry['script']}" if entry.get("script") else ""))[:120]})
    return rows


def _jobs():
    import clock
    from agents import monitor
    scheduler = monitor._scheduler
    if scheduler is None or not scheduler.running:
        return []
    return [{"id": j.id, "next": j.next_run_time.astimezone(clock.USER_TZ).strftime("%a %H:%M") if j.next_run_time else "not scheduled"}
            for j in sorted(scheduler.get_jobs(), key=lambda j: j.next_run_time or datetime.max.replace(tzinfo=timezone.utc))]


def _lines(text: str) -> list[str]:
    return [line for line in (text or "").splitlines() if line.strip()]


def _board():
    import board
    return _lines(board.listing())


def _plugins():
    import plugins
    return _lines(plugins.listing())


def _background():
    import background
    return _lines(background.listing())


def _proposals():
    import proposals
    return len(proposals.pending())


def _engines():
    import browser
    return {"sandbox": bool(shutil.which("bwrap")), "ffmpeg": bool(shutil.which("ffmpeg")),
            "lightpanda": bool(shutil.which("lightpanda")), "browser": browser.status().removeprefix("Browser: ").rstrip(".")}


def _errors():
    path = config.LOG_FILE
    if not path.exists():
        return []
    with open(path, "rb") as f:
        f.seek(max(0, path.stat().st_size - 40000))
        tail = f.read().decode("utf-8", "replace").splitlines()
    rows = [line for line in tail if line[:4].isdigit()]
    return [sanitize.redact(line)[:300] for line in rows[-_ERROR_LINES:]][::-1]


def snapshot() -> dict:
    """Everything the page shows. Each part fails alone, and says so."""
    return {"now": datetime.now(timezone.utc).isoformat(timespec="seconds"), "commit": _safe(_commit),
            "process": _safe(_process), "resources": _safe(_resources), "tokens": _safe(_tokens),
            "scheduled": _safe(_scheduled), "jobs": _safe(_jobs), "board": _safe(_board), "plugins": _safe(_plugins),
            "background": _safe(_background), "proposals": _safe(_proposals), "engines": _safe(_engines),
            "errors": _safe(_errors)}


def _guarded(handler):
    async def guarded(request):
        from aiohttp import web
        if not host_ok(request.headers.get("Host", "")):
            logger.warning("Dashboard refused a request for host %r", request.headers.get("Host", "")[:80])
            return web.Response(status=403, text="Forbidden")
        response = await handler(request)
        response.headers.update(_HEADERS)
        return response
    return guarded


async def _page(request):
    from aiohttp import web
    return web.Response(body=(_HERE / "index.html").read_bytes(), content_type="text/html", charset="utf-8")


async def _asset(request):
    from aiohttp import web
    name = request.match_info["name"]
    kinds = {"app.js": "application/javascript", "app.css": "text/css"}
    if name not in kinds:
        return web.Response(status=404, text="Not found")
    return web.Response(body=(_HERE / name).read_bytes(), content_type=kinds[name], charset="utf-8")


async def _status(request):
    import asyncio
    from aiohttp import web
    return web.json_response(await asyncio.get_running_loop().run_in_executor(None, snapshot))


def add_routes(application) -> None:
    application.router.add_get("/dashboard", _guarded(_page))
    application.router.add_get("/dashboard/status.json", _guarded(_status))
    application.router.add_get("/dashboard/{name}", _guarded(_asset))
