"""IGOR over HTTP, off until configured: Hermes' API server and webhook adapter.

The API (Hermes' /v1/chat/completions) is what any OpenAI-style front end speaks, and what IGOR's own
app will. It is on once API_SERVER_KEY is set. Hermes' version is stateless, the client sending the
whole conversation; IGOR has one conversation, in its own store, so only the newest user message is
taken and everything else about the turn is what Discord gets. No images, no tool progress, and no
follow-through, whose later rounds have nowhere to go.

Webhooks (Hermes' adapter) let an outside service start something, on for each route in
memory/private/webhooks.json, a file no tool can reach:
    {"routes": {"my-route": {"secret": "...", "events": ["push"], "prompt": "Pushed: {head_commit.message}",
                             "filters": [{"field": "ref", "equals": "refs/heads/main"}],
                             "deliver_only": true, "brief": "..."}}}
As Hermes': a signature on every request (GitHub's, GitLab's, or the timestamped generic one, with no
unsigned or replayable form), a secret required on every route, 30 requests a minute, a delivery seen
once, 1 MB. deliver_only sends the rendered prompt with no model. Otherwise a read-only run writes the
message from its brief, seeing the payload as outside text, because authenticated does not mean
trusted. On top of Hermes, for a budget where such a run is about 1800 tokens: 24 runs a day, then
plain delivery. Not built: script filters, coalescing, per-route tools, delivery anywhere else.

Bound to loopback, as Hermes' API is by default, so nothing outside the host reaches it without an SSH
tunnel or a step the user takes on purpose: IGOR_HTTP_HOST and Oracle's firewall.
"""

import asyncio
import hashlib
import hmac
import json
import logging
import os
import re
import secrets
import time
from pathlib import Path
from typing import Awaitable, Callable, Optional

import config
import sanitize

logger = logging.getLogger(__name__)

_DEFAULT_PORT = 8642
_MAX_BODY = 1024 * 1024
_MIN_KEY = 16
_MODEL_NAME = "igor"
_KEEPALIVE_S = 10
_RATE_PER_MINUTE = 30
_SEEN_FOR_S = 3600
_SKEW_S = 300
_MODEL_RUNS_A_DAY = 24
_RAW_CHARS = 4000
_FIELD_CHARS = 2000
_DEFAULT_BRIEF = "Tell the user what this event says, briefly, and whether they need to do anything."

_runner = None
_get_orchestrator: Optional[Callable] = None
_send: Optional[Callable[[str], Awaitable[object]]] = None
_hits: dict[str, tuple[float, int]] = {}
_seen: dict[str, float] = {}
_runs: dict[str, int] = {}
_tasks: set = set()
_one_run = asyncio.Semaphore(1)


def _api_key() -> str:
    key = os.getenv("API_SERVER_KEY", "")
    if key and len(key) < _MIN_KEY:
        logger.error("API_SERVER_KEY is under %d characters, so the API stays off", _MIN_KEY)
        return ""
    return key


def _routes_path() -> Path:
    return config.MEMORY_DIR / "private" / "webhooks.json"


def _routes() -> dict:
    """Routes with a secret. A route without one is refused and logged, never left open."""
    try:
        data = json.loads(_routes_path().read_text(encoding="utf-8"))
    except OSError:
        return {}
    except ValueError as e:
        logger.error("webhooks.json is not readable JSON - %s", e)
        return {}
    routes = data.get("routes") if isinstance(data, dict) else None
    good = {}
    for name, route in (routes.items() if isinstance(routes, dict) else ()):
        if isinstance(route, dict) and isinstance(route.get("secret"), str) and route["secret"]:
            good[name] = route
        else:
            logger.error("Webhook route %s has no secret, so it is off", name)
    return good


def enabled() -> bool:
    return bool(_api_key() or _routes())


def _error(status: int, message: str, kind: str = "invalid_request_error"):
    from aiohttp import web
    return web.json_response({"error": {"message": message, "type": kind}}, status=status)


def _authorized(request) -> bool:
    key = _api_key()
    given = request.headers.get("Authorization", "")
    return bool(key) and hmac.compare_digest(given.encode(), f"Bearer {key}".encode())


def _last_user_text(body: dict) -> tuple[Optional[str], str]:
    """(the newest user message, "") or (None, what is wrong)."""
    messages = body.get("messages")
    if not isinstance(messages, list):
        return None, "messages must be a list"
    last = next((m for m in reversed(messages) if isinstance(m, dict) and m.get("role") == "user"), None)
    if last is None:
        return None, "there is no user message"
    content = last.get("content")
    if isinstance(content, list):
        if any(not isinstance(p, dict) or p.get("type") != "text" for p in content):
            return None, "only text is accepted here"
        content = "\n".join(str(p.get("text") or "") for p in content)
    if not isinstance(content, str) or not content.strip():
        return None, "the user message is empty"
    return content, ""


async def _answer(content: str) -> tuple[str, str]:
    """(reply, finish_reason), through the same door as a Discord message."""
    orchestrator = _get_orchestrator() if _get_orchestrator else None
    if orchestrator is None:
        return "I.G.O.R. is still starting.", "error"
    try:
        result = await orchestrator.process(config.AUTHORIZED_USER_ID, content)
    except Exception as e:
        logger.error("An API message failed - %s: %s", type(e).__name__, e)
        return "Something went wrong. Details have been logged.", "error"
    orchestrator._pending_follow = None
    if result is None:
        return "", "cancelled"
    return sanitize.clean(result[0]), "stop"


def _completion(reply: str, finish: str, stamp: int, chat_id: str) -> dict:
    return {"id": chat_id, "object": "chat.completion", "created": stamp, "model": _MODEL_NAME,
            "choices": [{"index": 0, "message": {"role": "assistant", "content": reply}, "finish_reason": finish}]}


def _chunk(chat_id: str, stamp: int, delta: dict, finish: Optional[str] = None) -> bytes:
    data = {"id": chat_id, "object": "chat.completion.chunk", "created": stamp, "model": _MODEL_NAME,
            "choices": [{"index": 0, "delta": delta, "finish_reason": finish}]}
    return f"data: {json.dumps(data)}\n\n".encode()


async def _stream(request, content: str, stamp: int, chat_id: str):
    """One answer, in the stream's shape, with comments while it is worked on so a client's idle
    timeout does not fire: a turn can take minutes behind a rate limit."""
    from aiohttp import web
    response = web.StreamResponse(headers={"Content-Type": "text/event-stream", "Cache-Control": "no-cache"})
    await response.prepare(request)
    await response.write(_chunk(chat_id, stamp, {"role": "assistant"}))
    work = asyncio.create_task(_answer(content))
    while True:
        done, _ = await asyncio.wait({work}, timeout=_KEEPALIVE_S)
        if done:
            break
        await response.write(b": keepalive\n\n")
    reply, finish = work.result()
    if reply:
        await response.write(_chunk(chat_id, stamp, {"content": reply}))
    await response.write(_chunk(chat_id, stamp, {}, finish))
    await response.write(b"data: [DONE]\n\n")
    await response.write_eof()
    return response


async def _chat(request):
    from aiohttp import web
    if not _authorized(request):
        return _error(401, "Invalid API key", "authentication_error")
    try:
        body = await request.json()
    except (ValueError, web.HTTPException):
        return _error(400, "The body must be JSON, under 1 MB")
    if not isinstance(body, dict):
        return _error(400, "The body must be a JSON object")
    content, why = _last_user_text(body)
    if content is None:
        return _error(400, why)
    stamp, chat_id = int(time.time()), f"chatcmpl-{secrets.token_hex(12)}"
    if body.get("stream") is True:
        return await _stream(request, content, stamp, chat_id)
    reply, finish = await _answer(content)
    return web.json_response(_completion(reply, finish, stamp, chat_id))


async def _models(request):
    from aiohttp import web
    if not _authorized(request):
        return _error(401, "Invalid API key", "authentication_error")
    return web.json_response({"object": "list", "data": [{"id": _MODEL_NAME, "object": "model", "owned_by": "igor"}]})


async def _health(request):
    from aiohttp import web
    return web.json_response({"status": "ok"})


def signature_ok(secret: str, headers, raw: bytes, now: Optional[float] = None) -> bool:
    """GitHub's, GitLab's or the timestamped generic form. Hermes also keeps an untimed generic one
    for old senders; it can be replayed for ever and nothing here is old."""
    key = secret.encode()
    github = headers.get("X-Hub-Signature-256")
    if github:
        return hmac.compare_digest(github.encode(), b"sha256=" + hmac.new(key, raw, hashlib.sha256).hexdigest().encode())
    gitlab = headers.get("X-Gitlab-Token")
    if gitlab:
        return hmac.compare_digest(gitlab.encode(), key)
    given, stamp = headers.get("X-Webhook-Signature-V2"), headers.get("X-Webhook-Timestamp")
    if given and stamp:
        try:
            if abs((time.time() if now is None else now) - float(stamp)) > _SKEW_S:
                return False
        except ValueError:
            return False
        return hmac.compare_digest(given.encode(), hmac.new(key, stamp.encode() + b"." + raw, hashlib.sha256).hexdigest().encode())
    return False


def _rate_ok(name: str, now: float) -> bool:
    start, count = _hits.get(name, (now, 0))
    if now - start >= 60:
        start, count = now, 0
    _hits[name] = (start, count + 1)
    return count < _RATE_PER_MINUTE


def _first_sighting(delivery: str, now: float) -> bool:
    for key in [k for k, at in _seen.items() if now - at > _SEEN_FOR_S]:
        _seen.pop(key, None)
    if not delivery or delivery in _seen:
        return not delivery
    _seen[delivery] = now
    return True


def _field(payload, path: str):
    node = payload
    for part in path.split("."):
        if not isinstance(node, dict) or part not in node:
            return None
        node = node[part]
    return node


_SLOT = re.compile(r"\{([A-Za-z0-9_.-]+)\}")


def render(template: Optional[str], payload) -> str:
    """Hermes' templates: {a.b} from the payload, {__raw__} the whole of it, a missing key left as written."""
    raw = json.dumps(payload, indent=1, default=str)[:_RAW_CHARS]
    if not template:
        return raw

    def slot(m):
        if m.group(1) == "__raw__":
            return raw
        value = _field(payload, m.group(1))
        if value is None:
            return m.group(0)
        return json.dumps(value, default=str)[:_FIELD_CHARS] if isinstance(value, (dict, list)) else str(value)[:_FIELD_CHARS]

    return _SLOT.sub(slot, template)


def _passes(filters, payload) -> bool:
    for rule in filters if isinstance(filters, list) else ():
        value = _field(payload, str(rule.get("field") or "")) if isinstance(rule, dict) else None
        if "equals" in rule and value != rule["equals"]:
            return False
        if "contains" in rule and str(rule["contains"]) not in str(value if value is not None else ""):
            return False
    return True


def _run_allowed(now: float) -> bool:
    day = time.strftime("%Y-%m-%d", time.gmtime(now))
    if _runs.get(day, 0) >= _MODEL_RUNS_A_DAY:
        return False
    _runs.clear() if day not in _runs else None
    _runs[day] = _runs.get(day, 0) + 1
    return True


async def _agent_run(name: str, route: dict, text: str) -> None:
    """A scheduled message's run: read-only tools, the payload as outside text. Never raises."""
    from agents import compose
    async with _one_run:
        try:
            written = await compose.write(str(route.get("brief") or _DEFAULT_BRIEF), material=text)
        except Exception as e:
            logger.error("Webhook %s run failed - %s: %s", name, type(e).__name__, e)
            written = None
    written = (written or "").strip() or f"An outside event came in on {name}:\n{text[:1500]}"
    if "[SILENT]" in written:
        logger.info("Webhook %s run stayed silent", name)
        return
    try:
        await _send(written)
    except Exception as e:
        logger.error("Could not send webhook %s's message - %s: %s", name, type(e).__name__, e)


async def _webhook(request):
    from aiohttp import web
    name = request.match_info["name"]
    route = _routes().get(name)
    if route is None:
        return web.json_response({"error": "unknown route"}, status=404)
    now = time.time()
    if not _rate_ok(name, now):
        return web.json_response({"error": "rate limited"}, status=429)
    try:
        raw = await request.read()
    except web.HTTPException:
        return web.json_response({"error": "too large"}, status=413)
    if not signature_ok(route["secret"], request.headers, raw, now):
        logger.warning("Webhook %s refused a bad signature", name)
        return web.json_response({"error": "bad signature"}, status=401)
    delivery = (request.headers.get("X-GitHub-Delivery") or request.headers.get("webhook-id")
                or request.headers.get("X-Request-ID") or "")
    if not _first_sighting(f"{name}:{delivery}" if delivery else "", now):
        return web.json_response({"status": "duplicate"})
    try:
        payload = json.loads(raw.decode("utf-8", "replace"))
    except ValueError:
        payload = {"text": raw.decode("utf-8", "replace")}
    event = (request.headers.get("X-GitHub-Event") or request.headers.get("X-Gitlab-Event")
             or (payload.get("event_type") if isinstance(payload, dict) else None))
    events = route.get("events")
    if isinstance(events, list) and events and event not in events:
        return web.json_response({"status": "ignored", "reason": "event"})
    if not _passes(route.get("filters"), payload):
        return web.json_response({"status": "ignored", "reason": "filter"})
    text = render(route.get("prompt"), payload)
    if _send is None:
        return web.json_response({"error": "not ready"}, status=503)
    plain = route.get("deliver_only") is True
    if not plain and not _run_allowed(now):
        logger.warning("Webhook %s delivered plainly: %d model runs today", name, _MODEL_RUNS_A_DAY)
        plain = True
    if plain:
        try:
            ok = await _send(text[:3500])
        except Exception as e:
            logger.error("Webhook %s could not deliver - %s: %s", name, type(e).__name__, e)
            ok = False
        # The sender retries on a 502, as Hermes' does.
        return web.json_response({"status": "delivered"}) if ok else web.json_response({"error": "not delivered"}, status=502)
    task = asyncio.create_task(_agent_run(name, route, text))
    _tasks.add(task)
    task.add_done_callback(_tasks.discard)
    return web.json_response({"status": "accepted"}, status=202)


def app():
    from aiohttp import web
    application = web.Application(client_max_size=_MAX_BODY)
    application.router.add_get("/health", _health)
    if _api_key():
        application.router.add_post("/v1/chat/completions", _chat)
        application.router.add_get("/v1/models", _models)
    application.router.add_post("/webhooks/{name}", _webhook)
    return application


def set_targets(get_orchestrator: Callable, send: Callable[[str], Awaitable[object]]) -> None:
    global _get_orchestrator, _send
    _get_orchestrator, _send = get_orchestrator, send


async def start(get_orchestrator: Callable, send: Callable[[str], Awaitable[object]]) -> bool:
    """Whether it is listening. Once a process; off with nothing configured. Never raises: IGOR's
    main door is Discord, and a port in use must not keep it shut."""
    global _runner
    set_targets(get_orchestrator, send)
    if _runner is not None:
        return True
    if not enabled():
        return False
    from aiohttp import web
    host = os.getenv("IGOR_HTTP_HOST", "127.0.0.1")
    try:
        port = int(os.getenv("IGOR_HTTP_PORT", str(_DEFAULT_PORT)))
        runner = web.AppRunner(app(), access_log=None)
        await runner.setup()
        await web.TCPSite(runner, host, port).start()
    except Exception as e:
        logger.error("The HTTP server could not start - %s: %s", type(e).__name__, e)
        return False
    _runner = runner
    logger.info("HTTP server listening on %s:%d (API %s, %d webhook routes)", host, port,
                "on" if _api_key() else "off", len(_routes()))
    return True
