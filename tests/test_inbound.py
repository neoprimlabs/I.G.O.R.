"""Tests for agents/inbound.py - reading voice notes, images, documents and videos the user sends.

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


def _run(attachments, transcribe=None, describe=None, digest=None, ask=""):
    from agents import inbound

    async def no_transcribe(data, name):
        raise AssertionError("transcribe was not expected")

    async def no_describe(urls):
        raise AssertionError("describe was not expected")

    async def no_digest(text, ask=""):
        return ""

    return asyncio.run(inbound.read(attachments, transcribe=transcribe or no_transcribe,
                                    describe=describe or no_describe, now=NOW,
                                    digest=digest or no_digest, ask=ask))


def test_kinds() -> None:
    from agents import inbound
    cases = {("voice-message.ogg", "audio/ogg"): "voice", ("photo.JPG", None): "image",
             ("shot", "image/png"): "image", ("report.pdf", None): "pdf", ("cv.docx", None): "docx",
             ("notes.md", None): "text", ("data", "text/csv"): "text", ("clip.mp4", "video/mp4"): "video",
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

    parts, _ = _run([_Att("cv.docx", None, _docx(["Sam, builder", "Python &amp; Flutter"]))])
    _check("Word documents are read", "Sam, builder" in parts[0] and "Python & Flutter" in parts[0], parts[0])

    long_text = ("line of the contract\n" * 400).encode()
    parts, tools = _run([_Att("contract.txt", "text/plain", long_text)])
    _check("a long document goes to the tool agent", tools is True)
    _check("only the opening is in the message", len(parts[0]) < 2200 and "read_file pages through the rest" in parts[0],
           str(len(parts[0])))
    _check("and the message names the saved file", "memory/inbox/" in parts[0] or "inbox" in parts[0], parts[0][:200])

    # Live 2026-10-30: a 19,000-character transcript. React could hold two read windows
    # of it, so the whole document is summarised on arrival by one 20b call.
    asked = []

    async def summarise(text, ask=""):
        asked.append((text, ask))
        return "A contract between two firms. Payment falls due in 30 days."

    parts, tools = _run([_Att("contract.txt", "text/plain", long_text)], digest=summarise, ask="any traps?")
    _check("a long document is summarised on arrival", "Payment falls due in 30 days" in parts[0], parts[0][:300])
    _check("the summary replaces the opening", "line of the contract" not in parts[0], parts[0][:300])
    _check("it is handed the whole of the text", asked and asked[0][0].count("line of the contract") == 400)
    _check("and the user's words", asked and asked[0][1] == "any traps?")
    _check("the message says what it is and where the exact text is",
           "another model's summary of the whole of it" in parts[0] and "read_file has the exact text" in parts[0], parts[0][:300])
    _check("it still goes to the tool agent", tools is True)
    _check("and the summary stays inside the untrusted markers",
           parts[0].index("UNTRUSTED EXTERNAL") < parts[0].index("Payment falls due")
           < parts[0].index("END UNTRUSTED"), parts[0][:300])

    huge_text = ("line of the contract\n" * 2000).encode()
    parts, _ = _run([_Att("contract.txt", "text/plain", huge_text)], digest=summarise)
    _check("a document past one call's limit says how far the summary reaches",
           "summary of its first 20,000 characters" in parts[0], parts[0][:300])

    async def too_long(text, ask=""):
        return "A whole point.\n" * 60 + "A point cut in ha" + "x" * 9000

    parts, _ = _run([_Att("contract.txt", "text/plain", long_text)], digest=too_long)
    _check("a summary that runs long is held to size", len(parts[0]) < 2200, str(len(parts[0])))
    _check("and stops at the end of a line", "A whole point.\n[END" in parts[0] and "A point cut" not in parts[0], parts[0][-200:])

    async def down(text, ask=""):
        raise RuntimeError("20b is down")

    parts, tools = _run([_Att("contract.txt", "text/plain", long_text)], digest=down)
    _check("a failed summary falls back to the opening",
           "line of the contract" in parts[0] and "read_file pages through the rest" in parts[0] and tools is True, parts[0][:300])
    asked.clear()
    parts, _ = _run([_Att("note.txt", "text/plain", b"A short note.")], digest=summarise)
    _check("a short document costs no call", "A short note." in parts[0] and not asked)
    many = [_Att(f"c{n}.txt", "text/plain", long_text) for n in range(4)]
    _run(many, digest=summarise)
    _check("at most two summaries a message", len(asked) == 2, str(len(asked)))

    parts, _ = _run([_Att("game.exe", "application/octet-stream", b"x")])
    _check("an unsupported file is named, not ignored", "was not read" in parts[0], parts[0])
    huge = _Att("big.pdf", "application/pdf", size=11 * 1024 * 1024)
    _check("an oversized document is refused before download", "over the 10 MB" in _run([huge])[0][0] and huge.reads == 0)


def _xlsx() -> bytes:
    """A two-sheet workbook as Excel writes it: shared strings, an inline string, a number."""
    m = 'xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"'
    r = 'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"'
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("xl/workbook.xml", f'<workbook {m} {r}><sheets><sheet name="Invoices" sheetId="1" r:id="rId1"/>'
                                      f'<sheet name="Notes" sheetId="2" r:id="rId2"/></sheets></workbook>')
        z.writestr("xl/_rels/workbook.xml.rels",
                   '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
                   '<Relationship Id="rId1" Target="worksheets/sheet1.xml"/>'
                   '<Relationship Id="rId2" Target="/xl/worksheets/sheet2.xml"/></Relationships>')
        z.writestr("xl/sharedStrings.xml", f'<sst {m}><si><t>Client</t></si><si><t>Total</t></si>'
                                           f'<si><r><t>Acme </t></r><r><t>Ltd</t></r></si></sst>')
        z.writestr("xl/worksheets/sheet1.xml", f'<worksheet {m}><sheetData>'
                   '<row r="1"><c r="A1" t="s"><v>0</v></c><c r="B1" t="s"><v>1</v></c></row>'
                   '<row r="2"><c r="A2" t="s"><v>2</v></c><c r="B2"><v>412.5</v></c></row>'
                   '</sheetData></worksheet>')
        z.writestr("xl/worksheets/sheet2.xml", f'<worksheet {m}><sheetData><row r="1">'
                   '<c r="A1" t="inlineStr"><is><t>Paid on the 3rd</t></is></c></row></sheetData></worksheet>')
    return buf.getvalue()


def test_spreadsheets_and_notebooks() -> None:
    from agents import inbound
    _check("xlsx and ipynb are recognised", inbound._kind("q3.xlsx", None) == "xlsx" and
           inbound._kind("eda.ipynb", None) == "ipynb")
    text, facts = inbound._xlsx_text(_xlsx())
    _check("every sheet is read, by name", "## Sheet: Invoices" in text and "## Sheet: Notes" in text, text)
    _check("shared strings, rich text, numbers and inline strings come through",
           "Client\tTotal" in text and "Acme Ltd\t412.5" in text and "Paid on the 3rd" in text, text)
    _check("the facts count the sheets", facts == "an Excel workbook, 2 sheets", facts)
    _check("a broken workbook says so", inbound._xlsx_text(b"not a zip")[1].startswith("could not be read"))

    import json
    notebook = json.dumps({"cells": [
        {"cell_type": "markdown", "source": ["# Sales\n", "Quarterly look"]},
        {"cell_type": "code", "source": "df.groupby('region').sum()",
         "outputs": [{"output_type": "stream", "text": ["east 10\n", "west 12\n"]},
                     {"output_type": "execute_result", "data": {"text/plain": "x" * 2500}}]}]}).encode()
    text, facts = inbound._ipynb_text(notebook)
    _check("notebook cells are read in order", text.index("# Sales") < text.index("df.groupby"), text[:200])
    _check("with their outputs", "east 10" in text and "west 12" in text)
    _check("a huge output is cut", "[output cut]" in text and "x" * 2001 not in text)
    _check("the facts count the cells", facts == "a Jupyter notebook, 2 cells", facts)

    parts, _ = _run([_Att("q3.xlsx", None, _xlsx())])
    _check("a workbook attachment lands in the message", "Acme Ltd" in parts[0] and "Excel workbook" in parts[0], parts[0][:200])


def _pptx(only_pictures=False) -> bytes:
    """A deck as PowerPoint writes it, with the second-named slide shown first, notes on one and
    a slide that holds only a picture."""
    p = 'xmlns:p="http://schemas.openxmlformats.org/presentationml/2006/main"'
    a = 'xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main"'
    r = 'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"'
    rel = 'xmlns="http://schemas.openxmlformats.org/package/2006/relationships"'

    def slide(*lines):
        body = "".join(f"<a:p><a:r><a:t>{line}</a:t></a:r></a:p>" for line in lines)
        return f"<p:sld {p} {a}><p:txBody>{body}</p:txBody></p:sld>"

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("ppt/presentation.xml", f'<p:presentation {p} {r}><p:sldIdLst><p:sldId r:id="rId2"/>'
                                           '<p:sldId r:id="rId1"/><p:sldId r:id="rId3"/></p:sldIdLst></p:presentation>')
        z.writestr("ppt/_rels/presentation.xml.rels",
                   f'<Relationships {rel}><Relationship Id="rId1" Target="slides/slide1.xml"/>'
                   '<Relationship Id="rId2" Target="/ppt/slides/slide2.xml"/>'
                   '<Relationship Id="rId3" Target="slides/slide3.xml"/></Relationships>')
        z.writestr("ppt/slides/slide1.xml", slide() if only_pictures else slide("Named first, shown second", "Kettle sales up"))
        z.writestr("ppt/slides/slide2.xml", slide() if only_pictures else slide("Named second, shown first"))
        z.writestr("ppt/slides/slide3.xml", slide())
        z.writestr("ppt/slides/_rels/slide1.xml.rels",
                   f'<Relationships {rel}><Relationship Type="http://x/relationships/notesSlide" '
                   'Target="../notesSlides/notesSlide1.xml"/></Relationships>')
        z.writestr("ppt/notesSlides/notesSlide1.xml", slide("Say the kettle part slowly", "2"))
    return buf.getvalue()


def _epub(locked=False) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("META-INF/container.xml",
                   '<container xmlns="urn:oasis:names:tc:opendocument:xmlns:container"><rootfiles>'
                   '<rootfile full-path="OEBPS/book.opf"/></rootfiles></container>')
        z.writestr("OEBPS/book.opf",
                   '<package xmlns="http://www.idpf.org/2007/opf" xmlns:dc="http://purl.org/dc/elements/1.1/">'
                   '<metadata><dc:title>Kettles Through Time</dc:title></metadata>'
                   '<manifest><item id="a" href="text/one%20a.xhtml"/><item id="b" href="text/two.xhtml"/>'
                   '<item id="f" href="font.otf"/></manifest>'
                   '<spine><itemref idref="b"/><itemref idref="a"/></spine></package>')
        z.writestr("OEBPS/text/one a.xhtml", "<html><head><title>x</title><style>p{}</style></head>"
                                             "<body><p>The second chapter &amp; more</p></body></html>")
        z.writestr("OEBPS/text/two.xhtml", "<html><body><h1>Chapter one</h1><p>Steel came first.</p></body></html>")
        z.writestr("OEBPS/font.otf", b"not text")
        uri = "OEBPS/text/two.xhtml" if locked else "OEBPS/font.otf"
        z.writestr("META-INF/encryption.xml",
                   '<encryption xmlns="urn:oasis:names:tc:opendocument:xmlns:container" '
                   'xmlns:enc="http://www.w3.org/2001/04/xmlenc#"><enc:EncryptedData><enc:CipherData>'
                   f'<enc:CipherReference URI="{uri}"/></enc:CipherData></enc:EncryptedData></encryption>')
    return buf.getvalue()


def _odf(body: str) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("content.xml", '<office:document-content xmlns:office="urn:oasis:names:tc:opendocument:xmlns:office:1.0" '
                                  'xmlns:text="urn:oasis:names:tc:opendocument:xmlns:text:1.0" '
                                  'xmlns:table="urn:oasis:names:tc:opendocument:xmlns:table:1.0">'
                                  f'<office:body>{body}</office:body></office:document-content>')
    return buf.getvalue()


def test_slides_books_and_opendocument() -> None:
    from agents import inbound
    _check("the new kinds are recognised",
           [inbound._kind(n, None) for n in ("a.pptx", "b.epub", "c.odt", "d.ods", "e.odp")]
           == ["pptx", "epub", "odf", "odf", "odf"])
    _check("the old binary formats are still turned away, not read as noise",
           inbound._kind("old.ppt", None) == "other" and inbound._kind("old.doc", None) == "other")

    text, facts = inbound._pptx_text(_pptx())
    _check("slides come in the deck's order, not by file name",
           text.index("Named second, shown first") < text.index("Named first, shown second"), text)
    _check("each is numbered as shown", text.startswith("[slide 1]\nNamed second"), text)
    _check("speaker notes come with their slide", "[notes] Say the kettle part slowly" in text, text)
    _check("without the page number the notes also carry", "[notes] Say the kettle part slowly\n2" not in text, text)
    _check("the facts count slides and say what was not read",
           facts == "a PowerPoint deck, 3 slides, 1 with no text on the slide, and pictures are not read", facts)
    # Found on python-pptx's own sample deck: blank slides, words only in the notes, and nothing came back.
    text, facts = inbound._pptx_text(_pptx(only_pictures=True))
    _check("a deck of pictures still gives its speaker notes", "Say the kettle part slowly" in text, repr(text))
    _check("a broken deck says so", inbound._pptx_text(b"not a zip")[1].startswith("could not be read"))
    parts, _ = _run([_Att("q3.pptx", None, _pptx())])
    _check("a deck attachment lands in the message, as outside text",
           "Kettle sales up" in parts[0] and "UNTRUSTED" in parts[0], parts[0][:200])

    text, facts = inbound._epub_text(_epub())
    _check("a book reads in its spine's order", text.index("Chapter one") < text.index("The second chapter"), text)
    _check("escapes are undone, a chapter's name is decoded, and styles are left out",
           "The second chapter & more" in text and "p{}" not in text, text)
    _check("the facts give the title", facts == "an EPUB book, Kettles Through Time, 2 sections", facts)
    _check("a font listed as encrypted is not a locked book", "Steel came first" in text)
    text, facts = inbound._epub_text(_epub(locked=True))
    _check("a locked chapter is said outright, not read as noise", text == "" and "copy-protected" in facts, facts)
    _check("a broken book says so", inbound._epub_text(b"not a zip")[1].startswith("could not be read"))
    parts, _ = _run([_Att("book.epub", "application/epub+zip", _epub(locked=True))])
    _check("and the user is told", "copy-protected" in parts[0], parts[0][:200])

    text, facts = inbound._odf_text(_odf('<office:text><text:h>Plan</text:h><text:p>Buy a <text:span>kettle</text:span></text:p>'
                                         '</office:text>'))
    _check("OpenDocument text reads in order", text == "Plan\nBuy a kettle" and facts == "an OpenDocument file", text)
    text, facts = inbound._odf_text(_odf('<office:spreadsheet><table:table><table:table-row><table:table-cell><text:p>Item</text:p>'
                                         '</table:table-cell><table:table-cell><text:p>Price</text:p></table:table-cell>'
                                         '</table:table-row><table:table-row><table:table-cell/></table:table-row>'
                                         '</table:table></office:spreadsheet>'))
    _check("a spreadsheet keeps its columns, and empty rows drop", text == "Item\tPrice" and "spreadsheet" in facts, repr(text))
    text, _ = inbound._odf_text(_odf('<office:spreadsheet><table:table><table:table-row><table:table-cell><text:p>Price</text:p>'
                                     '<text:p>in dollars</text:p></table:table-cell></table:table-row></table:table></office:spreadsheet>'))
    _check("two lines in a cell stay two words", text == "Price in dollars", repr(text))
    _check("a broken file says so", inbound._odf_text(b"not a zip")[1].startswith("could not be read"))


def test_zip_bomb() -> None:
    from agents import inbound
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("word/document.xml", b"<w:p>" + b"a" * (inbound._MAX_UNPACKED + 1))
        z.writestr("xl/workbook.xml", b"<x/>")
        z.writestr("ppt/presentation.xml", b"<x/>")
        z.writestr("content.xml", b"<x/>")
        z.writestr("META-INF/container.xml", b"<x/>")
    bomb = buf.getvalue()
    _check("the fixture is small enough to arrive", len(bomb) < inbound._MAX_DOC_BYTES, str(len(bomb)))
    for name, read in (("Word", inbound._docx_text), ("Excel", inbound._xlsx_text), ("PowerPoint", inbound._pptx_text),
                       ("EPUB", inbound._epub_text), ("OpenDocument", inbound._odf_text)):
        text, facts = read(bomb)
        _check(f"{name}: one that unpacks too large is refused", text == "" and facts.startswith("could not be read"), facts)


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
    alone = inbound.combine("", ["[Document the user sent: a.txt]"])
    _check("a document alone says the user sent no words, and what to do", "no message" in alone and "what it says" in alone, alone)
    _check("with their words it does not", "no message" not in inbound.combine("look", ["[Document the user sent: a.txt]"]))


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

    async def route(destination, task, file_mode=False, carry=None):
        routed.append(destination)
        return "ok"

    o._classify, o._route, o._update_context = classify, route, lambda *a, **k: None
    asyncio.run(o.process(config.AUTHORIZED_USER_ID, "summarise this", prefer_tools=True))
    _check("prefer_tools goes straight to React", routed == ["React"] and classified == [], f"{routed} {classified}")
    asyncio.run(o.process(config.AUTHORIZED_USER_ID, "hello"))
    _check("without it the router still decides", routed[-1] == "Direct" and classified == ["hello"], str(routed))


def test_video_kinds():
    from agents import inbound
    _check("a video type", inbound._kind("a.mp4", "video/mp4") == "video")
    _check("a phone's mov with no type", inbound._kind("a.MOV", None) == "video")
    _check("a video type wins over an extension that could be sound", inbound._kind("a.webm", "video/webm") == "video")
    _check("webm that says audio stays a voice note", inbound._kind("a.webm", "audio/webm") == "voice")
    _check("webm with no type stays a voice note", inbound._kind("a.webm", None) == "voice")


def _fake_ffmpeg(duration="42.5", sound=True, frames=True, probe_code=0):
    calls = []

    async def fake(folder, program, *args):
        calls.append((program, args))
        if program == "ffprobe":
            return probe_code, duration if probe_code == 0 else ""
        out = args[-1].rsplit("/", 1)[-1]
        wanted = sound if out == "sound.ogg" else frames
        if not wanted:
            return 1, ""
        (folder / out).write_bytes(b"made")
        return 0, ""

    return fake, calls


def _run_video(fake, transcribe=None, describe=None, digest=None, attachments=None):
    from agents import inbound
    import shutil
    held = inbound._ffmpeg, shutil.which
    inbound._ffmpeg = fake
    shutil.which = lambda name, *a, **k: f"/usr/bin/{name}"

    async def words(data, name):
        return "the kettle is back in stock"

    async def sees(images):
        return [f"view {i + 1}" for i in range(len(images))]

    async def short(text, ask=""):
        return "a summary of it"

    try:
        return asyncio.run(inbound.read(attachments or [_Att("clip.mp4", "video/mp4", b"bytes")], transcribe=transcribe or words,
                                        describe=describe or sees, digest=digest or short, now=NOW))
    finally:
        inbound._ffmpeg, shutil.which = held


def test_video_reads_the_sound_and_three_frames():
    fake, calls = _fake_ffmpeg()
    parts, prefer = _run_video(fake)
    text = parts[0]
    _check("the words are there", "Sound: the kettle is back in stock" in text, text)
    _check("each frame is stamped", all(f"Frame at {t}: view {i}" in text for i, t in ((1, "0:07"), (2, "0:21"), (3, "0:35"))), text)
    _check("it says how little was read", "3 still frames and the sound were read" in text and "0:42 long" in text, text)
    _check("inside the untrusted markers", "[UNTRUSTED EXTERNAL CONTENT" in text and text.index("[UNTRUSTED") < text.index("Sound:"), text)
    _check("saved like a document", "Saved as " in text and list((config.MEMORY_DIR / "inbox").glob("*-clip.txt")), text)
    _check("a short clip needs no tool agent", prefer is False)
    _check("frames, then the sound", [c[0] for c in calls] == ["ffprobe", "ffmpeg", "ffmpeg", "ffmpeg", "ffmpeg"], str(calls))
    _check("the sound is capped", "900" in calls[-1][1], str(calls[-1]))


def test_video_each_half_fails_alone():
    fake, _ = _fake_ffmpeg(sound=False)
    parts, _ = _run_video(fake)
    _check("no sound track is said plainly", "Sound: none." in parts[0] and "Frame at" in parts[0], parts[0])

    async def bad_words(data, name):
        raise RuntimeError("whisper is down")

    fake, _ = _fake_ffmpeg()
    parts, _ = _run_video(fake, transcribe=bad_words)
    _check("Whisper down leaves the frames", "could not be transcribed (RuntimeError)" in parts[0] and "Frame at" in parts[0], parts[0])

    async def bad_eyes(images):
        raise RuntimeError("vision is down")

    fake, _ = _fake_ffmpeg()
    parts, _ = _run_video(fake, describe=bad_eyes)
    _check("vision down leaves the sound", "could not be read (RuntimeError)" in parts[0] and "kettle" in parts[0], parts[0])

    fake, _ = _fake_ffmpeg(frames=False)
    parts, _ = _run_video(fake)
    _check("no frames is said plainly", "Frames: none" in parts[0] and "only 0 still frames" in parts[0], parts[0])


def test_video_that_cannot_be_read():
    from agents import inbound
    import shutil
    fake, _ = _fake_ffmpeg(probe_code=1)
    parts, _ = _run_video(fake)
    _check("not a video", "could not be read as a video" in parts[0] and parts[0].startswith("[A video"), parts[0])
    fake, calls = _fake_ffmpeg(probe_code=None)
    parts, _ = _run_video(fake)
    _check("a decoder that hangs ends there", "could not be read as a video" in parts[0] and len(calls) == 1, parts[0])

    fake, calls = _fake_ffmpeg()
    parts, _ = _run_video(fake, attachments=[_Att("big.mp4", "video/mp4", size=60 * 1024 * 1024)])
    _check("too big is refused before anything runs", "over the 50 MB" in parts[0] and not calls, parts[0])

    fake, calls = _fake_ffmpeg()
    parts, _ = _run_video(fake, attachments=[_Att("a.mp4", "video/mp4", b"x"), _Att("b.mp4", "video/mp4", b"y")])
    _check("one video a message", len(parts) == 2 and "b.mp4, was not read - one per message" in parts[1], str(parts))

    held = shutil.which
    shutil.which = lambda name, *a, **k: None
    try:
        parts, _ = asyncio.run(inbound.read([_Att("clip.mp4", "video/mp4", b"bytes")], now=NOW))
    finally:
        shutil.which = held
    _check("without ffmpeg it says so", "ffmpeg is not installed" in parts[0] and "No text could be taken" in parts[0], parts[0])

    async def boom(folder, program, *args):
        raise inbound.sandbox_error("NOT RUN")

    inbound.sandbox_error = RuntimeError
    parts, _ = _run_video(boom)
    del inbound.sandbox_error
    _check("a sandbox that will not start is reported, not raised", "could not be read (RuntimeError)" in parts[0], parts[0])


def test_long_video_goes_through_the_summary():
    fake, _ = _fake_ffmpeg()

    async def talks(data, name):
        return "word " * 2000

    parts, prefer = _run_video(fake, transcribe=talks)
    _check("a long talk is summarised", "a summary of it" in parts[0] and "word word" not in parts[0], parts[0][:300])
    _check("and gets the tool agent", prefer is True)


def test_video_with_the_real_ffmpeg():
    """Only where ffmpeg is installed: the deploy gate, once it is there. A generated clip, the real sandbox."""
    from agents import inbound
    import shutil
    import subprocess
    if not (shutil.which("ffmpeg") and shutil.which("bwrap")):
        print("  skip  ffmpeg or bubblewrap is not here")
        return
    held = tempfile.mkdtemp()
    clip = Path(held) / "made.mp4"
    subprocess.run(["ffmpeg", "-v", "error", "-f", "lavfi", "-i", "testsrc=duration=4:size=320x240:rate=5", "-f", "lavfi", "-i",
                    "sine=frequency=440:duration=4", "-shortest", "-pix_fmt", "yuv420p", "-y", str(clip)], check=True, timeout=60)
    heard, seen = [], []

    async def words(data, name):
        heard.append((len(data), name))
        return "a tone"

    async def sees(images):
        seen.extend(images)
        return ["a test card" for _ in images]

    parts, _ = asyncio.run(inbound.read([_Att("made.mp4", "video/mp4", clip.read_bytes())], transcribe=words, describe=sees, now=NOW))
    _check("the sound came out", heard and heard[0][0] > 0, str(heard))
    _check("three frames came out as jpegs", len(seen) == 3 and all(s.startswith("data:image/jpeg;base64,") for s in seen), str(len(seen)))
    _check("the length was read", "0:04 long" in parts[0], parts[0])

    (Path(held) / "not.mp4").write_bytes(b"this is not a video at all")
    parts, _ = asyncio.run(inbound.read([_Att("not.mp4", "video/mp4", b"this is not a video at all")], transcribe=words, describe=sees, now=NOW))
    _check("rubbish is refused by the real decoder", "could not be read as a video" in parts[0], parts[0])
    shutil.rmtree(held, ignore_errors=True)


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            print(name)
            fn()
    if _failures:
        print(f"\n{len(_failures)} FAILED: {', '.join(_failures)}")
        sys.exit(1)
    print("\nall pass")
