"""Real files sent to the user: Hermes' deliverable mode, on what this host has.

Hermes' agent makes a file with its tools (matplotlib, its Office and PDF skills) and names the path
in its reply; the gateway picks the path out and uploads the file. This host has none of those
libraries and 956 MB to run them in, and a model writing a whole document as tool arguments spends
a call's budget on it. So code renders: the model gives plain Markdown, or names a file, or asks for
its own last reply, which costs no output tokens at all. Word, PDF and Excel are written with the
standard library: they are small zip and text formats, and these cover what a person sends on.

Only the formats below, by the name's extension. Not HTML, where a page's words could become a
page the user opens. Text that came from a page can reach a cell, so a CSV cell that a spreadsheet
would run as a formula is marked as text. The user is the only recipient, so this is no more
outward than a reply, and it stays open after a web read as a reply does.
"""

import csv
import io
import logging
import re
import zipfile
import zlib
from pathlib import Path
from typing import Awaitable, Callable, Optional
from xml.sax.saxutils import escape

import config
import sanitize

logger = logging.getLogger(__name__)

FORMATS = (".docx", ".pdf", ".xlsx", ".csv", ".md", ".txt")
_SENT_AS_THEY_ARE = (".docx", ".pdf", ".xlsx", ".png", ".jpg", ".jpeg", ".zip")
_MAX_BYTES = 8 * 1024 * 1024  # Discord's limit for a bot in a DM is 10 MiB.
_MAX_SOURCE_CHARS = 400_000
_CONTROL = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f￾￿]")

_sender: Optional[Callable[[bytes, str], Awaitable[bool]]] = None


def set_sender(fn: Callable[[bytes, str], Awaitable[bool]]) -> None:
    global _sender
    _sender = fn


# The Markdown a model writes, and no more: headings, lists, tables, code, bold, italic, links.
_HEADING = re.compile(r"^(#{1,6})\s+(.*?)\s*#*\s*$")
_BULLET = re.compile(r"^(\s*)[-*+]\s+(.*)$")
_NUMBERED = re.compile(r"^(\s*)(\d+)[.)]\s+(.*)$")
_TABLE_RULE = re.compile(r"^\s*\|?\s*:?-{2,}:?\s*(\|\s*:?-{2,}:?\s*)*\|?\s*$")
_RULE = re.compile(r"^\s*([-*_])\s*(\1\s*){2,}$")
_INLINE = re.compile(
    r"\*\*(?=\S)(?P<bold>.+?)(?<=\S)\*\*|__(?=\S)(?P<bold2>.+?)(?<=\S)__"
    r"|\*(?=[^\s*])(?P<italic>.+?)(?<=[^\s*])\*"
    r"|`(?P<code>[^`]+)`"
    r"|\[(?P<label>[^\]]+)\]\((?P<href>https?://[^\s)]+)\)"
    r"|<(?P<auto>https?://[^\s>]+)>"
    r"|(?P<bare>https?://[^\s<>)\]\"]+)")


def _split_row(line: str) -> list[str]:
    body = line.strip()
    body = body[1:] if body.startswith("|") else body
    body = body[:-1] if body.endswith("|") and not body.endswith("\\|") else body
    return [cell.strip().replace("\\|", "|") for cell in re.split(r"(?<!\\)\|", body)]


def parse(text: str) -> list[tuple]:
    """Blocks: ("h", level, text), ("p", [lines]), ("li", level, marker, text), ("table", rows),
    ("code", [lines])."""
    lines = (text or "").replace("\r\n", "\n").replace("\r", "\n").split("\n")
    blocks: list[tuple] = []
    plain: list[str] = []

    def flush() -> None:
        if plain:
            blocks.append(("p", list(plain)))
            plain.clear()

    i = 0
    while i < len(lines):
        line = lines[i]
        stripped = line.strip()
        if stripped.startswith("```"):
            flush()
            code, i = [], i + 1
            while i < len(lines) and not lines[i].strip().startswith("```"):
                code.append(lines[i])
                i += 1
            blocks.append(("code", code))
            i += 1
            continue
        if "|" in line and i + 1 < len(lines) and _TABLE_RULE.match(lines[i + 1]) and "-" in lines[i + 1]:
            flush()
            rows, i = [_split_row(line)], i + 2
            while i < len(lines) and "|" in lines[i] and lines[i].strip():
                rows.append(_split_row(lines[i]))
                i += 1
            blocks.append(("table", rows))
            continue
        i += 1
        if not stripped or _RULE.match(line):
            flush()
            continue
        heading, bullet, numbered = _HEADING.match(line), _BULLET.match(line), _NUMBERED.match(line)
        if heading:
            flush()
            blocks.append(("h", min(len(heading.group(1)), 3), heading.group(2)))
        elif bullet or numbered:
            flush()
            found = bullet or numbered
            level = min(len(found.group(1).replace("\t", "    ")) // 2, 2)
            blocks.append(("li", level, numbered.group(2) if numbered else "-", found.group(found.lastindex)))
        else:
            plain.append(stripped[1:].strip() if stripped.startswith(">") else stripped)
    flush()
    return blocks


def _runs(text: str) -> list[tuple[str, str, Optional[str]]]:
    """(text, "" or "b" or "i", link) for one line of Markdown."""
    runs, at = [], 0
    for m in _INLINE.finditer(text):
        if m.start() > at:
            runs.append((text[at:m.start()], "", None))
        groups = m.groupdict()
        if groups["bold"] or groups["bold2"]:
            runs.append((groups["bold"] or groups["bold2"], "b", None))
        elif groups["italic"]:
            runs.append((groups["italic"], "i", None))
        elif groups["code"]:
            runs.append((groups["code"], "", None))
        elif groups["href"]:
            runs.append((groups["label"], "", groups["href"]))
        elif groups["auto"]:
            runs.append((groups["auto"], "", groups["auto"]))
        else:
            # A sentence's full stop is not part of the link.
            url = groups["bare"].rstrip(".,;:!?'")
            runs.append((url, "", url))
            if len(url) < len(groups["bare"]):
                runs.append((groups["bare"][len(url):], "", None))
        at = m.end()
    if at < len(text):
        runs.append((text[at:], "", None))
    return runs


def _plain(text: str) -> str:
    return "".join(run[0] for run in _runs(text))


def _xml(text: str) -> str:
    return escape(_CONTROL.sub("", text))


def _zip(parts: dict[str, str]) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as z:
        for name, body in parts.items():
            z.writestr(name, body.encode("utf-8"))
    return buffer.getvalue()


_XML_HEAD = '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'
_W_NS = ('xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main" '
         'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"')
_REL = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
_HYPERLINK_REL = _REL + "/hyperlink"

_DOCX_STYLES = (
    _XML_HEAD + f"<w:styles {_W_NS}>"
    '<w:docDefaults><w:rPrDefault><w:rPr><w:rFonts w:ascii="Calibri" w:hAnsi="Calibri" w:cs="Calibri"/>'
    '<w:sz w:val="22"/></w:rPr></w:rPrDefault><w:pPrDefault><w:pPr><w:spacing w:after="120" w:line="264" '
    'w:lineRule="auto"/></w:pPr></w:pPrDefault></w:docDefaults>'
    '<w:style w:type="paragraph" w:default="1" w:styleId="Normal"><w:name w:val="Normal"/></w:style>'
    + "".join(f'<w:style w:type="paragraph" w:styleId="Heading{n}"><w:name w:val="heading {n}"/>'
              f'<w:basedOn w:val="Normal"/><w:next w:val="Normal"/><w:pPr><w:keepNext/>'
              f'<w:spacing w:before="{before}" w:after="80"/><w:outlineLvl w:val="{n - 1}"/></w:pPr>'
              f'<w:rPr><w:b/><w:sz w:val="{size}"/></w:rPr></w:style>'
              for n, size, before in ((1, 36, 240), (2, 28, 200), (3, 24, 160)))
    + '<w:style w:type="paragraph" w:styleId="Code"><w:name w:val="Code"/><w:basedOn w:val="Normal"/>'
      '<w:pPr><w:spacing w:after="0"/></w:pPr><w:rPr><w:rFonts w:ascii="Consolas" w:hAnsi="Consolas"/>'
      '<w:sz w:val="19"/></w:rPr></w:style>'
      '<w:style w:type="character" w:styleId="Hyperlink"><w:name w:val="Hyperlink"/><w:rPr>'
      '<w:color w:val="0563C1"/><w:u w:val="single"/></w:rPr></w:style>'
      '<w:style w:type="table" w:styleId="TableGrid"><w:name w:val="Table Grid"/><w:tblPr><w:tblBorders>'
    + "".join(f'<w:{side} w:val="single" w:sz="4" w:space="0" w:color="808080"/>'
              for side in ("top", "left", "bottom", "right", "insideH", "insideV"))
    + "</w:tblBorders></w:tblPr></w:style></w:styles>")


def _list_levels(kind: str) -> str:
    out = []
    for level in range(3):
        left = 360 * (level + 1)
        if kind == "bullet":
            body = '<w:numFmt w:val="bullet"/><w:lvlText w:val="&#8226;"/>'
        else:
            body = f'<w:numFmt w:val="decimal"/><w:lvlText w:val="%{level + 1}."/>'
        out.append(f'<w:lvl w:ilvl="{level}"><w:start w:val="1"/>{body}<w:lvlJc w:val="left"/>'
                   f'<w:pPr><w:ind w:left="{left}" w:hanging="360"/></w:pPr></w:lvl>')
    return "".join(out)


def to_docx(text: str) -> bytes:
    links: list[str] = []
    numbered_lists = 0

    def run_xml(run: tuple) -> str:
        words, style, link = run
        props = {"b": "<w:b/>", "i": "<w:i/>"}.get(style, "")
        if link:
            props = '<w:rStyle w:val="Hyperlink"/>' + props
        props = f"<w:rPr>{props}</w:rPr>" if props else ""
        body = f'<w:r>{props}<w:t xml:space="preserve">{_xml(words)}</w:t></w:r>'
        if not link:
            return body
        links.append(link)
        return f'<w:hyperlink r:id="rId{len(links) + 100}">{body}</w:hyperlink>'

    def paragraph(line_list: list[str], props: str = "") -> str:
        inner = "<w:r><w:br/></w:r>".join("".join(run_xml(r) for r in _runs(line)) for line in line_list)
        return f"<w:p>{f'<w:pPr>{props}</w:pPr>' if props else ''}{inner}</w:p>"

    body = []
    for block in parse(text):
        kind = block[0]
        if kind == "h":
            body.append(paragraph([block[2]], f'<w:pStyle w:val="Heading{block[1]}"/>'))
        elif kind == "p":
            body.append(paragraph(block[1]))
        elif kind == "code":
            for line in block[1] or [""]:
                body.append(f'<w:p><w:pPr><w:pStyle w:val="Code"/></w:pPr><w:r><w:t xml:space="preserve">{_xml(line)}'
                            "</w:t></w:r></w:p>")
        elif kind == "li":
            ordered = block[2] != "-"
            # Each list the model starts from 1 gets its own numbering, or Word carries on counting.
            if ordered and block[2] == "1" and block[1] == 0:
                numbered_lists += 1
            num_id = 1 if not ordered else 1 + max(numbered_lists, 1)
            body.append(paragraph([block[3]], f'<w:numPr><w:ilvl w:val="{block[1]}"/><w:numId w:val="{num_id}"/></w:numPr>'))
        elif kind == "table":
            rows = block[1]
            width = max(len(row) for row in rows)
            cells = [(row + [""] * width)[:width] for row in rows]
            grid = "".join(f'<w:gridCol w:w="{9360 // width}"/>' for _ in range(width))
            xml_rows = []
            for r, row in enumerate(cells):
                tc = []
                for cell in row:
                    runs = _runs(cell)
                    if r == 0:
                        runs = [(words, "b", link) for words, _, link in runs]
                    inner = "".join(run_xml(run) for run in runs)
                    tc.append(f'<w:tc><w:tcPr><w:tcW w:w="{9360 // width}" w:type="dxa"/></w:tcPr>'
                              f'<w:p><w:pPr><w:spacing w:after="0"/></w:pPr>{inner}</w:p></w:tc>')
                head = "<w:trPr><w:tblHeader/></w:trPr>" if r == 0 else ""
                xml_rows.append(f"<w:tr>{head}{''.join(tc)}</w:tr>")
            body.append('<w:tbl><w:tblPr><w:tblStyle w:val="TableGrid"/><w:tblW w:w="0" w:type="auto"/></w:tblPr>'
                        f"<w:tblGrid>{grid}</w:tblGrid>{''.join(xml_rows)}</w:tbl>"
                        '<w:p><w:pPr><w:spacing w:after="60"/></w:pPr></w:p>')

    document = (_XML_HEAD + f"<w:document {_W_NS}><w:body>{''.join(body) or '<w:p/>'}"
                '<w:sectPr><w:pgSz w:w="12240" w:h="15840"/><w:pgMar w:top="1440" w:right="1440" w:bottom="1440" '
                'w:left="1440" w:header="720" w:footer="720" w:gutter="0"/></w:sectPr></w:body></w:document>')
    nums = ['<w:num w:numId="1"><w:abstractNumId w:val="0"/></w:num>']
    for n in range(max(numbered_lists, 1)):
        nums.append(f'<w:num w:numId="{n + 2}"><w:abstractNumId w:val="1"/><w:lvlOverride w:ilvl="0">'
                    '<w:startOverride w:val="1"/></w:lvlOverride></w:num>')
    numbering = (_XML_HEAD + f"<w:numbering {_W_NS}>"
                 f'<w:abstractNum w:abstractNumId="0">{_list_levels("bullet")}</w:abstractNum>'
                 f'<w:abstractNum w:abstractNumId="1">{_list_levels("decimal")}</w:abstractNum>'
                 + "".join(nums) + "</w:numbering>")
    rels = "".join(f'<Relationship Id="rId{i + 101}" Type="{_HYPERLINK_REL}" Target="{escape(url, {chr(34): "&quot;"})}" '
                   'TargetMode="External"/>' for i, url in enumerate(links))
    return _zip({
        "[Content_Types].xml": (
            _XML_HEAD + '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
            '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
            '<Default Extension="xml" ContentType="application/xml"/>'
            '<Override PartName="/word/document.xml" ContentType="application/vnd.openxmlformats-officedocument.'
            'wordprocessingml.document.main+xml"/>'
            '<Override PartName="/word/styles.xml" ContentType="application/vnd.openxmlformats-officedocument.'
            'wordprocessingml.styles+xml"/>'
            '<Override PartName="/word/numbering.xml" ContentType="application/vnd.openxmlformats-officedocument.'
            'wordprocessingml.numbering+xml"/></Types>'),
        "_rels/.rels": (
            _XML_HEAD + '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
            f'<Relationship Id="rId1" Type="{_REL}/officeDocument" Target="word/document.xml"/></Relationships>'),
        "word/_rels/document.xml.rels": (
            _XML_HEAD + '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
            f'<Relationship Id="rId1" Type="{_REL}/styles" Target="styles.xml"/>'
            f'<Relationship Id="rId2" Type="{_REL}/numbering" Target="numbering.xml"/>{rels}</Relationships>'),
        "word/document.xml": document,
        "word/styles.xml": _DOCX_STYLES,
        "word/numbering.xml": numbering,
    })


# Character widths from Adobe's metrics for the PDF base fonts, from space to tilde, in 1000ths of the
# type size. A PDF has no layout engine, so wrapping a line needs these.
_HELVETICA = (278, 278, 355, 556, 556, 889, 667, 191, 333, 333, 389, 584, 278, 333, 278, 278, *[556] * 10,
              278, 278, 584, 584, 584, 556, 1015, 667, 667, 722, 722, 667, 611, 778, 722, 278, 500, 667, 556,
              833, 722, 778, 667, 778, 722, 667, 611, 722, 667, 944, 667, 667, 611, 278, 278, 278, 469, 556,
              333, 556, 556, 500, 556, 556, 278, 556, 556, 222, 222, 500, 222, 833, 556, 556, 556, 556, 333,
              500, 278, 556, 500, 722, 500, 500, 500, 334, 260, 334, 584)
_HELVETICA_BOLD = (278, 333, 474, 556, 556, 889, 722, 238, 333, 333, 389, 584, 278, 333, 278, 278, *[556] * 10,
                   333, 333, 584, 584, 584, 611, 975, 722, 722, 722, 722, 667, 611, 778, 722, 278, 556, 722,
                   611, 833, 722, 778, 667, 778, 722, 667, 611, 722, 667, 944, 667, 667, 611, 333, 278, 333,
                   584, 556, 333, 556, 611, 556, 611, 556, 333, 611, 611, 278, 278, 556, 278, 889, 611, 611,
                   611, 611, 389, 556, 333, 611, 556, 778, 556, 556, 500, 389, 280, 389, 584)
_PAGE_W, _PAGE_H, _MARGIN = 612, 792, 72
_FONTS = {"": ("F1", _HELVETICA), "b": ("F2", _HELVETICA_BOLD), "i": ("F3", _HELVETICA), "c": ("F4", None)}


def _width(words: str, style: str, size: float) -> float:
    table = _FONTS[style][1]
    if table is None:
        return len(words) * 0.6 * size
    return sum(table[ord(c) - 32] if 32 <= ord(c) <= 126 else 556 for c in words) * size / 1000


def _pdf_escape(words: str) -> bytes:
    data = words.encode("cp1252", "replace")
    return data.replace(b"\\", b"\\\\").replace(b"(", b"\\(").replace(b")", b"\\)")


def _wrap(runs: list[tuple], size: float, room: float) -> list[list[tuple[str, str]]]:
    """Runs into lines of (words, style) that fit the room, splitting a word only if it must."""
    lines: list[list[tuple[str, str]]] = [[]]
    used = 0.0
    for words, style, link in runs:
        style = style or ""
        if link and link != words:
            words = f"{words} ({link})"
        for piece in re.findall(r"\S+\s*|\s+", words):
            width = _width(piece.rstrip(), style, size)
            if used and used + width > room:
                lines.append([])
                used = 0.0
            while width > room:
                cut = len(piece)
                while cut > 1 and _width(piece[:cut], style, size) > room:
                    cut -= 1
                lines[-1].append((piece[:cut], style))
                lines.append([])
                used = 0.0
                piece = piece[cut:]
                width = _width(piece.rstrip(), style, size)
            lines[-1].append((piece, style))
            used += _width(piece, style, size)
    return [line for line in lines if line] or [[]]


class _Pages:
    """Lays text down from the top of the page, starting a new one when it runs out."""

    def __init__(self) -> None:
        self.pages: list[list[bytes]] = [[]]
        self.y = _PAGE_H - _MARGIN

    def need(self, height: float) -> None:
        if self.y - height < _MARGIN:
            self.pages.append([])
            self.y = _PAGE_H - _MARGIN

    def line(self, parts: list[tuple[str, str]], x: float, size: float, leading: float) -> None:
        self.need(leading)
        self.y -= leading
        self.write(parts, x, self.y + (leading - size) * 0.4, size)

    def write(self, parts: list[tuple[str, str]], x: float, y: float, size: float) -> None:
        out = [b"BT", f"{x:.2f} {y:.2f} Td".encode()]
        for words, style in parts:
            out.append(f"/{_FONTS[style][0]} {size:g} Tf".encode())
            out.append(b"(" + _pdf_escape(words) + b") Tj")
        out.append(b"ET")
        self.pages[-1].append(b" ".join(out))

    def rule(self, x1: float, y1: float, x2: float, y2: float) -> None:
        self.pages[-1].append(f"{x1:.2f} {y1:.2f} m {x2:.2f} {y2:.2f} l S".encode())


def _pdf_table(pages: _Pages, rows: list[list[str]], left: float, room: float) -> None:
    size, leading, pad = 9.5, 12.5, 4.0
    width = max(len(row) for row in rows)
    rows = [(row + [""] * width)[:width] for row in rows]
    wanted = [max(max((_width(_plain(row[c]), "", size) for row in rows), default=0), 30) + pad * 2
              for c in range(width)]
    total = sum(wanted)
    even = room / width
    if total <= room:
        columns = [w * room / total for w in wanted]
    else:
        # Narrow columns keep what they need, and the rest share what is left.
        columns = [min(w, even) for w in wanted]
        spare = room - sum(columns)
        hungry = [c for c in range(width) if wanted[c] > columns[c]]
        need = sum(wanted[c] - columns[c] for c in hungry) or 1
        for c in hungry:
            columns[c] += spare * (wanted[c] - columns[c]) / need
    for r, row in enumerate(rows):
        cells = []
        for c, cell in enumerate(row):
            runs = _runs(cell) or [("", "", None)]
            if r == 0:
                runs = [(words, "b", link) for words, _, link in runs]
            cells.append(_wrap(runs, size, columns[c] - pad * 2))
        height = max(len(lines) for lines in cells) * leading + pad * 2
        pages.need(height)
        top = pages.y
        x = left
        for c, lines in enumerate(cells):
            y = top - pad
            for parts in lines:
                y -= leading
                if parts:
                    pages.write(parts, x + pad, y + 3, size)
            pages.rule(x, top, x, top - height)
            x += columns[c]
        pages.rule(x, top, x, top - height)
        pages.rule(left, top, x, top)
        pages.rule(left, top - height, x, top - height)
        pages.y = top - height
    pages.y -= 8


def to_pdf(text: str, title: str = "") -> bytes:
    pages = _Pages()
    room = _PAGE_W - 2 * _MARGIN
    for block in parse(text):
        kind = block[0]
        if kind == "h":
            size = {1: 18, 2: 14, 3: 12}[block[1]]
            pages.y -= size * 0.5
            lines = _wrap([(words, "b", link) for words, _, link in _runs(block[2])], size, room)
            pages.need(size * 1.25 * len(lines) + 24)
            for parts in lines:
                pages.line(parts, _MARGIN, size, size * 1.25)
            pages.y -= 3
        elif kind == "p":
            for source in block[1]:
                for parts in _wrap(_runs(source), 11, room):
                    pages.line(parts, _MARGIN, 11, 14.5)
            pages.y -= 6
        elif kind == "li":
            indent = _MARGIN + 16 * block[1]
            marker = "\u2022" if block[2] == "-" else f"{block[2]}."
            lines = _wrap(_runs(block[3]), 11, room - (indent - _MARGIN) - 18)
            for n, parts in enumerate(lines):
                pages.line(parts, indent + 18, 11, 14.5)
                if n == 0:
                    pages.write([(marker, "")], indent + 4, pages.y + 1.4, 11)
            pages.y -= 2
        elif kind == "code":
            for source in block[1] or [""]:
                for parts in _wrap([(source or " ", "c", None)], 9, room):
                    pages.line(parts, _MARGIN, 9, 11.5)
            pages.y -= 6
        elif kind == "table":
            _pdf_table(pages, block[1], _MARGIN, room)

    objects: list[bytes] = []
    page_ids = []
    first_page = 7
    total = len(pages.pages)
    for n, parts in enumerate(pages.pages):
        footer = f"BT /F1 9 Tf {_PAGE_W / 2 - 18:.2f} 40 Td (Page {n + 1} of {total}) Tj ET".encode()
        stream = zlib.compress(b"0.5 w\n" + b"\n".join(parts + [footer]))
        page_id = first_page + 2 * n
        page_ids.append(page_id)
        objects.append(f"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 {_PAGE_W} {_PAGE_H}] /Contents {page_id + 1} 0 R "
                       "/Resources << /Font << /F1 3 0 R /F2 4 0 R /F3 5 0 R /F4 6 0 R >> >> >>".encode())
        objects.append(f"<< /Length {len(stream)} /Filter /FlateDecode >>\nstream\n".encode() + stream + b"\nendstream")
    fonts = ("Helvetica", "Helvetica-Bold", "Helvetica-Oblique", "Courier")
    head = [b"<< /Type /Catalog /Pages 2 0 R >>",
            f"<< /Type /Pages /Kids [{' '.join(f'{p} 0 R' for p in page_ids)}] /Count {total} >>".encode()]
    head += [f"<< /Type /Font /Subtype /Type1 /BaseFont /{f} /Encoding /WinAnsiEncoding >>".encode() for f in fonts]
    info_id = first_page + 2 * total
    objects = head + objects + [b"<< /Title (" + _pdf_escape(title or "Document") + b") /Producer (I.G.O.R.) >>"]

    out = bytearray(b"%PDF-1.4\n%\xe2\xe3\xcf\xd3\n")
    offsets = []
    for n, body in enumerate(objects, 1):
        offsets.append(len(out))
        out += f"{n} 0 obj\n".encode() + body + b"\nendobj\n"
    start = len(out)
    out += f"xref\n0 {len(objects) + 1}\n0000000000 65535 f \n".encode()
    out += b"".join(f"{at:010d} 00000 n \n".encode() for at in offsets)
    out += f"trailer\n<< /Size {len(objects) + 1} /Root 1 0 R /Info {info_id} 0 R >>\nstartxref\n{start}\n%%EOF\n".encode()
    return bytes(out)


def _rows_of(text: str) -> list[tuple[str, list[list[str]]]]:
    """(sheet name, rows): each Markdown table, or else the text's own columns, or else its lines."""
    sheets, heading = [], ""
    for block in parse(text):
        if block[0] == "h":
            heading = _plain(block[2])
        elif block[0] == "table":
            sheets.append((heading, [[_plain(cell) for cell in row] for row in block[1]]))
    if sheets:
        return sheets
    lines = [line for line in (text or "").replace("\r\n", "\n").split("\n") if line.strip()]
    for delimiter in ("\t", ",", ";"):
        rows = list(csv.reader(lines, delimiter=delimiter))
        if len(rows) > 1 and len(rows[0]) > 1 and all(len(row) == len(rows[0]) for row in rows):
            return [("", rows)]
    rows = []
    for block in parse(text):
        if block[0] == "h":
            rows.append([_plain(block[2])])
        elif block[0] == "p":
            rows += [[_plain(line)] for line in block[1]]
        elif block[0] == "li":
            rows.append([_plain(block[3])])
        elif block[0] == "code":
            rows += [[line] for line in block[1]]
    return [("", rows)]


_NUMBER = re.compile(r"^-?(0|[1-9]\d{0,14})(\.\d+)?$")


def _column(n: int) -> str:
    name = ""
    n += 1
    while n:
        n, rest = divmod(n - 1, 26)
        name = chr(65 + rest) + name
    return name


def to_xlsx(text: str) -> bytes:
    sheets = _rows_of(text) or [("", [])]
    parts, names, used = {}, [], set()
    for index, (title, rows) in enumerate(sheets, 1):
        name = re.sub(r"[\[\]:*?/\\']", " ", title).strip()[:28] or f"Sheet{index}"
        while name.lower() in used:
            name = f"{name[:25]} {index}"
        used.add(name.lower())
        names.append(name)
        width = max((len(row) for row in rows), default=1)
        lines = []
        for r, row in enumerate(rows, 1):
            cells = []
            for c, value in enumerate(row):
                ref = f"{_column(c)}{r}"
                value = value.strip()
                if not value:
                    continue
                if r > 1 and _NUMBER.match(value):
                    cells.append(f'<c r="{ref}"><v>{value}</v></c>')
                else:
                    style = 1 if r == 1 and len(rows) > 1 else 2
                    cells.append(f'<c r="{ref}" t="inlineStr" s="{style}"><is><t xml:space="preserve">{_xml(value)}</t></is></c>')
            lines.append(f'<row r="{r}">{"".join(cells)}</row>')
        columns = "".join(
            f'<col min="{c + 1}" max="{c + 1}" width="{min(max((len(row[c]) for row in rows if c < len(row)), default=8) + 2, 60)}" '
            'customWidth="1"/>' for c in range(width))
        frozen = ('<sheetViews><sheetView workbookViewId="0"><pane ySplit="1" topLeftCell="A2" activePane="bottomLeft" '
                  'state="frozen"/></sheetView></sheetViews>') if len(rows) > 1 else ""
        parts[f"xl/worksheets/sheet{index}.xml"] = (
            _XML_HEAD + '<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
            f"{frozen}<cols>{columns}</cols><sheetData>{''.join(lines)}</sheetData></worksheet>")
    book_sheets = "".join(f'<sheet name="{escape(_CONTROL.sub("", n), {chr(34): "&quot;"})}" sheetId="{i}" r:id="rId{i}"/>' for i, n in enumerate(names, 1))
    overrides = "".join(f'<Override PartName="/xl/worksheets/sheet{i}.xml" ContentType="application/vnd.openxmlformats-'
                        'officedocument.spreadsheetml.worksheet+xml"/>' for i in range(1, len(names) + 1))
    sheet_rels = "".join(f'<Relationship Id="rId{i}" Type="{_REL}/worksheet" Target="worksheets/sheet{i}.xml"/>'
                         for i in range(1, len(names) + 1))
    parts.update({
        "[Content_Types].xml": (
            _XML_HEAD + '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
            '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
            '<Default Extension="xml" ContentType="application/xml"/>'
            '<Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.'
            'spreadsheetml.sheet.main+xml"/>'
            '<Override PartName="/xl/styles.xml" ContentType="application/vnd.openxmlformats-officedocument.'
            f'spreadsheetml.styles+xml"/>{overrides}</Types>'),
        "_rels/.rels": (
            _XML_HEAD + '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
            f'<Relationship Id="rId1" Type="{_REL}/officeDocument" Target="xl/workbook.xml"/></Relationships>'),
        "xl/workbook.xml": (
            _XML_HEAD + '<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" '
            f'xmlns:r="{_REL}"><sheets>{book_sheets}</sheets></workbook>'),
        "xl/_rels/workbook.xml.rels": (
            _XML_HEAD + '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
            f'{sheet_rels}<Relationship Id="rId{len(names) + 1}" Type="{_REL}/styles" Target="styles.xml"/></Relationships>'),
        "xl/styles.xml": (
            _XML_HEAD + '<styleSheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
            '<fonts count="2"><font><sz val="11"/><name val="Calibri"/></font><font><b/><sz val="11"/><name val="Calibri"/></font></fonts>'
            '<fills count="2"><fill><patternFill patternType="none"/></fill><fill><patternFill patternType="gray125"/></fill></fills>'
            '<borders count="1"><border><left/><right/><top/><bottom/><diagonal/></border></borders>'
            '<cellStyleXfs count="1"><xf numFmtId="0" fontId="0" fillId="0" borderId="0"/></cellStyleXfs>'
            '<cellXfs count="3"><xf numFmtId="0" fontId="0" fillId="0" borderId="0" xfId="0"/>'
            '<xf numFmtId="0" fontId="1" fillId="0" borderId="0" xfId="0" applyFont="1"/>'
            '<xf numFmtId="0" fontId="0" fillId="0" borderId="0" xfId="0" applyAlignment="1"><alignment vertical="top" wrapText="1"/></xf>'
            '</cellXfs></styleSheet>'),
    })
    return _zip(parts)


def _safe_cell(value: str) -> str:
    """OWASP's fix for CSV injection: a spreadsheet runs a cell starting with these as a formula."""
    return f"'{value}" if value[:1] in ("=", "+", "-", "@", "\t", "\r") and not _NUMBER.match(value) else value


def to_csv(text: str) -> bytes:
    buffer = io.StringIO(newline="")
    writer = csv.writer(buffer)
    sheets = _rows_of(text)
    for n, (_, rows) in enumerate(sheets):
        if n:
            writer.writerow([])
        writer.writerows([[_safe_cell(cell) for cell in row] for row in rows])
    return buffer.getvalue().encode("utf-8-sig")


def _name(requested: str, text: str = "", default_ext: str = ".pdf") -> str:
    """A file name that is safe to hand to Discord, with one of the formats' extensions."""
    raw = Path(str(requested or "").replace("\\", "/")).name.strip()
    stem, ext = raw, ""
    if "." in raw:
        stem, _, tail = raw.rpartition(".")
        ext = f".{tail.lower()}"
    if not stem:
        first = next((b for b in parse(text) if b[0] in ("h", "p")), None)
        words = (_plain(first[2]) if first and first[0] == "h" else _plain(first[1][0]) if first else "")
        stem = " ".join(words.split()[:6])
    slug = re.sub(r"[\s_]+", "-", re.sub(r"[^\w\s-]", "", stem.lower())).strip("-")[:60] or "igor-document"
    return slug + (ext or default_ext)


def render(text: str, filename: str) -> tuple[bytes, str]:
    """(the file, a note if anything was lost). Text is cleaned here, so a key that reached a
    draft goes no further and a file reads like the rest of IGOR's writing."""
    text = sanitize.clean(_CONTROL.sub("", text or ""))
    ext = Path(filename).suffix.lower()
    if ext == ".docx":
        return to_docx(text), ""
    if ext == ".pdf":
        lost = sum(1 for c in text if not c.isspace() and c.encode("cp1252", "ignore") == b"")
        note = f"{lost} characters could not be shown in a PDF's font, so they appear as question marks: a Word file keeps them." if lost else ""
        return to_pdf(text, Path(filename).stem.replace("-", " ")), note
    if ext == ".xlsx":
        return to_xlsx(text), ""
    if ext == ".csv":
        return to_csv(text), ""
    return text.encode("utf-8-sig"), ""


def _size(count: int) -> str:
    return f"{count / 1048576:.1f} MB" if count >= 1048576 else f"{max(count // 1024, 1)} KB"


async def send(data: bytes, filename: str) -> bool:
    if _sender is None:
        logger.error("A file was ready to send, but no sender is set")
        return False
    return bool(await _sender(data, filename))


async def _send_checked(data: bytes, filename: str) -> str:
    """"" once it is sent, otherwise why it was not."""
    if len(data) > _MAX_BYTES:
        return f"{filename} came to {_size(len(data))}, over the 8 MB this can send."
    try:
        sent = await send(data, filename)
    except Exception as e:
        logger.error("Sending %s failed - %s: %s", filename, type(e).__name__, e)
        sent = False
    if not sent:
        return f"Discord would not take {filename}."
    logger.info("Sent %s, %d bytes", filename, len(data))
    return ""


async def _deliver(text: str, filename: str) -> tuple[str, str]:
    """(what was sent, a note on it) or ("", why not)."""
    try:
        data, note = render(text, filename)
    except Exception as e:
        logger.error("Could not make %s - %s: %s", filename, type(e).__name__, e)
        return "", f"I could not make {filename} ({type(e).__name__})."
    why = await _send_checked(data, filename)
    return ("", why) if why else (f"Sent {filename} ({_size(len(data))}).", note)


_RECEIPT_LINE = re.compile(r"^-# .*$", re.MULTILINE)
_PROACTIVE = "[sent proactively]"
# "Send that as Word" straight after "send that as a PDF" means the document, not the news of it.
_ABOUT_A_FILE = re.compile(r"^(-# )?Sent [^\s(]+ \([\d.]+ [KM]B\)\.", re.MULTILINE)
_SHORT_NOTE = 300


def last_reply() -> str:
    """IGOR's newest message, without the mark on a post or the receipts under a reply. Within a
    turn that is the reply before it: a turn's own is stored when it ends."""
    import context_store
    for row in reversed(context_store.load(12)):
        if row.get("role") == "assistant" and (row.get("content") or "").strip():
            text = row["content"].strip()
            text = text[len(_PROACTIVE):].strip() if text.startswith(_PROACTIVE) else text
            sent_a_file = bool(_ABOUT_A_FILE.search(text))
            text = _RECEIPT_LINE.sub("", text).strip()
            if text and not (sent_a_file and len(text) < _SHORT_NOTE):
                return text
    return ""


async def export_last(ext: str) -> str:
    """The user's "send that as a PDF": no model."""
    text = last_reply()
    if not text:
        return "I have no earlier message to turn into a file."
    sent, note = await _deliver(text, _name("", text, ext))
    return " ".join(x for x in (sent, note) if x)


def _from_path(path: str, name: str) -> tuple[Optional[bytes], str, str]:
    """(file as it is, or None) and (text to render), for a file on the server. The caller has
    checked the path against the file rules."""
    source = (config.BASE_DIR / path).resolve()
    ext = Path(name).suffix.lower()
    if source.suffix.lower() in _SENT_AS_THEY_ARE and source.suffix.lower() == ext:
        return source.read_bytes(), "", ""
    if source.suffix.lower() in _SENT_AS_THEY_ARE:
        return None, "", f"{source.suffix} cannot be turned into {ext}: give the same extension to send it as it is."
    return None, source.read_text(encoding="utf-8", errors="replace")[:_MAX_SOURCE_CHARS], ""


async def run_tool(inputs: dict) -> str:
    """React's send_file. The caller has checked `path` against the file rules."""
    content = inputs.get("content") or ""
    path = (inputs.get("path") or "").strip()
    wants_last = inputs.get("last_reply") is True or str(inputs.get("last_reply") or "").lower() == "true"
    if sum(bool(x) for x in (content.strip(), path, wants_last)) != 1:
        return "[NOT SENT: give exactly one of content, path or last_reply.]"
    default_ext = Path(path).suffix.lower() if path else ".pdf"
    default_ext = default_ext if default_ext in FORMATS else ".txt"
    raw = None
    text = content
    if wants_last:
        text = last_reply()
        if not text:
            return "[NOT SENT: there is no earlier reply to send.]"
    name = _name(inputs.get("name") or (Path(path).name if path else ""), text, default_ext)
    ext = Path(name).suffix.lower()
    if path:
        source = (config.BASE_DIR / path).resolve()
        if not source.is_file():
            return f"[NOT SENT: there is no file at {path}.]"
        if source.stat().st_size > _MAX_BYTES * 4:
            return f"[NOT SENT: {path} is {_size(source.stat().st_size)}, too large to send.]"
        try:
            raw, text, why = _from_path(path, name)
        except OSError as e:
            return f"[NOT SENT: could not read {path} ({type(e).__name__}).]"
        if why:
            return f"[NOT SENT: {why}]"
    if raw is not None:
        why = await _send_checked(raw, name)
        return f"[NOT SENT: {why} Tell the user.]" if why else f"Sent {name} ({_size(len(raw))})."
    if ext not in FORMATS:
        return f"[NOT SENT: {ext or 'no extension'} is not offered. Name it with one of {', '.join(FORMATS)}.]"
    sent, note = await _deliver(text, name)
    if not sent:
        return f"[NOT SENT: {note} Tell the user.]"
    # On its own line: the first line is what the user sees under the reply.
    return f"{sent}\n{note}" if note else sent
