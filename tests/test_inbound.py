"""Tests for agents/inbound.py - reading voice notes, images and documents the user sends.

Run: python tests/test_inbound.py   (repo root, no network; needs pypdf)

Hermes transcribes voice notes and describes images for text-only models before the
agent sees the message, and pages documents through read_file. These check the IGOR
version: what each kind of attachment turns into, the limits, the untrusted markers,
and that a long document is sent to React.
"""

import asyncio
import io
import os
import sys
import tempfile
import time
import zipfile
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import config

config.MEMORY_DIR = Path(tempfile.mkdtemp())

_failures: list[str] = []
NOW = datetime(2026, 10, 19, 12, 0, tzinfo=timezone.utc)


def _check(name: str, condition: bool, detail: str = "") -> None:
    print(f"  {'pass' if condition else 'FAIL'}  {name}" + ("" if condition else f"  {detail}"))
    if not condition:
        _failures.append(name)


class _Att:
    def __init__(self, filename, content_type, data=b"", size=None, url=None):
        self.filename, self.content_type, self.data = filename, content_type, data
        self.size = len(data) if size is None else size
        self.url = url or f"https://cdn.example/{filename}"
        self.reads = 0

    async def read(self):
        self.reads += 1
        return self.data


def _pdf(text: str) -> bytes:
    """A one-page PDF whose page draws `text`, built by hand so the test needs no writer."""
    stream = f"BT /F1 24 Tf 72 700 Td ({text}) Tj ET".encode()
    objects = [b"<< /Type /Catalog /Pages 2 0 R >>",
               b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
               b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Contents 4 0 R "
               b"/Resources << /Font << /F1 5 0 R >> >> >>",
               b"<< /Length %d >>\nstream\n" % len(stream) + stream + b"\nendstream",
               b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>"]
    out, offsets = b"%PDF-1.4\n", []
    for i, obj in enumerate(objects, 1):
        offsets.append(len(out))
        out += b"%d 0 obj\n" % i + obj + b"\nendobj\n"
    xref = len(out)
    out += b"xref\n0 %d\n0000000000 65535 f \n" % (len(objects) + 1)
    out += b"".join(b"%010d 00000 n \n" % off for off in offsets)
    out += b"trailer\n<< /Size %d /Root 1 0 R >>\nstartxref\n%d\n%%%%EOF\n" % (len(objects) + 1, xref)
    return out


def _blank_pdf() -> bytes:
    from pypdf import PdfWriter
    writer = PdfWriter()
    writer.add_blank_page(width=612, height=792)
    buf = io.BytesIO()
    writer.write(buf)
    return buf.getvalue()


def _docx(paragraphs) -> bytes:
    body = "".join(f"<w:p><w:r><w:t>{p}</w:t></w:r></w:p>" for p in paragraphs)
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("word/document.xml", f'<w:document xmlns:w="x"><w:body>{body}</w:body></w:document>')
    return buf.getvalue()


def _run(attachments, transcribe=None, describe=None):
    from agents import inbound

    async def no_transcribe(data, name):
        raise AssertionError("transcribe was not expected")

    async def no_describe(urls):
        raise AssertionError("describe was not expected")

    return asyncio.run(inbound.read(attachments, transcribe=transcribe or no_transcribe,
                                    describe=describe or no_describe, now=NOW))


def test_kinds() -> None:
    from agents import inbound
    cases = {("voice-message.ogg", "audio/ogg"): "voice", ("photo.JPG", None): "image",
             ("shot", "image/png"): "image", ("report.pdf", None): "pdf", ("cv.docx", None): "docx",
             ("notes.md", None): "text", ("data", "text/csv"): "text", ("clip.mp4", "video/mp4"): "other",
             ("app.exe", "application/octet-stream"): "other"}
    for (name, ct), kind in cases.items():
        _check(f"{name} is {kind}", inbound._kind(name, ct) == kind, inbound._kind(name, ct))


def test_voice() -> None:
    async def transcribe(data, name):
        return "remind me at five to call the dentist"

    parts, tools = _run([_Att("voice-message.ogg", "audio/ogg", b"OggS...")], transcribe=transcribe)
    _check("a voice note becomes the user's own words", parts == ["(voice note) remind me at five to call the dentist"],
           str(parts))
    _check("with no untrusted marker", "UNTRUSTED" not in parts[0])
    _check("and no tool agent needed", tools is False)

    async def silent(data, name):
        return ""

    async def broken(data, name):
        raise RuntimeError("413")

    _check("silence is reported", "no speech" in _run([_Att("v.ogg", "audio/ogg", b"x")], transcribe=silent)[0][0])
    _check("a failure is reported, not dropped",
           "could not be transcribed" in _run([_Att("v.ogg", "audio/ogg", b"x")], transcribe=broken)[0][0])
    big = _Att("long.ogg", "audio/ogg", size=30 * 1024 * 1024)
    _check("an oversized note is refused before download", "over the 25 MB" in _run([big])[0][0] and big.reads == 0)


def test_images() -> None:
    seen = []

    async def describe(urls):
        seen.append(list(urls))
        return [f"description {i + 1}" for i in range(len(urls))]

    atts = [_Att(f"shot{i}.png", "image/png", b"png") for i in range(4)]
    parts, tools = _run(atts, describe=describe)
    _check("three images go in one call, by URL", seen == [[a.url for a in atts[:3]]], str(seen))
    _check("each description is inside the untrusted markers",
           all("[UNTRUSTED EXTERNAL CONTENT" in p and "description" in p for p in parts[:3]), str(parts[:3]))
    _check("the fourth is reported as not read", "1 more images were not read" in parts[-1], parts[-1])
    _check("images do not need the tool agent", tools is False)

    calls = []

    async def url_fails(urls):
        calls.append(urls[0][:22])
        if urls[0].startswith("https://"):
            raise RuntimeError("400 could not fetch image")
        return ["inline worked"]

    parts, _ = _run([_Att("small.png", "image/png", b"\x89PNG")], describe=url_fails)
    _check("a failed URL falls back to sending the image inline",
           calls == ["https://cdn.example/sm", "data:image/png;base64,"] and "inline worked" in parts[0], str(calls))

    async def always_fails(urls):
        raise RuntimeError("500")

    parts, _ = _run([_Att("big.png", "image/png", size=5 * 1024 * 1024)], describe=always_fails)
    _check("a large image that cannot be fetched is reported", "could not be read" in parts[0], str(parts))


def test_documents() -> None:
    parts, tools = _run([_Att("invoice.pdf", "application/pdf", _pdf("Total due 412.50 USD"))])
    _check("PDF text is read", "Total due 412.50 USD" in parts[0], parts[0][:300])
    _check("with its page count", "1 page," in parts[0], parts[0][:200])
    _check("inside the untrusted markers", "[UNTRUSTED EXTERNAL CONTENT" in parts[0])
    _check("a short document stays with the normal route", tools is False)
    saved = sorted((config.MEMORY_DIR / "inbox").glob("*invoice*.txt"))
    _check("the full text is saved to the inbox", len(saved) == 1 and "412.50" in saved[0].read_text(), str(saved))

    parts, _ = _run([_Att("scan.pdf", "application/pdf", _blank_pdf())])
    _check("a scanned PDF says it has no text layer", "no text layer" in parts[0] and "No text could be taken" in parts[0],
           parts[0])

    parts, _ = _run([_Att("cv.docx", None, _docx(["Brian, builder", "Python &amp; Flutter"]))])
    _check("Word documents are read", "Brian, builder" in parts[0] and "Python & Flutter" in parts[0], parts[0])

    long_text = ("line of the contract\n" * 400).encode()
    parts, tools = _run([_Att("contract.txt", "text/plain", long_text)])
    _check("a long document goes to the tool agent", tools is True)
    _check("only the opening is in the message", len(parts[0]) < 2200 and "read_file pages through the rest" in parts[0],
           str(len(parts[0])))
    _check("and the message names the saved file", "memory/inbox/" in parts[0] or "inbox" in parts[0], parts[0][:200])

    parts, _ = _run([_Att("clip.mp4", "video/mp4", b"x")])
    _check("an unsupported file is named, not ignored", "was not read" in parts[0], parts[0])
    huge = _Att("big.pdf", "application/pdf", size=11 * 1024 * 1024)
    _check("an oversized document is refused before download", "over the 10 MB" in _run([huge])[0][0] and huge.reads == 0)


def test_inbox_is_pruned() -> None:
    from agents import inbound
    inbox = config.MEMORY_DIR / "inbox"
    inbox.mkdir(parents=True, exist_ok=True)
    old = inbox / "20260901-000000-old.txt"
    old.write_text("old", encoding="utf-8")
    stale = NOW.timestamp() - 31 * 86400
    os.utime(old, (stale, stale))
    inbound._save("new.txt", "new", NOW)
    _check("documents older than 30 days are removed on the next save", not old.exists())


def test_combine() -> None:
    from agents import inbound
    _check("the user's words come first", inbound.combine("what is this?", ["[Image 1 ...]"]) == "what is this?\n\n[Image 1 ...]")
    _check("an attachment alone stands on its own", inbound.combine("", ["(voice note) hi"]) == "(voice note) hi")


def test_inbox_reads_are_untrusted() -> None:
    from agents import react
    mem = config.MEMORY_DIR
    _check("reading an inbox file closes state-changing tools",
           react._reads_untrusted("read_file", {"path": str(mem / "inbox" / "20261019-invoice.txt")}))
    _check("the work folder still counts", react._reads_untrusted("read_file", {"path": str(mem / "work" / "g3" / "paper.md")}))
    _check("an ordinary memory file does not", not react._reads_untrusted("read_file", {"path": str(mem / "tasks.md")}))


def test_long_document_skips_the_router() -> None:
    import orchestrator

    async def notify(_):
        pass

    o = orchestrator.Orchestrator(notify)
    routed, classified = [], []

    async def classify(text):
        classified.append(text)
        return "Direct"

    async def route(destination, task, file_mode=False):
        routed.append(destination)
        return "ok"

    o._classify, o._route, o._update_context = classify, route, lambda *a: None
    asyncio.run(o.process(config.AUTHORIZED_USER_ID, "summarise this", prefer_tools=True))
    _check("prefer_tools goes straight to React", routed == ["React"] and classified == [], f"{routed} {classified}")
    asyncio.run(o.process(config.AUTHORIZED_USER_ID, "hello"))
    _check("without it the router still decides", routed[-1] == "Direct" and classified == ["hello"], str(routed))


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            print(name)
            fn()
    if _failures:
        print(f"\n{len(_failures)} FAILED: {', '.join(_failures)}")
        sys.exit(1)
    print("\nall pass")
