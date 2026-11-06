"""Scripts on events (Hermes' shell hooks), and IGOR over HTTP (its API server and webhooks).

Run: python tests/test_hooks.py   (repo root, no API calls; the HTTP half needs aiohttp)

The sandbox is stood in for by a plain Python subprocess, except on the server, where the real one runs too.
"""

import asyncio
import hashlib
import hmac
import json
import os
import shutil
import sys
import tempfile
import time
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import config

config.MEMORY_DIR = Path(tempfile.mkdtemp())

import hooks
import http_server
import prerun
import sandbox

_failures: list[str] = []
USER = config.AUTHORIZED_USER_ID


def _check(name: str, condition: bool, detail: str = "") -> None:
    print(f"  {'pass' if condition else 'FAIL'}  {name}" + ("" if condition else f"  {detail}"))
    if not condition:
        _failures.append(name)


def _stand_in(argv, network=False, mounts=(), env=None, **kw):
    """The script is the first mount's source. Runs anywhere."""
    return [sys.executable, str(mounts[0][1])]


def _place(name: str, source: str) -> None:
    hooks._folder().mkdir(parents=True, exist_ok=True)
    (hooks._folder() / name).write_text(source, encoding="utf-8")


def _clear() -> None:
    shutil.rmtree(hooks._folder(), ignore_errors=True)


def test_no_hook_costs_nothing() -> None:
    _clear()
    real = prerun.spawn

    async def no_spawn(*a, **k):
        raise AssertionError("a script ran")

    prerun.spawn = no_spawn
    try:
        _check("with none placed, a tool call goes ahead", asyncio.run(hooks.pre_tool("shell", {})) is None)

        async def run():
            hooks.fire("agent_end", route="React")
            await hooks.drain()

        asyncio.run(run())
    finally:
        prerun.spawn = real
    _check("and nothing is spawned", True)


def test_scripts_found_by_name() -> None:
    _clear()
    for name in ("pre_tool_call.py", "pre_tool_call_guard.sh", "agent_end.py", "pre_tool_call.txt", "other.py", "pre_tool_callx.py"):
        _place(name, "")
    names = [p.name for p in hooks.scripts("pre_tool_call")]
    _check("an event's own, and only those", names == ["pre_tool_call.py", "pre_tool_call_guard.sh"], str(names))
    _check("a missing folder is no hooks", hooks.scripts("startup") == [])
    _clear()


def test_blocking() -> None:
    real = sandbox.prepare
    sandbox.prepare = _stand_in
    try:
        _clear()
        _place("pre_tool_call.py", "import json,sys\nd=json.load(sys.stdin)\nprint(json.dumps({'decision':'block','reason':'no '+d['tool_name']+' '+d['tool_input']['command']}))\n")
        reply = asyncio.run(hooks.pre_tool("shell", {"command": "ls"}))
        _check("the payload arrives and JSON blocks", reply and "no shell ls" in reply and reply.startswith("[blocked"), str(reply))
        _place("pre_tool_call.py", "import sys\nsys.stderr.write('policy says no')\nsys.exit(2)\n")
        reply = asyncio.run(hooks.pre_tool("shell", {}))
        _check("exit code 2 blocks, with its reason", reply and "policy says no" in reply, str(reply))
        _place("pre_tool_call.py", "import json\nprint(json.dumps({'action':'block','message':'other shape'}))\n")
        reply = asyncio.run(hooks.pre_tool("shell", {}))
        _check("both of Hermes' shapes", reply and "other shape" in reply, str(reply))
        _place("pre_tool_call.py", "print('all fine')\n")
        _check("anything else lets it through", asyncio.run(hooks.pre_tool("shell", {})) is None)
        _place("pre_tool_call.py", "import sys\nsys.exit(1)\n")
        _check("a crash fails open", asyncio.run(hooks.pre_tool("shell", {})) is None)
        _place("pre_tool_call.py", "this is not python\n")
        _check("so does a syntax error", asyncio.run(hooks.pre_tool("shell", {})) is None)
        limit = hooks._TIMEOUT_S
        hooks._TIMEOUT_S = 1
        _place("pre_tool_call.py", "import time\ntime.sleep(30)\n")
        started = time.time()
        _check("so does a hook that hangs", asyncio.run(hooks.pre_tool("shell", {})) is None)
        _check("cut off at its limit", time.time() - started < 10, str(time.time() - started))
        hooks._TIMEOUT_S = limit
    finally:
        sandbox.prepare = real
        _clear()


def test_observers() -> None:
    real = sandbox.prepare
    sandbox.prepare = _stand_in
    told = []

    async def sender(text):
        told.append(text)

    hooks.set_sender(sender)

    async def fire(event, **fields):
        hooks.fire(event, **fields)
        await hooks.drain()

    try:
        _clear()
        _place("agent_end.py", "import json,sys\nprint('ended on', json.load(sys.stdin)['route'])\n")
        asyncio.run(fire("agent_end", route="React", message="m", response="r"))
        _check("what it prints goes to the user", told == ["ended on React"], str(told))
        told.clear()
        _place("agent_end.py", "pass\n")
        asyncio.run(fire("agent_end", route="React"))
        _check("printing nothing says nothing", told == [])
        _place("agent_end.py", "import json\nprint(json.dumps({'wakeAgent': False}))\n")
        asyncio.run(fire("agent_end", route="React"))
        _check("nor does the quiet mark", told == [])
        _place("agent_end.py", "raise SystemExit(4)\n")
        asyncio.run(fire("agent_end", route="React"))
        _check("nor a failure", told == [])

        async def broken(text):
            raise RuntimeError("no")

        hooks.set_sender(broken)
        _place("agent_end.py", "print('x')\n")
        asyncio.run(fire("agent_end", route="React"))
        _check("a failed send does not break anything", True)
        hooks.set_sender(sender)

        told.clear()
        _place("startup.py", "print('up')\n")
        hooks._started = False

        async def twice():
            hooks.fire_startup()
            await hooks.drain()
            hooks.fire_startup()
            await hooks.drain()

        asyncio.run(twice())
        _check("startup runs once a process, whatever Discord does", told == ["up"], str(told))
        hooks.fire("agent_end", route="x")
        _check("firing with no event loop is harmless", True)
    finally:
        sandbox.prepare = real
        _clear()


def test_sealed_and_wired() -> None:
    from agents import react
    root = Path("/opt/igor/memory/private/cron_scripts/hooks/pre_tool_call.py")
    _check("no tool can place a hook", react._is_secret_file(root))
    _check("nor read the webhook secrets", react._is_secret_file(Path("/opt/igor/memory/private/webhooks.json")))
    _check("scheduled scripts do not list the hooks folder", "hooks" not in prerun.available())


def test_a_hook_stops_a_tool() -> None:
    from agents import react
    calls = []

    async def create(model, **request):
        calls.append(request)
        if len(calls) == 1:
            call = SimpleNamespace(id="c1", type="function", function=SimpleNamespace(name="shell", arguments=json.dumps({"command": "ls"})))
            return SimpleNamespace(choices=[SimpleNamespace(finish_reason="tool_calls", message=SimpleNamespace(content=None, tool_calls=[call]))], usage=None)
        return SimpleNamespace(choices=[SimpleNamespace(finish_reason="stop", message=SimpleNamespace(content="Blocked, as you asked.", tool_calls=None))], usage=None)

    fake = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=create)))
    ran = []

    async def execute(name, inputs, allow_private=False):
        ran.append(name)
        return "ok"

    real = react._get_client, react._execute_tool, sandbox.prepare
    react._get_client, react._execute_tool, sandbox.prepare = (lambda: fake), execute, _stand_in
    _place("pre_tool_call.py", "import sys\nsys.stderr.write('not today')\nsys.exit(2)\n")
    try:
        reply = asyncio.run(react.handle("list the files", [], allowed_tools=["shell"]))
    finally:
        react._get_client, react._execute_tool, sandbox.prepare = real
        _clear()
    _check("the tool did not run", ran == [], str(ran))
    _check("the model is told why", "not today" in calls[1]["messages"][-1]["content"], str(calls[1]["messages"][-1]))


def test_real_sandbox() -> None:
    if not shutil.which("bwrap") or not Path("/opt/igor").is_dir():
        print("  skip  no bubblewrap here - run on the server")
        return
    keep = config.MEMORY_DIR
    config.MEMORY_DIR = Path(tempfile.mkdtemp(dir="/tmp"))
    try:
        _place("pre_tool_call.py", "import json,os,sys\nd=json.load(sys.stdin)\nprint(json.dumps({'decision':'block','reason':str(d['tool_name'])+' keys:'+str(sum(k.endswith('KEY') for k in os.environ))}))\n")
        reply = asyncio.run(hooks.pre_tool("shell", {"command": "ls"}))
        _check("in the real sandbox it reads its input and sees no keys", reply and "shell keys:0" in reply, str(reply))
    finally:
        shutil.rmtree(config.MEMORY_DIR, ignore_errors=True)
        config.MEMORY_DIR = keep


# --- HTTP ---


def _has_aiohttp() -> bool:
    try:
        import aiohttp  # noqa: F401
        return True
    except ImportError:
        print("  skip  no aiohttp here - run on the server")
        return False


KEY = "a-long-enough-test-key"


async def _client():
    from aiohttp.test_utils import TestClient, TestServer
    client = TestClient(TestServer(http_server.app()))
    await client.start_server()
    return client


def _sign(secret: str, raw: bytes, stamp: float = None) -> dict:
    stamp = str(int(time.time() if stamp is None else stamp))
    return {"X-Webhook-Timestamp": stamp,
            "X-Webhook-Signature-V2": hmac.new(secret.encode(), stamp.encode() + b"." + raw, hashlib.sha256).hexdigest()}


def _write_routes(routes: dict) -> None:
    http_server._routes_path().parent.mkdir(parents=True, exist_ok=True)
    http_server._routes_path().write_text(json.dumps({"routes": routes}), encoding="utf-8")


def _reset_http() -> None:
    http_server._hits.clear()
    http_server._seen.clear()
    http_server._runs.clear()
    os.environ.pop("API_SERVER_KEY", None)
    try:
        http_server._routes_path().unlink()
    except OSError:
        pass


def test_off_by_default() -> None:
    _reset_http()
    _check("nothing configured, nothing listens", http_server.enabled() is False)
    os.environ["API_SERVER_KEY"] = "short"
    _check("a short key does not turn it on", http_server.enabled() is False)
    os.environ["API_SERVER_KEY"] = KEY
    _check("a good key does", http_server.enabled() is True)
    _reset_http()
    _write_routes({"open": {"prompt": "x"}})
    _check("a route with no secret does not", http_server.enabled() is False and http_server._routes() == {})
    _write_routes({"good": {"secret": "s"}})
    _check("one with a secret does", http_server.enabled() is True)
    http_server._routes_path().write_text("not json", encoding="utf-8")
    _check("a bad file is no routes", http_server._routes() == {})
    _reset_http()

    async def start():
        return await http_server.start(lambda: None, None)

    os.environ["IGOR_DASHBOARD"] = "off"
    try:
        _check("start stays off too, with the dashboard off", asyncio.run(start()) is False and http_server._runner is None)
    finally:
        del os.environ["IGOR_DASHBOARD"]


def test_api() -> None:
    if not _has_aiohttp():
        return
    _reset_http()
    os.environ["API_SERVER_KEY"] = KEY
    asked = []

    class Fake:
        _pending_follow = "waiting"

        async def process(self, user_id, content):
            asked.append((user_id, content))
            if content == "boom":
                raise RuntimeError("no")
            if content == "stopped":
                return None
            return f"reply to {content} — ok", False

    fake = Fake()
    http_server.set_targets(lambda: fake, None)
    auth = {"Authorization": f"Bearer {KEY}"}

    async def run():
        client = await _client()
        try:
            out = {}
            out["health"] = await (await client.get("/health")).json()
            out["no key"] = (await client.post("/v1/chat/completions", json={"messages": []})).status
            out["wrong key"] = (await client.post("/v1/chat/completions", json={"messages": []}, headers={"Authorization": "Bearer nope"})).status
            out["models no key"] = (await client.get("/v1/models")).status
            out["models"] = await (await client.get("/v1/models", headers=auth)).json()
            out["bad json"] = (await client.post("/v1/chat/completions", data="{nope", headers=auth)).status
            out["no user"] = (await client.post("/v1/chat/completions", json={"messages": [{"role": "system", "content": "x"}]}, headers=auth)).status
            out["image"] = (await client.post("/v1/chat/completions", json={"messages": [{"role": "user", "content": [{"type": "image_url", "image_url": {"url": "x"}}]}]}, headers=auth)).status
            big = {"messages": [{"role": "user", "content": "x" * (2 * 1024 * 1024)}]}
            out["too big"] = (await client.post("/v1/chat/completions", json=big, headers=auth)).status
            good = {"model": "anything", "messages": [{"role": "user", "content": "old"}, {"role": "assistant", "content": "a"},
                                                      {"role": "user", "content": "new question"}]}
            response = await client.post("/v1/chat/completions", json=good, headers=auth)
            out["good"] = await response.json()
            out["parts"] = await (await client.post("/v1/chat/completions", json={"messages": [{"role": "user", "content": [{"type": "text", "text": "in parts"}]}]}, headers=auth)).json()
            out["fail"] = await (await client.post("/v1/chat/completions", json={"messages": [{"role": "user", "content": "boom"}]}, headers=auth)).json()
            out["stopped"] = await (await client.post("/v1/chat/completions", json={"messages": [{"role": "user", "content": "stopped"}]}, headers=auth)).json()
            stream = await client.post("/v1/chat/completions", json={"stream": True, "messages": [{"role": "user", "content": "streamed"}]}, headers=auth)
            out["stream type"] = stream.headers["Content-Type"]
            out["stream"] = (await stream.read()).decode()
            return out
        finally:
            await client.close()

    out = asyncio.run(run())
    _check("health needs no key and says no more", out["health"] == {"status": "ok"}, str(out["health"]))
    _check("no key is refused", out["no key"] == 401)
    _check("nor a wrong one", out["wrong key"] == 401)
    _check("nor for the model list", out["models no key"] == 401)
    _check("the model list has IGOR", out["models"]["data"][0]["id"] == "igor", str(out["models"]))
    _check("bad JSON is a 400", out["bad json"] == 400)
    _check("no user message is a 400", out["no user"] == 400)
    _check("an image is refused, not ignored", out["image"] == 400)
    _check("over 1 MB is refused", out["too big"] in (400, 413), str(out["too big"]))
    choice = out["good"]["choices"][0]
    _check("the newest user message is what is asked", asked[0] == (USER, "new question"), str(asked[0]))
    _check("as the authorised user, through the same door", asked[0][0] == USER)
    _check("the OpenAI shape", out["good"]["object"] == "chat.completion" and choice["message"]["role"] == "assistant" and choice["finish_reason"] == "stop", str(out["good"]))
    _check("text is cleaned as on Discord", "—" not in choice["message"]["content"], choice["message"]["content"])
    _check("follow-through is not left waiting", fake._pending_follow is None)
    _check("text parts are read", asked[1][1] == "in parts", str(asked[1]))
    _check("a failure is an error, not a stop", out["fail"]["choices"][0]["finish_reason"] == "error", str(out["fail"]))
    _check("a stopped turn is cancelled", out["stopped"]["choices"][0]["finish_reason"] == "cancelled", str(out["stopped"]))
    _check("a stream is a stream", out["stream type"].startswith("text/event-stream"), out["stream type"])
    lines = [json.loads(l[6:]) for l in out["stream"].splitlines() if l.startswith("data: {")]
    _check("role, then content, then the finish", lines[0]["choices"][0]["delta"] == {"role": "assistant"}
           and "reply to streamed" in lines[1]["choices"][0]["delta"]["content"] and lines[-1]["choices"][0]["finish_reason"] == "stop", out["stream"])
    _check("it ends as OpenAI's does", out["stream"].rstrip().endswith("data: [DONE]"), out["stream"])

    http_server.set_targets(lambda: None, None)

    async def starting():
        client = await _client()
        try:
            return await (await client.post("/v1/chat/completions", json={"messages": [{"role": "user", "content": "x"}]}, headers=auth)).json()
        finally:
            await client.close()

    _check("before Discord connects it says so", asyncio.run(starting())["choices"][0]["finish_reason"] == "error")

    _reset_http()
    http_server.set_targets(lambda: fake, None)

    async def no_api():
        client = await _client()
        try:
            return (await client.post("/v1/chat/completions", json={"messages": [{"role": "user", "content": "x"}]})).status
        finally:
            await client.close()

    _check("with no key the API is not there at all", asyncio.run(no_api()) == 404)


def test_signatures() -> None:
    raw, now = b'{"a":1}', 1_000_000.0
    good = _sign("s", raw, now)
    _check("the generic form", http_server.signature_ok("s", good, raw, now))
    _check("a wrong secret fails", not http_server.signature_ok("x", good, raw, now))
    _check("a changed body fails", not http_server.signature_ok("s", good, b'{"a":2}', now))
    _check("an old request fails: no replay", not http_server.signature_ok("s", good, raw, now + 301))
    _check("a stamp from the future fails too", not http_server.signature_ok("s", good, raw, now - 301))
    _check("a bad stamp fails", not http_server.signature_ok("s", {**good, "X-Webhook-Timestamp": "soon"}, raw, now))
    _check("no signature fails", not http_server.signature_ok("s", {}, raw, now))
    _check("the old untimed form is not accepted", not http_server.signature_ok("s", {"X-Webhook-Signature": hmac.new(b"s", raw, hashlib.sha256).hexdigest()}, raw, now))
    github = {"X-Hub-Signature-256": "sha256=" + hmac.new(b"s", raw, hashlib.sha256).hexdigest()}
    _check("GitHub's", http_server.signature_ok("s", github, raw))
    _check("GitHub's with a bad digest", not http_server.signature_ok("s", {"X-Hub-Signature-256": "sha256=00"}, raw))
    _check("GitLab's", http_server.signature_ok("s", {"X-Gitlab-Token": "s"}, raw) and not http_server.signature_ok("s", {"X-Gitlab-Token": "x"}, raw))


def test_templates() -> None:
    payload = {"a": {"b": "deep"}, "n": 3, "list": [1, 2]}
    _check("dot notation", http_server.render("{a.b} {n}", payload) == "deep 3")
    _check("a missing key stays as written", http_server.render("{nope} {a.nope}", payload) == "{nope} {a.nope}")
    _check("a list comes as JSON", http_server.render("{list}", payload) == "[1, 2]")
    _check("no template dumps the payload", '"deep"' in http_server.render(None, payload))
    _check("nor does a payload's own braces run again", http_server.render("{x}", {"x": "{a.b}"}) == "{a.b}")
    _check("__raw__", '"deep"' in http_server.render("{__raw__}", payload))
    _check("a filter on equals", http_server._passes([{"field": "a.b", "equals": "deep"}], payload) and not http_server._passes([{"field": "a.b", "equals": "x"}], payload))
    _check("on contains", http_server._passes([{"field": "a.b", "contains": "ee"}], payload) and not http_server._passes([{"field": "nope", "contains": "x"}], payload))


def test_webhooks() -> None:
    if not _has_aiohttp():
        return
    _reset_http()
    told = []

    async def send(text):
        told.append(text)
        return True

    http_server.set_targets(lambda: None, send)
    _write_routes({
        "plain": {"secret": "s1", "deliver_only": True, "prompt": "Pushed: {msg}", "events": ["push"],
                  "filters": [{"field": "ref", "equals": "main"}]},
        "writer": {"secret": "s2", "brief": "say what happened", "prompt": "Event: {__raw__}"},
    })

    async def post(client, route, payload, secret, **headers):
        raw = json.dumps(payload).encode()
        return await client.post(f"/webhooks/{route}", data=raw, headers={**_sign(secret, raw), **headers})

    written = []

    async def write(brief, now=None, recent=None, material=None):
        written.append((brief, material))
        return "A push came in." if "quiet" not in material else "[SILENT]"

    from agents import compose
    real_write = compose.write
    compose.write = write

    async def run():
        client = await _client()
        out = {}
        try:
            out["unknown"] = (await post(client, "nope", {}, "s1")).status
            raw = b"{}"
            out["unsigned"] = (await client.post("/webhooks/plain", data=raw)).status
            out["wrong secret"] = (await post(client, "plain", {"msg": "x"}, "s2", **{"X-GitHub-Event": "push"})).status
            told_after_bad = list(told)
            good = {"msg": "fixed it", "ref": "main"}
            response = await post(client, "plain", good, "s1", **{"X-GitHub-Event": "push", "X-GitHub-Delivery": "d1"})
            out["delivered"] = response.status, await response.json()
            out["duplicate"] = await (await post(client, "plain", good, "s1", **{"X-GitHub-Event": "push", "X-GitHub-Delivery": "d1"})).json()
            out["other event"] = await (await post(client, "plain", good, "s1", **{"X-GitHub-Event": "issue"})).json()
            out["filtered"] = await (await post(client, "plain", {"msg": "x", "ref": "dev"}, "s1", **{"X-GitHub-Event": "push"})).json()
            response = await post(client, "writer", {"what": "a push"}, "s2")
            out["accepted"] = response.status, await response.json()
            await asyncio.gather(*list(http_server._tasks))
            await post(client, "writer", {"what": "quiet"}, "s2")
            await asyncio.gather(*list(http_server._tasks))
            raw = b"plain words"
            response = await client.post("/webhooks/plain", data=raw, headers={**_sign("s1", raw), "X-GitHub-Event": "push"})
            out["not json"] = response.status
            big = b"x" * (2 * 1024 * 1024)
            response = await client.post("/webhooks/plain", data=big, headers=_sign("s1", big))
            out["too big"] = response.status
            return out, told_after_bad
        finally:
            await client.close()

    try:
        out, told_after_bad = asyncio.run(run())
    finally:
        compose.write = real_write
    _check("an unknown route is a 404", out["unknown"] == 404)
    _check("no signature is refused", out["unsigned"] == 401)
    _check("a wrong secret is refused", out["wrong secret"] == 401)
    _check("and neither reached the user", told_after_bad == [], str(told_after_bad))
    _check("a good one is delivered with no model", out["delivered"] == (200, {"status": "delivered"}) and told[0] == "Pushed: fixed it", str(out["delivered"]) + str(told))
    _check("the same delivery twice is one", out["duplicate"] == {"status": "duplicate"} and told.count("Pushed: fixed it") == 1, str(out["duplicate"]))
    _check("an event it does not want is ignored", out["other event"]["reason"] == "event", str(out["other event"]))
    _check("a filter can ignore it", out["filtered"]["reason"] == "filter", str(out["filtered"]))
    _check("a model run is accepted and goes on behind", out["accepted"] == (202, {"status": "accepted"}), str(out["accepted"]))
    _check("from its brief, with the payload as material", written[0][0] == "say what happened" and "a push" in written[0][1], str(written))
    _check("and reaches the user", "A push came in." in told, str(told))
    _check("[SILENT] sends nothing", len([t for t in told if t == "[SILENT]"]) == 0, str(told))
    _check("a body that is not JSON is still handled", out["not json"] == 200)
    _check("over 1 MB is refused", out["too big"] == 413, str(out["too big"]))

    _reset_http()
    told.clear()
    _write_routes({"r": {"secret": "s", "deliver_only": True, "prompt": "x"}})

    async def refused(text):
        return False

    http_server.set_targets(lambda: None, refused)

    async def fails():
        client = await _client()
        try:
            return (await post(client, "r", {}, "s")).status
        finally:
            await client.close()

    _check("a failed delivery says so, so the sender retries", asyncio.run(fails()) == 502)

    _reset_http()
    http_server.set_targets(lambda: None, send)
    _write_routes({"r": {"secret": "s", "deliver_only": True, "prompt": "x"}})

    async def flood():
        client = await _client()
        try:
            codes = []
            for _ in range(http_server._RATE_PER_MINUTE + 2):
                codes.append((await post(client, "r", {}, "s")).status)
            return codes
        finally:
            await client.close()

    codes = asyncio.run(flood())
    _check("30 a minute, then 429", codes[:30] == [200] * 30 and codes[30:] == [429, 429], str(codes))

    _reset_http()
    told.clear()
    _write_routes({"r": {"secret": "s", "prompt": "Event: {what}"}})
    http_server._MODEL_RUNS_A_DAY, keep = 1, http_server._MODEL_RUNS_A_DAY

    async def over():
        client = await _client()
        try:
            first = (await post(client, "r", {"what": "one"}, "s")).status
            await asyncio.gather(*list(http_server._tasks))
            second = (await post(client, "r", {"what": "two"}, "s")).status
            return first, second
        finally:
            await client.close()

    compose.write = write
    try:
        first, second = asyncio.run(over())
    finally:
        compose.write = real_write
        http_server._MODEL_RUNS_A_DAY = keep
    _check("past the day's model runs it is delivered plainly, not dropped", first == 202 and second == 200 and "Event: two" in told, f"{first} {second} {told}")
    _reset_http()

    async def failing_write(brief, now=None, recent=None, material=None):
        raise RuntimeError("no")

    told.clear()
    compose.write = failing_write
    try:
        asyncio.run(http_server._agent_run("r", {}, "the event"))
    finally:
        compose.write = real_write
    _check("a failed run still tells the user", told and "the event" in told[0], str(told))


if __name__ == "__main__":
    print("\nhooks")
    test_no_hook_costs_nothing()
    test_scripts_found_by_name()
    test_blocking()
    test_observers()
    test_sealed_and_wired()
    test_a_hook_stops_a_tool()
    test_real_sandbox()
    print("\nthe HTTP server")
    test_off_by_default()
    test_signatures()
    test_templates()
    test_api()
    test_webhooks()
    print(f"\n{len(_failures)} failed" if _failures else "\nAll passed.")
    sys.exit(1 if _failures else 0)
