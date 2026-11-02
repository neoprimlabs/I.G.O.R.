"""Real files sent to the user (Hermes' deliverable mode), written with the standard library.

Run: python tests/test_deliver.py   (repo root, stdlib only, no API calls)

Each format is read back with the same readers IGOR uses on what the user sends it, and pypdf where
it is installed. They were also opened in python-docx, openpyxl and PyMuPDF on 2026-11-01 and the PDF
looked at, which a test cannot do. Invented content throughout: this repo is public.
"""

import asyncio
import io
import json
import sys
import tempfile
import xml.etree.ElementTree as ET
import zipfile
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import config

config.MEMORY_DIR = Path(tempfile.mkdtemp())

import context_store

context_store._DB_PATH = config.MEMORY_DIR / "context.db"

import commands
import deliver
from agents import inbound

_failures: list[str] = []


def _check(name: str, condition: bool, detail: str = "") -> None:
    print(f"  {'pass' if condition else 'FAIL'}  {name}" + ("" if condition else f"  {detail}"))
    if not condition:
        _failures.append(name)


SAMPLE = """# Kettle Notes & More

A report on **three kettles**, with *italics*, a [shop](https://example.com/shop?a=1&b=2) and a link https://example.org/page.
Its own line.

## Prices

| Kettle | Price | Notes |
|---|---:|---|
| Steel "Pro" | 38 | A long note that has to wrap inside its own cell in a table on a page, which is why it goes on |
| Copper <old> & bent | 120.50 | =HYPERLINK("http://evil.example","click") |

1. First
2. Second
   - nested
3. Third

- A bullet

```
print("hello")
```

1. Another list
"""


def _all_parts_parse(data: bytes) -> list[str]:
    with zipfile.ZipFile(io.BytesIO(data)) as z:
        for name in z.namelist():
            ET.fromstring(z.read(name))
        return z.namelist()


def test_parse() -> None:
    blocks = deliver.parse(SAMPLE)
    kinds = [b[0] for b in blocks]
    _check("headings, a paragraph, a table, lists and code", kinds[:4] == ["h", "p", "h", "table"] and "code" in kinds, str(kinds))
    _check("a line break in a paragraph is kept", len(blocks[1][1]) == 2, str(blocks[1]))
    table = blocks[3][1]
    _check("the rule under a table's header is not a row", len(table) == 3 and table[0] == ["Kettle", "Price", "Notes"], str(table))
    runs = deliver._runs("a **b** *c* [d](https://e.example/f) https://g.example/h.")
    _check("inline styles and links", ("b", "b", None) in runs and ("c", "i", None) in runs
           and ("d", "", "https://e.example/f") in runs and ("https://g.example/h", "", "https://g.example/h") in runs, str(runs))
    _check("a sentence's full stop is not part of its link", runs[-1] == (".", "", None), str(runs[-1]))
    _check("snake_case is left alone", deliver._plain("my_file_name") == "my_file_name")
    _check("a rule is not a list", deliver.parse("---\n\n* * *") == [])
    _check("nothing gives nothing", deliver.parse("") == [] and deliver.parse(None) == [])


def test_word() -> None:
    data, note = deliver.render(SAMPLE, "x.docx")
    names = _all_parts_parse(data)
    _check("every part is well-formed XML", "word/document.xml" in names and "word/numbering.xml" in names, str(names))
    text, _ = inbound._docx_text(data)
    _check("IGOR reads back the words", all(w in text for w in ("Kettle Notes & More", "three kettles", "nested", "print(")), text[:200])
    with zipfile.ZipFile(io.BytesIO(data)) as z:
        rels = z.read("word/_rels/document.xml.rels").decode()
        document = z.read("word/document.xml").decode()
        numbering = z.read("word/numbering.xml").decode()
    _check("a link keeps a full address, with its & escaped", "https://example.com/shop?a=1&amp;b=2" in rels, rels[:300])
    _check("each link is related", document.count("<w:hyperlink") == rels.count('TargetMode="External"'))
    _check("each list counted from 1 restarts", numbering.count("<w:startOverride") == 2, numbering[-300:])
    _check("nothing unescaped gets through", "<old>" not in document and "&lt;old&gt;" in document)
    data, _ = deliver.render("", "x.docx")
    _check("an empty document is still a document", "word/document.xml" in _all_parts_parse(data))
    data, _ = deliver.render("a\x01b\x0bc", "x.docx")
    _check("a control character cannot break the file", "word/document.xml" in _all_parts_parse(data))


def test_pdf() -> None:
    data, note = deliver.render(SAMPLE, "my-notes.pdf")
    _check("it is a PDF", data.startswith(b"%PDF-1.4") and data.rstrip().endswith(b"%%EOF"))
    _check("nothing was lost", note == "", note)
    start = int(data[data.rindex(b"startxref") + 10:data.rindex(b"%%EOF")].strip())
    _check("startxref points at the table", data[start:start + 4] == b"xref", str(data[start:start + 10]))
    entries = data[start:].split(b"\n")[2:]
    count = int(data[start:].split(b"\n")[1].split()[1])
    offsets = [int(e.split()[0]) for e in entries[1:count]]
    _check("every object is where the table says", all(data[o:o + 20].split(b" ")[0].isdigit() and b" 0 obj" in data[o:o + 20]
                                                       for o in offsets), str(offsets[:5]))
    try:
        import pypdf
    except ImportError:
        print("  skip  no pypdf here")
        pypdf = None
    if pypdf:
        reader = pypdf.PdfReader(io.BytesIO(data))
        text = reader.pages[0].extract_text()
        _check("a real reader finds the words", all(w in text for w in ("Kettle Notes", "three kettles", "Steel", "nested")), text[:300])
        _check("and the title", reader.metadata.title == "my notes", str(reader.metadata))
        long_text = "\n\n".join(f"Paragraph {n}. " + "Words that fill a page. " * 30 for n in range(40))
        data, _ = deliver.render(long_text, "long.pdf")
        pages = pypdf.PdfReader(io.BytesIO(data)).pages
        _check("a long one runs over pages, and none is lost",
               len(pages) > 2 and "Paragraph 39." in pages[-1].extract_text(), str(len(pages)))
        data, _ = deliver.render("", "empty.pdf")
        _check("an empty one is still a page", len(pypdf.PdfReader(io.BytesIO(data)).pages) == 1)
        data, _ = deliver.render("x" * 3000, "wide.pdf")
        text = "".join(pypdf.PdfReader(io.BytesIO(data)).pages[0].extract_text().split())
        _check("a word wider than the page is split, not cut off", text.count("x") == 3000, str(text.count("x")))
        data, _ = deliver.render("(brackets) and \\ back", "e.pdf")
        _check("what a PDF string treats as special is escaped",
               "(brackets) and \\ back" in pypdf.PdfReader(io.BytesIO(data)).pages[0].extract_text())
    _, note = deliver.render("Beijing 北京", "x.pdf")
    _check("it says when a font cannot show something", note.startswith("2 characters"), note)
    wide = deliver._wrap([("word " * 200, "", None)], 11, 468)
    _check("no line runs past the room", all(sum(deliver._width(w.rstrip(), s, 11) for w, s in line) <= 468 for line in wide), "")


def test_excel_and_csv() -> None:
    data, _ = deliver.render(SAMPLE, "x.xlsx")
    _all_parts_parse(data)
    text, _ = inbound._xlsx_text(data)
    _check("the sheet is named from the heading", "Prices" in text, text[:200])
    _check("IGOR reads back the rows", "Kettle\tPrice\tNotes" in text and "120.5" in text, text[:300])
    with zipfile.ZipFile(io.BytesIO(data)) as z:
        sheet = z.read("xl/worksheets/sheet1.xml").decode()
    _check("a number is a number", "<v>38</v>" in sheet, sheet[:300])
    _check("a formula from a page stays text", 't="inlineStr"' in sheet and "<f>" not in sheet and "=HYPERLINK" in sheet)
    _check("a leading zero stays text", deliver._NUMBER.match("007") is None and deliver._NUMBER.match("0.5") is not None)

    data, _ = deliver.render("| a | b |\n|---|---|\n| 1 | 2 |\n\n## Two\n\n| c |\n|---|\n| 3 |\n", "x.xlsx")
    with zipfile.ZipFile(io.BytesIO(data)) as z:
        _check("each table gets a sheet", "xl/worksheets/sheet2.xml" in z.namelist() and 'name="Two"' in z.read("xl/workbook.xml").decode())
    data, _ = deliver.render('## A "quoted" [title]: here\n\n| a |\n|---|\n| 1 |\n', "x.xlsx")
    _all_parts_parse(data)
    rows = deliver._rows_of("name,price\nkettle,38\ncup,4\n")
    _check("text that is already columns stays in columns", rows == [("", [["name", "price"], ["kettle", "38"], ["cup", "4"]])], str(rows))
    rows = deliver._rows_of("Just some words, in prose.\nAnd more of them")
    _check("prose is a row a line", rows == [("", [["Just some words, in prose."], ["And more of them"]])], str(rows))

    data, _ = deliver.render(SAMPLE, "x.csv")
    text = data.decode("utf-8-sig")
    _check("a formula from a page is marked as text in a CSV", "'=HYPERLINK" in text, text)
    _check("a negative number is left a number", deliver._safe_cell("-5") == "-5" and deliver._safe_cell("-x") == "'-x")
    _check("a BOM, so Excel reads it as UTF-8", data.startswith(b"\xef\xbb\xbf"))


def test_names_and_cleaning() -> None:
    _check("a name keeps its format", deliver._name("Cover Letter.DOCX") == "cover-letter.docx", deliver._name("Cover Letter.DOCX"))
    _check("a path cannot come through a name", deliver._name("../../etc/passwd.pdf") == "passwd.pdf", deliver._name("../../etc/passwd.pdf"))
    _check("nor by backslashes", deliver._name("..\\..\\x.pdf") == "x.pdf", deliver._name("..\\..\\x.pdf"))
    _check("no name takes the heading", deliver._name("", "# My Big Plan for Monday Morning Today\n\nx", ".pdf") == "my-big-plan-for-monday-morning.pdf")
    _check("nothing at all still has a name", deliver._name("", "", ".pdf") == "igor-document.pdf")
    data, _ = deliver.render("key gsk_abcdefghijklmnopqrstuvwxyz1234 and dash — here", "x.txt")
    text = data.decode("utf-8-sig")
    _check("a key goes no further than the draft", "gsk_" not in text and "[REDACTED]" in text, text)
    _check("and it reads like IGOR's other writing", "—" not in text and " - here" in text, text)


def _run_tool(inputs, sender=None):
    sent = []

    async def default(data, name):
        sent.append((name, data))
        return True

    deliver.set_sender(sender or default)
    return asyncio.run(deliver.run_tool(inputs)), sent


def test_tool() -> None:
    reply, sent = _run_tool({"name": "plan.pdf", "content": "# Plan\n\nWords."})
    _check("content is sent, named", reply.startswith("Sent plan.pdf (") and sent[0][0] == "plan.pdf", reply)
    reply, sent = _run_tool({"name": "x.pdf"})
    _check("no source is refused", reply.startswith("[NOT SENT") and not sent, reply)
    reply, sent = _run_tool({"name": "x.pdf", "content": "a", "last_reply": True})
    _check("two sources are refused", reply.startswith("[NOT SENT") and not sent, reply)
    reply, sent = _run_tool({"name": "x.exe", "content": "a"})
    _check("a format it does not offer is refused", reply.startswith("[NOT SENT") and not sent, reply)
    reply, sent = _run_tool({"name": "x.html", "content": "<script>a</script>"})
    _check("nor HTML, where a page's words could become a page", reply.startswith("[NOT SENT") and not sent, reply)
    reply, sent = _run_tool({"name": "noext", "content": "a"})
    _check("no extension is a PDF", sent and sent[0][0] == "noext.pdf", reply)

    async def refused(data, name):
        return False

    reply, _ = _run_tool({"name": "x.pdf", "content": "a"}, refused)
    _check("a refused send is not reported as sent", reply.startswith("[NOT SENT") and "Tell the user" in reply, reply)

    async def broke(data, name):
        raise RuntimeError("no")

    reply, _ = _run_tool({"name": "x.pdf", "content": "a"}, broke)
    _check("nor one that raises", reply.startswith("[NOT SENT"), reply)
    deliver._sender = None
    reply = asyncio.run(deliver.run_tool({"name": "x.pdf", "content": "a"}))
    _check("nor with no sender at all", reply.startswith("[NOT SENT"), reply)
    deliver._MAX_BYTES, keep = 10, deliver._MAX_BYTES
    reply, sent = _run_tool({"name": "x.txt", "content": "a" * 100})
    deliver._MAX_BYTES = keep
    _check("too big for Discord says so", reply.startswith("[NOT SENT") and "8 MB" in reply and not sent, reply)


def test_path() -> None:
    config.BASE_DIR, keep = config.MEMORY_DIR, config.BASE_DIR
    try:
        (config.BASE_DIR / "paper.md").write_text("# Paper\n\nFound.", encoding="utf-8")
        (config.BASE_DIR / "pic.png").write_bytes(b"\x89PNG not really")
        reply, sent = _run_tool({"name": "paper.docx", "path": "paper.md"})
        _check("a Markdown file becomes Word", sent and "Found." in inbound._docx_text(sent[0][1])[0], reply)
        reply, sent = _run_tool({"name": "", "path": "paper.md"})
        _check("with no name it keeps its own", sent and sent[0][0] == "paper.md", reply)
        reply, sent = _run_tool({"name": "pic.png", "path": "pic.png"})
        _check("a picture goes as it is", sent and sent[0][1] == b"\x89PNG not really", reply)
        reply, sent = _run_tool({"name": "pic.pdf", "path": "pic.png"})
        _check("and is not fed to a converter", reply.startswith("[NOT SENT") and not sent, reply)
        reply, sent = _run_tool({"name": "x.pdf", "path": "nothing.md"})
        _check("a missing file says so", reply.startswith("[NOT SENT") and "no file" in reply and not sent, reply)
    finally:
        config.BASE_DIR = keep


def test_the_rules_apply_to_a_path() -> None:
    from agents import react
    sent = []

    async def sender(data, name):
        sent.append(name)
        return True

    deliver.set_sender(sender)

    async def run(args, allow_private=True):
        return await react._execute_tool("send_file", args, allow_private=allow_private)

    # Real files in a throwaway tree, so "not there" cannot pass for "refused".
    root = Path(tempfile.mkdtemp())
    keep = config.BASE_DIR, config.MEMORY_DIR
    config.BASE_DIR, config.MEMORY_DIR = root, root / "memory"
    try:
        sealed = (".env", "memory/corrections.md", ".git/config", "memory/private/cron_scripts/x.py",
                  "scripts/backup_memory.local.ps1", "memory/private/mcp_servers.json")
        for path in sealed + ("memory/resume.md", "memory/ok.md"):
            (root / path).parent.mkdir(parents=True, exist_ok=True)
            (root / path).write_text("the secret", encoding="utf-8")
        for path in sealed + ("../outside.md", "/etc/passwd"):
            reply = asyncio.run(run({"name": "x.txt", "path": path}))
            _check(f"{path} is refused", reply.startswith(("[sealed", "[access denied")) and not sent, reply[:90])
        reply = asyncio.run(run({"name": "x.txt", "path": "memory/resume.md"}, allow_private=False))
        _check("a private file is refused after a web read", reply.startswith("[private file") and not sent, reply[:90])
        reply = asyncio.run(run({"name": "resume.docx", "path": "memory/resume.md"}))
        _check("and goes to the user's own chat otherwise", sent == ["resume.docx"], reply[:90])
        sent.clear()
        try:
            (root / "memory" / "link.md").symlink_to(root / ".env")
        except OSError:
            print("  skip  no symlinks here")
        else:
            reply = asyncio.run(run({"name": "x.txt", "path": "memory/link.md"}))
            _check("a link to a sealed file is the sealed file", reply.startswith("[sealed") and not sent, reply[:90])
    finally:
        config.BASE_DIR, config.MEMORY_DIR = keep
    _check("a worker cannot send one", "send_file" in react._WORKER_FORBIDDEN)
    _check("it is not closed by a web read, as a reply is not", "send_file" not in react._QUARANTINED_AFTER_WEB)
    _check("its result counts as a save, so a failure shows under the reply", "send_file" in react._SAVING_TOOLS)


def test_last_reply() -> None:
    context_store.clear()
    _check("nothing yet", deliver.last_reply() == "")
    context_store.append("user", "hello")
    context_store.append("assistant", "The answer.\n\n-# Saved a note in tasks.md")
    _check("the receipts are not part of it", deliver.last_reply() == "The answer.", deliver.last_reply())
    context_store.append("assistant", "[sent proactively]\nThe digest.")
    _check("a post has no mark", deliver.last_reply() == "The digest.", deliver.last_reply())
    context_store.append("assistant", "Sent the-digest.pdf (2 KB).")
    _check("news of a file is not the document", deliver.last_reply() == "The digest.", deliver.last_reply())
    context_store.append("assistant", "Here is your report as a PDF.\n\n-# Sent report.pdf (5 KB).")
    _check("nor a short reply about one", deliver.last_reply() == "The digest.", deliver.last_reply())
    context_store.append("user", "a question")
    _check("the user's words are never it", deliver.last_reply() == "The digest.", deliver.last_reply())
    reply, sent = _run_tool({"name": "d.txt", "last_reply": True})
    _check("the tool can send it", sent and b"The digest." in sent[0][1], reply)
    reply = asyncio.run(deliver.export_last(".pdf"))
    _check("the command names it from the text", reply.startswith("Sent the-digest.pdf ("), reply)
    context_store.clear()
    reply = asyncio.run(deliver.export_last(".pdf"))
    _check("with nothing to send, it says so", "no earlier message" in reply, reply)


def test_words() -> None:
    for text, expected in (("send that as a pdf", ".pdf"), ("Send me that as a PDF please.", ".pdf"), ("make that a word doc", ".docx"),
                           ("turn this into a spreadsheet", ".xlsx"), ("put that in a csv", ".csv"), ("can I get that as a PDF?", ".pdf"),
                           ("could you send it to me as a word document", ".docx"), ("give me the last reply as a text file", ".txt"),
                           ("export it to excel", ".xlsx"), ("igor, save that as a pdf", ".pdf")):
        _check(f"{text!r} is {expected}", commands.export_format(text) == expected, str(commands.export_format(text)))
    for text in ("send that to Sam", "what is a pdf", "make that shorter", "send me a pdf about kettles", "save that as a skill",
                 "make it 9am", "", None):
        _check(f"{text!r} is not the last reply in a file", commands.export_format(text) is None, str(commands.export_format(text)))
    for text in ("make me a spreadsheet of the jobs you found", "write a cover letter as a Word document", "put these prices in a CSV",
                 "create a PDF of my notes"):
        _check(f"{text!r} wants a file", commands.wants_file(text))
    for text in ("what is a pdf", "I read a pdf yesterday", "how do spreadsheets work"):
        _check(f"{text!r} does not", not commands.wants_file(text))


def test_the_turn_starts_with_it_open() -> None:
    from agents import react
    calls = []

    async def create(model, **request):
        calls.append({t["function"]["name"] for t in request.get("tools") or []})
        return SimpleNamespace(choices=[SimpleNamespace(finish_reason="stop", message=SimpleNamespace(content="Done.", tool_calls=None))],
                               usage=None)

    fake = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=create)))
    real = react._get_client
    react._get_client = lambda: fake
    try:
        asyncio.run(react.handle("make me a spreadsheet of these prices", []))
        asyncio.run(react.handle("how are the prices", []))
    finally:
        react._get_client = real
    _check("a request for a file needs no bridge step", "send_file" in calls[0], str(calls[0]))
    _check("any other turn does not carry it", "send_file" not in calls[1], str(calls[1]))


if __name__ == "__main__":
    print("\nparsing")
    test_parse()
    print("\nWord")
    test_word()
    print("\nPDF")
    test_pdf()
    print("\nExcel and CSV")
    test_excel_and_csv()
    print("\nnames and cleaning")
    test_names_and_cleaning()
    print("\nthe tool")
    test_tool()
    test_path()
    test_the_rules_apply_to_a_path()
    print("\nthe last reply")
    test_last_reply()
    print("\nthe user's words")
    test_words()
    test_the_turn_starts_with_it_open()
    print(f"\n{len(_failures)} failed" if _failures else "\nAll passed.")
    sys.exit(1 if _failures else 0)
