"""IGOR's state on a web page: Hermes' web dashboard, at /dashboard on IGOR's HTTP server.

Hermes' runs on 127.0.0.1 with no login, because only the machine itself can reach a loopback port, and
checks the Host header so a page in someone's browser cannot reach it by DNS rebinding. This does both.
The user reaches it through an SSH tunnel on the key they already hold; nothing is opened on the firewall.

It changes nothing but the conversation. Hermes' pages also edit config, keys and skills; IGOR keeps those
in files no tool can reach, and a page that wrote them would be a new way in. What it shows is what
`status` shows and more: the commit, Discord's heartbeat, memory and disk with Hermes' pressure levels,
tokens by the hour with the headroom left, scheduled messages and jobs, the board, plugins, background
commands, proposals waiting, the engines installed and the newest errors. No file contents and no keys:
errors are redacted.

A process with the network on this host could read that, as with Hermes': here that is a plugin or a script
the user placed. Lightpanda's private-network block keeps a web page in IGOR's browser out, and fetch_url
refuses loopback.

Chat is Hermes' Chat tab: IGOR's one conversation, Discord's turns included, read from the store and sent
through the same door as a Discord message. Hermes asks a session token of its chat even on loopback; so
does this, because a plugin can reach the port and must not talk to IGOR as the user. The key is in
memory/private under a .key name, hidden from every sandbox and refused to read_file, and the user's
launcher fetches it over SSH and hands it to the page after the #, which the browser never sends.
"""

import asyncio
import hmac
import json
import logging
import os
import re
import secrets
import shutil
import subprocess
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import urlsplit

import config
import sanitize

logger = logging.getLogger(__name__)

_STARTED = time.time()
_BOOT = secrets.token_hex(4)
_HERE = Path(__file__).resolve().parent / "dashboard_page"
_ERROR_LINES = 10
# Everything the page loads, by name: nothing else under the folder is served. The fonts are OFL, licences beside them.
_ASSETS = {"app.js": "application/javascript", "app.css": "text/css", "igor.jpg": "image/jpeg", "npl.jpg": "image/jpeg",
           "fonts/bebas-neue.woff2": "font/woff2", "fonts/dm-mono-400.woff2": "font/woff2", "fonts/dm-mono-500.woff2": "font/woff2"}
_HEARTBEAT_STALE_S = 180
# Hermes' resource banner: elevated below 128 MiB or 15% free, critical below 64 MiB or 5%; disk below 512 and 256 MB.
_MEMORY_LEVELS = ((64, 0.05, "critical"), (128, 0.15, "elevated"))
_DISK_LEVELS = ((256, "critical"), (512, "elevated"))
_HEADERS = {
    "Content-Security-Policy": "default-src 'none'; script-src 'self'; style-src 'self'; connect-src 'self'; font-src 'self'; img-src 'self' data:",
    "X-Frame-Options": "DENY", "Referrer-Policy": "no-referrer", "Cache-Control": "no-store",
    "X-Content-Type-Options": "nosniff",
}
_KEY_FILE = "dashboard.key"
_CHAT_CHARS = 4000
_CHAT_QUEUE = 3
_CHAT_ROWS = 30
_CHAT_CATCH_UP = 200
_NOTES_KEPT = 50
_PROACTIVE = "[sent proactively]"

# Dashboard messages not yet answered, and replies the store does not keep (commands, errors) as notes.
_open_turns: set[int] = set()
_turn_seq = 0
_notes: list[dict] = []
_note_seq = 0
_tasks: set = set()


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


def _when(moment: datetime) -> str:
    """A time as the user would say it: today 2:20 PM, tomorrow 4:00 AM, Fri 9:00 AM, Mon 9 Nov, 12:00 PM."""
    import clock
    local = moment.astimezone(clock.USER_TZ)
    days = (local.date() - datetime.now(clock.USER_TZ).date()).days
    day = {0: "today", 1: "tomorrow", -1: "yesterday"}.get(days)
    if day is None:
        day = local.strftime("%a") if 1 < days < 7 else f"{local:%a} {local.day} {local:%b},"
    return f"{day} {local.strftime('%I:%M %p').lstrip('0')}"


def _commit():
    done = subprocess.run(["git", "-C", str(config.BASE_DIR), "log", "-1", "--format=%h%x09%s%x09%cI"],
                          capture_output=True, text=True, timeout=5)
    sha, subject, when = (done.stdout.strip().split("\t") + ["", "", ""])[:3]
    return {"sha": sha, "subject": subject, "when": when, "when_text": _when(datetime.fromisoformat(when)) if when else ""}


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
    disk_usage = shutil.disk_usage(config.MEMORY_DIR)
    free_mb = disk_usage.free // (1024 * 1024)
    disk = next((level for mb, level in _DISK_LEVELS if free_mb < mb), "ok")
    return {"memory_total_mb": total, "memory_available_mb": available, "memory": memory,
            "swap_used_mb": info.get("SwapTotal", 0) - info.get("SwapFree", 0), "disk_free_mb": free_mb,
            "disk_total_mb": disk_usage.total // (1024 * 1024), "disk": disk}


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
    roles = {}
    for role, model in config.MODELS.items():
        roles.setdefault(model, []).append(role)
    for model, entry in models.items():
        entry["roles"] = roles.get(model, [])
    return {"hours": [moment.astimezone(clock.USER_TZ).strftime("%I %p").lstrip("0") for moment in moments], "models": models}


def _scheduled():
    path = config.MEMORY_DIR / "scheduled.json"
    entries = json.loads(path.read_text(encoding="utf-8")) if path.exists() else []
    rows = []
    for entry in sorted(entries, key=lambda e: e.get("due") or ""):
        script = entry.get("script")
        rows.append({"when": _when(datetime.fromisoformat(entry["due"])) if entry.get("due") else "",
                     "repeat": entry.get("repeat") or "once",
                     "what": (entry.get("brief") or entry.get("content") or (f"Runs {script}" if script else "(no brief)"))[:120]})
    return rows


# The scheduler's jobs by name. One run once at startup is left out.
_JOB_NAMES = {"morning_digest": "Morning digest", "model_update_check": "Model update check", "job_watch": "Job watch",
              "gig_watch": "Gig watch", "price_watch": "Price watch", "stock_watch": "Stock watch",
              "scheduled_messages": "Scheduled messages", "heartbeat": "Heartbeat", "page_heartbeat": "Page watch",
              "board": "Task board", "presence": "Check-ins", "advocacy_draft": "Weekly advocacy draft",
              "overnight": "Overnight work"}
# overnight.py works 00:00 to 06:59; its hourly tick does nothing the rest of the day.
_NIGHT_ENDS = 7


def _every(interval: timedelta) -> str:
    minutes = round(interval.total_seconds() / 60)
    if minutes < 60:
        return "every minute" if minutes <= 1 else f"every {minutes} minutes"
    hours = round(minutes / 60)
    return "every hour" if hours == 1 else f"every {hours} hours"


def _jobs():
    import clock
    from agents import monitor
    scheduler = monitor._scheduler
    if scheduler is None or not scheduler.running:
        return []
    rows = []
    for job in scheduler.get_jobs():
        if job.id.endswith("_startup") or job.next_run_time is None:
            continue
        upcoming, interval = job.next_run_time, getattr(job.trigger, "interval", None)
        if interval is not None:
            every = _every(interval)
        else:
            fields = {f.name: str(f) for f in getattr(job.trigger, "fields", [])}
            every = "weekly" if fields.get("day_of_week", "*") != "*" else "daily" if fields.get("hour", "*") != "*" else "hourly"
        if job.id == "overnight":
            local = upcoming.astimezone(clock.USER_TZ)
            upcoming = (local + timedelta(days=1)).replace(hour=0) if local.hour >= _NIGHT_ENDS else local
            every = "nightly, midnight to 7 AM"
        rows.append({"name": _JOB_NAMES.get(job.id, job.id.replace("_", " ").capitalize()), "when": _when(upcoming),
                     "every": every, "frequent": interval is not None and interval < timedelta(hours=1),
                     "at": upcoming.timestamp()})
    return sorted(rows, key=lambda r: r["at"])


def _lines(text: str) -> list[str]:
    return [line for line in (text or "").splitlines() if line.strip()]


def _board():
    import board
    text = board.listing()
    return [] if text == board.EMPTY else _lines(text)


def _plugins():
    import plugins
    text = plugins.listing()
    return [] if text == plugins.NONE_PLACED else _lines(text)


def _background():
    import background
    return _lines(background.listing()) if background._jobs else []


def _proposals():
    import proposals
    return len(proposals.pending())


def _engines():
    import browser
    return {"sandbox": bool(shutil.which("bwrap")), "ffmpeg": bool(shutil.which("ffmpeg")),
            "lightpanda": bool(shutil.which("lightpanda")), "browser": browser.status().removeprefix("Browser: ").rstrip(".")}


_LOG_LINE = re.compile(r"^(\d{4}-\d\d-\d\d \d\d:\d\d:\d\d)[,.]\d+ (\w+) ([\w.]+): (.*)$")


def _errors():
    """The newest errors, newest first. A run of the same message with only its numbers changed, such as
    Discord's reconnect attempts, is one row with a count."""
    path = config.LOG_FILE
    if not path.exists():
        return []
    with open(path, "rb") as f:
        f.seek(max(0, path.stat().st_size - 40000))
        tail = f.read().decode("utf-8", "replace").splitlines()
    groups = []
    for line in reversed([line for line in tail if line[:4].isdigit()]):
        found = _LOG_LINE.match(line)
        if found:
            stamp, _, where, what = found.groups()
            # The log's times are the host's own.
            when = _when(datetime.strptime(stamp, "%Y-%m-%d %H:%M:%S").astimezone())
        else:
            when, where, what = "", "", line
        # An API's error body follows its status code: the JSON says it again, with account ids.
        what = sanitize.redact(what).split("{", 1)[0].rstrip(" -:") or what[:80]
        shape = (where, re.sub(r"\d+(\.\d+)?", "#", what))
        if groups and groups[-1]["shape"] == shape:
            groups[-1]["count"] += 1
            continue
        if len(groups) == _ERROR_LINES:
            break
        groups.append({"shape": shape, "when": when, "where": where.removeprefix("agents."), "what": what[:200], "count": 1})
    return [{key: value for key, value in group.items() if key != "shape"} for group in groups]


def snapshot() -> dict:
    """Everything the page shows. Each part fails alone, and says so."""
    return {"now": datetime.now(timezone.utc).isoformat(timespec="seconds"), "commit": _safe(_commit),
            "process": _safe(_process), "resources": _safe(_resources), "tokens": _safe(_tokens),
            "scheduled": _safe(_scheduled), "jobs": _safe(_jobs), "board": _safe(_board), "plugins": _safe(_plugins),
            "background": _safe(_background), "proposals": _safe(_proposals), "engines": _safe(_engines),
            "errors": _safe(_errors)}


_LOCKED = "Chat opens from the IGOR Dashboard shortcut on the PC."


def chat_key() -> str:
    """The chat's key, made on first use and kept until the file is deleted."""
    path = config.MEMORY_DIR / "private" / _KEY_FILE
    try:
        key = path.read_text(encoding="utf-8").strip()
    except OSError:
        key = ""
    if len(key) >= 32:
        return key
    key = secrets.token_urlsafe(32)
    path.parent.mkdir(parents=True, exist_ok=True)
    with os.fdopen(os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600), "w", encoding="utf-8") as f:
        f.write(key + "\n")
    return key


def _key_ok(request) -> bool:
    given = request.headers.get("X-IGOR-Key", "")
    try:
        key = chat_key()
    except OSError as e:
        logger.error("The dashboard chat has no key - %s: %s", type(e).__name__, e)
        return False
    return bool(given) and hmac.compare_digest(given.encode(), key.encode())


def _origin_ok(request) -> bool:
    """A browser names the page a request comes from, and only the dashboard's own may post. A form on
    another site arrives with that site's name, or "null"."""
    origin = request.headers.get("Origin")
    return origin is None or host_ok(urlsplit(origin).netloc)


def _local_time(ts: float) -> str:
    return _when(datetime.fromtimestamp(ts, timezone.utc)).removeprefix("today ")


def _note(role: str, text: str) -> None:
    global _note_seq
    _note_seq += 1
    _notes.append({"id": _note_seq, "role": role, "text": sanitize.clean(text), "at": _local_time(time.time())})
    del _notes[:-_NOTES_KEPT]


def _rows(after: int) -> tuple[list[dict], int]:
    """Stored messages after the one the page saw last, or the newest few on a first look or after a long
    gap, and the id to ask after next time."""
    import context_store
    last = context_store.max_id()
    if after < 0 or last - after > _CHAT_CATCH_UP:
        after = max(0, last - _CHAT_ROWS)
    rows = []
    for row_id, role, content, ts in context_store.rows_after(after):
        unprompted = content.startswith(_PROACTIVE)
        rows.append({"id": row_id, "role": "you" if role == "user" else "igor", "unprompted": unprompted,
                     "text": sanitize.clean(content.removeprefix(_PROACTIVE).strip() if unprompted else content),
                     "at": _local_time(ts)})
        last = max(last, row_id)
    return rows, max(last, after)


def _orchestrator():
    import http_server
    return http_server._get_orchestrator() if http_server._get_orchestrator else None


def _working() -> bool:
    """A dashboard message waiting, or any turn or follow-through running, Discord's included."""
    orchestrator = _orchestrator()
    return bool(_open_turns) or (orchestrator is not None
                                 and (orchestrator._stop_turn is not None or orchestrator._stop_follow is not None))


def _stored(text: str, since: int) -> bool:
    """Whether the store kept this reply. It keeps the reply without the notices added after it."""
    import context_store
    text = text.strip()
    return any(role == "assistant" and content.strip() and text.startswith(content.strip()[:200])
               for _, role, content, _ in context_store.rows_after(since))


async def _turn(number: int, text: str) -> None:
    """One message, as Discord sends it: the reply, then any follow-through rounds. A reply the store does
    not keep, a command's or an error's, goes to the page as a note with the words that asked for it."""
    import context_store
    answered = False
    try:
        orchestrator = _orchestrator()
        if orchestrator is None:
            _note("you", text)
            _note("igor", "I.G.O.R. is still starting. Send that again in a moment.")
            return
        since = context_store.max_id()
        result = await orchestrator.process(config.AUTHORIZED_USER_ID, text)
        reply = result[0] if result else ""
        if reply.strip() and not _stored(reply, since):
            _note("you", text)
            _note("igor", reply)
        answered = True
        _open_turns.discard(number)
        if result is None:
            return

        async def send(more: str) -> None:
            if more.strip() and not _stored(more, since):
                _note("igor", more)

        await orchestrator.follow_through(send)
    except Exception as e:
        logger.error("A dashboard message failed - %s: %s", type(e).__name__, e)
        if not answered:
            _note("you", text)
        _note("igor", "Something went wrong. Details have been logged.")
    finally:
        _open_turns.discard(number)


async def _chat_post(request):
    from aiohttp import web
    global _turn_seq
    if not _origin_ok(request):
        logger.warning("Dashboard chat refused a post from %r", request.headers.get("Origin", "")[:80])
        return web.json_response({"error": "Only the dashboard's own page may send messages."}, status=403)
    if not _key_ok(request):
        return web.json_response({"error": _LOCKED}, status=401)
    try:
        body = await request.json()
    except (ValueError, web.HTTPException):
        return web.json_response({"error": "That was not a message."}, status=400)
    text = body.get("text") if isinstance(body, dict) else None
    if not isinstance(text, str) or not text.strip():
        return web.json_response({"error": "There is nothing to send."}, status=400)
    if len(text) > _CHAT_CHARS:
        return web.json_response({"error": f"A message here stops at {_CHAT_CHARS} characters."}, status=400)
    import commands
    if len(_open_turns) >= _CHAT_QUEUE and commands.parse(text) != "stop":
        return web.json_response({"error": "Wait for the replies already on their way."}, status=429)
    _turn_seq += 1
    _open_turns.add(_turn_seq)
    task = asyncio.create_task(_turn(_turn_seq, text.strip()))
    _tasks.add(task)
    task.add_done_callback(_tasks.discard)
    return web.json_response({"turn": _turn_seq}, status=202)


async def _chat_get(request):
    """New messages since the page last asked. Waiting turns are read before the store, so a turn that
    ends in between still has its rows in this answer."""
    from aiohttp import web
    if not _key_ok(request):
        return web.json_response({"error": _LOCKED}, status=401)
    try:
        after = int(request.query.get("after", "-1"))
        seen = int(request.query.get("notes", "0"))
    except ValueError:
        return web.json_response({"error": "after and notes are numbers"}, status=400)
    waiting, working = sorted(_open_turns), _working()
    rows, last = await asyncio.get_running_loop().run_in_executor(None, _rows, after)
    if after < 0:
        seen = _note_seq
    elif request.query.get("boot") != _BOOT:
        seen = 0
    return web.json_response({"rows": rows, "last": last, "notes": [n for n in _notes if n["id"] > seen],
                              "notes_last": _note_seq, "boot": _BOOT, "waiting": waiting, "working": working})


def _guarded(handler):
    async def guarded(request):
        from aiohttp import web
        if not host_ok(request.headers.get("Host", "")):
            logger.warning("Dashboard refused a request for host %r", request.headers.get("Host", "")[:80])
            return web.Response(status=403, text="Forbidden")
        response = await handler(request)
        cached = response.headers.get("Cache-Control")
        response.headers.update(_HEADERS)
        if cached:
            response.headers["Cache-Control"] = cached
        return response
    return guarded


async def _page(request):
    from aiohttp import web
    return web.Response(body=(_HERE / "index.html").read_bytes(), content_type="text/html", charset="utf-8")


async def _asset(request):
    from aiohttp import web
    name = request.match_info["name"]
    kind = _ASSETS.get(name)
    if kind is None:
        return web.Response(status=404, text="Not found")
    text = kind.startswith(("text/", "application/"))
    response = web.Response(body=(_HERE / name).read_bytes(), content_type=kind, charset="utf-8" if text else None)
    if not text:
        response.headers["Cache-Control"] = "max-age=86400"
    return response


async def _status(request):
    from aiohttp import web
    return web.json_response(await asyncio.get_running_loop().run_in_executor(None, snapshot))


def add_routes(application) -> None:
    try:
        chat_key()
    except OSError as e:
        logger.error("Could not make the dashboard chat's key - %s: %s", type(e).__name__, e)
    application.router.add_get("/dashboard", _guarded(_page))
    application.router.add_get("/dashboard/status.json", _guarded(_status))
    application.router.add_get("/dashboard/chat.json", _guarded(_chat_get))
    application.router.add_post("/dashboard/chat", _guarded(_chat_post))
    application.router.add_get("/dashboard/{name:(fonts/)?[A-Za-z0-9._-]+}", _guarded(_asset))
