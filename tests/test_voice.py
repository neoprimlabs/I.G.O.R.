"""Voice on the dashboard: what is read aloud, the Talk button's route, and IGOR's voice giving way to the browser's.

Run: python tests/test_voice.py   (repo root, no API calls, needs aiohttp)
"""

import asyncio
import shutil
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import config

config.MEMORY_DIR = Path(tempfile.mkdtemp())
config.LOG_FILE = config.MEMORY_DIR / "igor.log"

import httpx
import openai

import context_store
import dashboard
import http_server
import usage
import voice

_failures: list[str] = []


def _check(name: str, condition: bool, detail: str = "") -> None:
    print(f"  {'pass' if condition else 'FAIL'}  {name}" + ("" if condition else f"  {detail}"))
    if not condition:
        _failures.append(name)


def test_what_is_read_aloud():
    reply = ("## Kettle watch\n"
             "- **In stock** at the maker's own shop\n"
             "- The deal post is [from April](https://example.com/deal/123) and is old\n"
             "See https://example.com/kettle for more.\n\n"
             "```python\nprint('hello')\n```\n"
             "| Shop | Price |\n|------|------:|\n| Example Co | $40 |\n"
             "That is all for now. `[Monitor]`")
    parts = voice.speech_parts(reply)
    said = " ".join(parts)
    _check("no addresses", "http" not in said and "example.com" not in said, said)
    _check("a link keeps its words", "from April" in said, said)
    _check("no markdown marks", not any(mark in said for mark in ("**", "##", "`", "|", "[Monitor]")), said)
    _check("code is left out and said to be", "print" not in said and "Code left out." in said, said)
    _check("a heading and list items end in a stop, so the voice pauses",
           said.startswith("Kettle watch. In stock at the maker's own shop. The deal post"), said)
    _check("a table row is read as its cells, its rule dropped", "Shop, Price. Example Co, $40." in said, said)
    _check("the route tag is gone", said.endswith("That is all for now."), said)
    _check("every part fits one request", all(len(p) <= voice.PART_CHARS for p in parts), str([len(p) for p in parts]))

    long = "First, " + ", ".join(f"item number {n} on the list" for n in range(30)) + "."
    pieces = voice.speech_parts(long)
    _check("a long sentence splits under the limit", len(pieces) > 1 and all(len(p) <= voice.PART_CHARS for p in pieces), str(pieces))
    _check("at a comma", all(p.endswith((",", ".")) for p in pieces), str(pieces))
    _check("losing no words", " ".join(pieces).replace(" ", "") == long.replace(" ", ""))
    _check("a word longer than a part is cut, not dropped", sum(map(len, voice.speech_parts("x" * 450))) == 451)
    _check("nothing to say is nothing", voice.speech_parts("") == [] and voice.speech_parts("```\ncode only") == ["(Code left out.)"])
    _check("short sentences share a part", voice.speech_parts("One. Two. Three.") == ["One. Two. Three."])
    _check("snake_case is left alone", "file_name_here" in " ".join(voice.speech_parts("Open file_name_here now.")))
    _check("a long reply stops at the most parts", len(voice.speech_parts("Word. " * 10000)) == voice.MAX_PARTS)


def test_hearing_picks_the_name_from_the_type():
    async def go():
        from agents import inbound
        seen = []

        async def fake(data, name, spoken=False):
            seen.append(name)
            _check("the Talk button is heard as speech, so a spoken okay is kept", spoken)
            return "hello there"

        real, inbound._transcribe = inbound._transcribe, fake
        try:
            for kind, name in (("audio/webm;codecs=opus", "voice.webm"), ("audio/ogg; codecs=opus", "voice.ogg"),
                               ("audio/mp4", "voice.m4a"), ("AUDIO/WAV", "voice.wav")):
                await voice.hear(b"x", kind)
                _check(f"{kind} goes as {name}", seen[-1] == name, str(seen))
            for kind in ("text/plain", "", "audio/x-unknown"):
                try:
                    await voice.hear(b"x", kind)
                    _check(f"refuses {kind!r}", False)
                except ValueError:
                    _check(f"refuses {kind!r}", True)
        finally:
            inbound._transcribe = real

    asyncio.run(go())


def _error(kind, status, message, headers=None):
    response = httpx.Response(status, headers=headers or {}, request=httpx.Request("POST", "https://api.groq.com/openai/v1/audio/speech"))
    return kind(message, response=response, body=None)


class _Speech:
    def __init__(self, outcome):
        self.outcome = outcome
        self.calls = []
        self.options = {}

    def with_options(self, **options):
        self.options = options
        return self

    @property
    def audio(self):
        return self

    @property
    def speech(self):
        return self

    async def create(self, **kwargs):
        self.calls.append(kwargs)
        if isinstance(self.outcome, Exception):
            raise self.outcome
        return type("Binary", (), {"content": self.outcome})()


def test_igor_voice_gives_way():
    async def go():
        from agents import react
        real = react._get_client
        try:
            fake = _Speech(b"RIFF" + b"\0" * 100)
            react._get_client = lambda: fake
            voice._blocked.update(until=0.0, why="")
            audio = await voice.speak("Good evening.", "daniel")
            _check("speaks in WAV on the voice model", audio.startswith(b"RIFF") and fake.calls[0]["model"] == config.MODELS["voice"]
                   and fake.calls[0]["response_format"] == "wav" and fake.calls[0]["voice"] == "daniel", str(fake.calls))
            _check("without waiting out retries", fake.options.get("max_retries") == 0, str(fake.options))
            spent = usage.last_24h().get(config.MODELS["voice"], {})
            _check("characters go in the ledger as Voice", spent.get("Voice") == len("Good evening."), str(spent))

            fake = _Speech(_error(openai.BadRequestError, 400, "Error code: 400 - {'error': {'code': 'model_terms_required'}}"))
            react._get_client = lambda: fake
            try:
                await voice.speak("Hello.", "daniel")
                _check("terms not accepted is Unavailable", False)
            except voice.Unavailable as e:
                _check("terms not accepted is Unavailable, in the user's words", "terms" in str(e), str(e))
            able, why = voice.ready()
            _check("and the voice rests for an hour", not able and "terms" in why and voice._blocked["until"] - time.time() > 3000)
            try:
                await voice.speak("Hello.", "daniel")
            except voice.Unavailable:
                pass
            _check("without asking Groq again meanwhile", len(fake.calls) == 1)

            voice._blocked.update(until=0.0, why="")
            fake = _Speech(_error(openai.RateLimitError, 429, "Rate limit reached", {"retry-after": "7"}))
            react._get_client = lambda: fake
            try:
                await voice.speak("Hello.", "daniel")
                _check("a 429 is Unavailable", False)
            except voice.Unavailable:
                _check("a 429 is Unavailable", True)
            rest = voice._blocked["until"] - time.time()
            _check("for as long as Groq says", 5 < rest <= 7.5, str(rest))

            voice._blocked.update(until=0.0, why="")
            fake = _Speech(_error(openai.BadRequestError, 400, "input too long"))
            react._get_client = lambda: fake
            try:
                await voice.speak("Hello.", "daniel")
                _check("another bad request is raised as itself", False)
            except openai.BadRequestError:
                _check("another bad request is raised as itself, and blocks nothing", voice.ready()[0])

            fake = _Speech(b"")
            react._get_client = lambda: fake
            try:
                await voice.speak("Hello.", "daniel")
                _check("no sound is Unavailable", False)
            except voice.Unavailable:
                _check("no sound is Unavailable", True)
        finally:
            react._get_client = real
            voice._blocked.update(until=0.0, why="")

    asyncio.run(go())


class _Orchestrator:
    def __init__(self):
        self._stop_turn = None
        self._stop_follow = None
        self.heard = []

    async def process(self, user_id, content, prefer_tools=False):
        self.heard.append(content)
        context_store.append("user", content)
        context_store.append("assistant", f"Answer to {content}")
        return f"Answer to {content}", False

    async def follow_through(self, send):
        return 0


def test_the_routes():
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
        key = (config.MEMORY_DIR / "private" / "dashboard.key").read_text(encoding="utf-8").strip()
        good = {"X-IGOR-Key": key}
        sound = b"OggS" + b"\1" * 4000
        heard, said = [], []

        async def hear(data, kind):
            heard.append(kind)
            if heard_as[0] == "boom":
                raise RuntimeError("whisper fell over")
            if heard_as[0] == "type":
                raise ValueError("no")
            return heard_as[0]

        async def speak(line, name):
            said.append((line, name))
            if spoken[0] is None:
                raise voice.Unavailable("Groq's voice limit is used up for now")
            return spoken[0]

        heard_as, spoken = ["Are we missing any kettle drops"], [b"RIFFwav"]
        real = voice.hear, voice.speak
        voice.hear, voice.speak = hear, speak

        async def done():
            for _ in range(200):
                if not dashboard._open_turns and not dashboard._tasks:
                    return
                await asyncio.sleep(0.02)

        try:
            async with ClientSession() as s:
                for path in ("/dashboard/voice", "/dashboard/speech", "/dashboard/speech.wav"):
                    async with s.post(base + path, data=sound, headers={"Content-Type": "audio/ogg"}) as r:
                        _check(f"{path}: no key, nothing", r.status == 401)
                    async with s.post(base + path, data=sound, headers={**good, "Content-Type": "audio/ogg", "Origin": "http://evil.example"}) as r:
                        _check(f"{path}: another page may not post", r.status == 403)
                _check("nothing was heard or said for them", not heard and not said)

                async with s.post(f"{base}/dashboard/voice", data=sound, headers={**good, "Content-Type": "audio/ogg;codecs=opus", "Origin": base}) as r:
                    data = await r.json()
                _check("a recording is taken", r.status == 202 and data["text"] == "(voice note) Are we missing any kettle drops", str(data))
                await done()
                _check("and goes to IGOR labelled as a voice note", fake.heard == ["(voice note) Are we missing any kettle drops"], str(fake.heard))
                _check("the type reaches Whisper", heard == ["audio/ogg"], str(heard))

                for body, kind, status, why in ((sound, "text/plain", 400, "not audio"), (b"OggS", "audio/ogg", 400, "empty"),
                                                (b"\0" * (2 * 1024 * 1024), "audio/ogg", 413, "over the body limit")):
                    async with s.post(f"{base}/dashboard/voice", data=body, headers={**good, "Content-Type": kind}) as r:
                        _check(f"refused: {why}", r.status == status, str(r.status))
                _check("none of them reached Whisper", len(heard) == 1, str(heard))
                for outcome, status, why in (("", 422, "no words"), ("boom", 502, "Whisper failed"), ("type", 415, "a format it cannot read")):
                    heard_as[0] = outcome
                    async with s.post(f"{base}/dashboard/voice", data=sound, headers={**good, "Content-Type": "audio/ogg"}) as r:
                        message = (await r.json())["error"]
                    _check(f"{why} says so", r.status == status and message, f"{r.status} {message}")
                _check("and nothing reached IGOR", len(fake.heard) == 1)

                dashboard._open_turns.update({901, 902, 903})
                calls = len(heard)
                async with s.post(f"{base}/dashboard/voice", data=sound, headers={**good, "Content-Type": "audio/ogg"}) as r:
                    _check("a full queue refuses before transcribing", r.status == 429 and len(heard) == calls)
                dashboard._open_turns.difference_update({901, 902, 903})

                async with s.post(f"{base}/dashboard/speech", json={"text": "## Hi\nSee https://example.com now."}, headers=good) as r:
                    data = await r.json()
                _check("speech: the parts to say", r.status == 200 and data["parts"] == ["Hi. See now."] and data["igor_voice"] is True, str(data))
                voice._blocked.update(until=time.time() + 60, why="Groq's voice limit is used up for now")
                async with s.post(f"{base}/dashboard/speech", json={"text": "Hello."}, headers=good) as r:
                    data = await r.json()
                _check("and that IGOR's voice is resting, with why", data["igor_voice"] is False and "limit" in data["why"], str(data))
                voice._blocked.update(until=0.0, why="")
                for body in ({"text": ""}, {"text": "x" * 20001}, {}, ["Hello."]):
                    async with s.post(f"{base}/dashboard/speech", json=body, headers=good) as r:
                        _check(f"speech refuses {str(body)[:30]}", r.status == 400)

                async with s.post(f"{base}/dashboard/speech.wav", json={"line": "Good evening.", "voice": "daniel"}, headers=good) as r:
                    body = await r.read()
                _check("a part comes back as WAV", r.status == 200 and r.headers["Content-Type"] == "audio/wav" and body == b"RIFFwav")
                _check("in the voice asked for", said[-1] == ("Good evening.", "daniel"), str(said))
                spoken[0] = None
                async with s.post(f"{base}/dashboard/speech.wav", json={"line": "Good evening.", "voice": "daniel"}, headers=good) as r:
                    data = await r.json()
                _check("an unavailable voice is a 503 with why", r.status == 503 and "limit" in data["error"], str(data))
                count = len(said)
                for body, why in (({"line": "x" * 201, "voice": "daniel"}, "a part over the limit"), ({"line": "Hi.", "voice": "nobody"}, "an unknown voice"),
                                  ({"line": "  ", "voice": "daniel"}, "nothing to say"), ({"voice": "daniel"}, "no line")):
                    async with s.post(f"{base}/dashboard/speech.wav", json=body, headers=good) as r:
                        _check(f"refused: {why}", r.status == 400)
                _check("none of them reached Groq", len(said) == count)

                async with s.get(f"{base}/dashboard") as r:
                    _check("the page may play the audio it fetched", "media-src blob:" in r.headers.get("Content-Security-Policy", ""))
        finally:
            voice.hear, voice.speak = real
            await runner.cleanup()
            http_server.set_targets(None, None)

    asyncio.run(go())


def test_the_page():
    folder = Path(dashboard.__file__).parent / "dashboard_page"
    script = (folder / "app.js").read_text(encoding="utf-8")
    page = (folder / "index.html").read_text(encoding="utf-8")
    _check("a Talk button and the read-aloud controls", all(f'id="{name}"' in page for name in ("talk", "aloud", "voice", "voice-test", "voice-note")))
    _check("the page's voices are the server's", all(f'"{name}"' in script for name in voice.VOICES))
    _check("a recording stays under the body limit", "audioBitsPerSecond: 32000" in script and "MAX_RECORD_MS = 115000" in script)
    _check("unprompted posts are not read out on arrival", "!unprompted" in script)


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
