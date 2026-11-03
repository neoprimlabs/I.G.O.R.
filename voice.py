"""Voice for the dashboard: what the user says, heard by Whisper, and IGOR's replies read aloud.

Hermes (checked 2026-11-03): push-to-talk records the user, a speech-to-text provider turns it into the
message, and `/voice tts` switches spoken replies on and off, read by a separate text-to-speech provider.
Here the page records and plays; this module does the two halves the page cannot.

Hearing is the Whisper call voice notes already use (agents/inbound.py). Measured on the key 2026-11-03:
a five-second clip came back word for word in 1.5 seconds, and the limit is 2000 recordings a day.

Speaking is Groq's Orpheus, so a reply goes to no company that has not already seen it. It needs the
account's owner to accept its terms in the Groq console; until then, and whenever its limit is reached,
the page reads with the browser's own voice instead. Its per-request ceiling of 200 characters is from a
secondary source, so replies go in parts of at most that; each part is one request, played while the
next is fetched, the way the Claude Code reader on the user's PC does it.

What is read is what the user would want said: link addresses, code blocks and markdown marks are left
out, because the user listens rather than reads, and a spoken address is noise.
"""

import logging
import re
import time
from types import SimpleNamespace

import config

logger = logging.getLogger(__name__)

PART_CHARS = 200
MAX_PARTS = 80
VOICES = ("daniel", "troy", "austin", "autumn", "diana", "hannah")
_TIMEOUT_S = 30
_TERMS_RECHECK_S = 3600
_LIMIT_WAIT_S = 60
_blocked = {"until": 0.0, "why": ""}

# Groq picks the decoder from the file name, so the recording's type names it.
_AUDIO_NAMES = {"audio/webm": "voice.webm", "audio/ogg": "voice.ogg", "audio/mp4": "voice.m4a",
                "audio/mpeg": "voice.mp3", "audio/wav": "voice.wav", "audio/x-wav": "voice.wav",
                "audio/flac": "voice.flac"}


class Unavailable(Exception):
    """IGOR's own voice cannot speak right now; the page reads with the browser's instead."""


async def hear(data: bytes, content_type: str) -> str:
    name = _AUDIO_NAMES.get((content_type or "").split(";")[0].strip().lower())
    if name is None:
        raise ValueError(f"not a recording type Whisper reads: {content_type!r}")
    from agents import inbound
    return await inbound._transcribe(data, name)


_FENCE = re.compile(r"```.*?(?:```|$)", re.DOTALL)
_LINK = re.compile(r"!?\[([^\]\n]+)\]\([^)\s]+\)")
_ADDRESS = re.compile(r"<?\b(?:https?://|www\.)\S+>?", re.IGNORECASE)
_TABLE_RULE = re.compile(r"\|?\s*:?-{2,}:?\s*(\|\s*:?-{2,}:?\s*)*\|?")
_MARKER = re.compile(r"^(?:#{1,6}\s+|>\s*|[-*+•]\s+|\d{1,3}[.)]\s+)")
# A route's tag at the end of a reply, such as [Monitor].
_TAG = re.compile(r"\[[A-Z][\w-]*(?: [\w-]+){0,2}\]")
_EMPHASIS = re.compile(r"(?<!\w)[*_](\S(?:[^*_\n]*?\S)?)[*_](?!\w)")


def _plain(line: str) -> str:
    line = line.strip()
    if line.startswith("|"):
        line = ", ".join(cell.strip() for cell in line.strip("|").split("|") if cell.strip())
    line = _MARKER.sub("", line).replace("**", "").replace("__", "").replace("`", "")
    line = _EMPHASIS.sub(r"\1", _TAG.sub("", line))
    return re.sub(r"\s+", " ", line).strip(" -")


def _fit(sentence: str) -> list[str]:
    """A sentence in pieces of at most PART_CHARS: at a comma if one is far enough in, else at a space."""
    pieces = []
    while len(sentence) > PART_CHARS:
        cut = sentence.rfind(", ", 0, PART_CHARS)
        if cut < PART_CHARS // 3:
            cut = sentence.rfind(" ", 0, PART_CHARS)
        if cut <= 0:
            cut = PART_CHARS - 1
        pieces.append(sentence[:cut + 1].strip())
        sentence = sentence[cut + 1:].strip()
    return pieces + [sentence] if sentence else pieces


def speech_parts(text: str) -> list[str]:
    """A reply as it should sound, in parts of at most PART_CHARS. A heading or list item without its own
    full stop gets one, so the voice pauses there instead of running it into the next line."""
    text = _ADDRESS.sub("", _LINK.sub(r"\1", _FENCE.sub("\n(Code left out.)\n", text or "")))
    lines = []
    for raw in text.splitlines():
        if _TABLE_RULE.fullmatch(raw.strip()):
            continue
        line = _plain(raw)
        if not re.search(r"[A-Za-z0-9]", line):
            continue
        ended = line.rstrip(")\"'")
        lines.append(line if ended and ended[-1] in ".!?:;," else line + ".")
    parts, current = [], ""
    for sentence in re.split(r"(?<=[.!?:;])\s+", " ".join(lines)):
        for piece in _fit(sentence):
            if current and len(current) + 1 + len(piece) > PART_CHARS:
                parts.append(current)
                current = piece
            else:
                current = f"{current} {piece}".strip()
    if current:
        parts.append(current)
    return parts[:MAX_PARTS]


def ready() -> tuple[bool, str]:
    """Whether IGOR's own voice can be tried now, and if not, why, in the user's words."""
    if time.time() < _blocked["until"]:
        return False, _blocked["why"]
    return True, ""


def _block(seconds: float, why: str) -> None:
    _blocked.update(until=time.time() + seconds, why=why)
    logger.warning("IGOR's voice is off for %d seconds: %s", seconds, why)


async def speak(line: str, voice: str) -> bytes:
    """One part as WAV audio. Raises Unavailable when the voice cannot be used for a while."""
    import openai
    import usage
    from agents.react import _get_client
    able, why = ready()
    if not able:
        raise Unavailable(why)
    started = time.time()
    try:
        # No retries: a spoken reply that waits out a 429 backoff is worse than the browser's voice now.
        result = await _get_client().with_options(max_retries=0, timeout=_TIMEOUT_S).audio.speech.create(
            model=config.MODELS["voice"], voice=voice, input=line, response_format="wav")
    except openai.RateLimitError as e:
        after = e.response.headers.get("retry-after", "") if getattr(e, "response", None) is not None else ""
        _block(float(after) if after.replace(".", "", 1).isdigit() else _LIMIT_WAIT_S,
               "Groq's voice limit is used up for now")
        logger.warning("Voice 429: %s", str(e)[:300])
        raise Unavailable(_blocked["why"]) from e
    except openai.BadRequestError as e:
        if "model_terms_required" in str(e):
            _block(_TERMS_RECHECK_S, "Groq's voice model is waiting for its terms to be accepted in the Groq console")
            raise Unavailable(_blocked["why"]) from e
        raise
    audio = result.content
    if not audio:
        raise Unavailable("no sound came back")
    # Orpheus is billed by the character, so the ledger counts characters; its daily limit is unmeasured.
    usage.record(config.MODELS["voice"], SimpleNamespace(usage=SimpleNamespace(total_tokens=len(line))),
                 "Voice", ms=round((time.time() - started) * 1000))
    return audio
