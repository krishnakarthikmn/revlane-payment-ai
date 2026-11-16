"""A small PDF writer: pages of text, nothing else. Standard library only.

Enough for a claim: a title, paragraphs and a table of record ids and
amounts. Uses the fonts every PDF reader has built in, which cover Latin
text but not the rupee sign, so amounts are written with "INR".
"""

from dataclasses import dataclass

PAGE_WIDTH, PAGE_HEIGHT = 595, 842  # A4, in points
MARGIN = 56
LEADING = 14
FONTS = {"regular": "F1", "bold": "F2", "mono": "F3"}
SIZES = {"title": 16, "heading": 11, "body": 10, "mono": 9}
# Characters that fit across the page at each size.
WRAP = {"body": 92, "mono": 88}


@dataclass(frozen=True, slots=True)
class Line:
    text: str
    font: str = "regular"
    size: int = SIZES["body"]


def wrap(text: str, width: int = WRAP["body"]) -> list[str]:
    """Break a paragraph into lines at word boundaries."""
    lines: list[str] = []
    current = ""
    for word in text.split():
        while len(word) > width:  # an id longer than the page is wide
            if current:
                lines.append(current)
                current = ""
            lines.append(word[:width])
            word = word[width:]
        if current and len(current) + 1 + len(word) > width:
            lines.append(current)
            current = word
        else:
            current = f"{current} {word}".strip()
    if current:
        lines.append(current)
    return lines or [""]


def _escape(text: str) -> bytes:
    plain = text.replace("₹", "INR ").replace("−", "-").replace("·", "-").replace("’", "'")
    encoded = plain.encode("latin-1", errors="replace")
    return encoded.replace(b"\\", b"\\\\").replace(b"(", b"\\(").replace(b")", b"\\)")


def _page(lines: list[Line], number: int, pages: int) -> bytes:
    parts = [b"BT"]
    y = PAGE_HEIGHT - MARGIN
    for line in lines:
        parts.append(f"/{FONTS[line.font]} {line.size} Tf".encode())
        parts.append(f"1 0 0 1 {MARGIN} {y} Tm".encode())
        parts.append(b"(" + _escape(line.text) + b") Tj")
        y -= LEADING + max(0, line.size - SIZES["body"])
    parts.append(f"/{FONTS['regular']} 8 Tf".encode())
    parts.append(f"1 0 0 1 {MARGIN} {MARGIN - 24} Tm".encode())
    parts.append(f"(Page {number} of {pages}) Tj".encode())
    parts.append(b"ET")
    return b"\n".join(parts)


def render(lines: list[Line]) -> bytes:
    """A PDF with the lines laid out top to bottom, a new page when one fills."""
    per_page = (PAGE_HEIGHT - 2 * MARGIN) // LEADING - 2
    chunks = [lines[start : start + per_page] for start in range(0, len(lines), per_page)] or [[]]

    objects: list[bytes] = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"",  # the page tree, filled in below
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica /Encoding /WinAnsiEncoding >>",
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica-Bold /Encoding /WinAnsiEncoding >>",
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Courier /Encoding /WinAnsiEncoding >>",
    ]
    kids = []
    for number, chunk in enumerate(chunks, start=1):
        stream = _page(chunk, number, len(chunks))
        content = len(objects) + 1
        objects.append(b"<< /Length %d >>\nstream\n%s\nendstream" % (len(stream), stream))
        objects.append(
            (
                f"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 {PAGE_WIDTH} {PAGE_HEIGHT}] "
                f"/Resources << /Font << /F1 3 0 R /F2 4 0 R /F3 5 0 R >> >> "
                f"/Contents {content} 0 R >>"
            ).encode()
        )
        kids.append(f"{len(objects)} 0 R")
    objects[1] = f"<< /Type /Pages /Kids [{' '.join(kids)}] /Count {len(kids)} >>".encode()

    out = bytearray(b"%PDF-1.4\n%\xe2\xe3\xcf\xd3\n")
    offsets = []
    for index, body in enumerate(objects, start=1):
        offsets.append(len(out))
        out += f"{index} 0 obj\n".encode() + body + b"\nendobj\n"
    xref = len(out)
    out += f"xref\n0 {len(objects) + 1}\n".encode()
    out += b"0000000000 65535 f \n"
    for offset in offsets:
        out += f"{offset:010d} 00000 n \n".encode()
    out += (
        f"trailer\n<< /Size {len(objects) + 1} /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF\n".encode()
    )
    return bytes(out)
