"""Build small text PDFs from invented page text, so tests need no real bill."""

from tests.test_amber import CLOSE, DASH, OPEN, page1, page2


def make_pdf(pages: list[str]) -> bytes:
    """One Helvetica text line per source line. Characters outside Latin-1 become spaces."""
    objects = [b"<< /Type /Catalog /Pages 2 0 R >>", b""]
    kids = []
    for text in pages:
        lines = []
        for line in text.splitlines():
            safe = "".join(c if ord(c) < 256 else " " for c in line).encode("latin-1")
            safe = safe.replace(b"\\", b"\\\\").replace(b"(", b"\\(").replace(b")", b"\\)")
            lines.append(b"(" + safe + b") Tj T*")
        stream = b"BT /F1 9 Tf 12 TL 40 800 Td " + b" ".join(lines) + b" ET"
        objects.append(b"<< /Length %d >>\nstream\n" % len(stream) + stream + b"\nendstream")
        content = len(objects)
        objects.append(
            b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 595 842] /Contents %d 0 R "
            b"/Resources << /Font << /F1 << /Type /Font /Subtype /Type1 "
            b"/BaseFont /Helvetica /Encoding /WinAnsiEncoding >> >> >> >>" % content
        )
        kids.append(b"%d 0 R" % len(objects))
    objects[1] = b"<< /Type /Pages /Kids [%s] /Count %d >>" % (b" ".join(kids), len(kids))
    out = bytearray(b"%PDF-1.4\n")
    offsets = []
    for number, body in enumerate(objects, 1):
        offsets.append(len(out))
        out += b"%d 0 obj\n" % number + body + b"\nendobj\n"
    xref = len(out)
    out += b"xref\n0 %d\n0000000000 65535 f \n" % (len(objects) + 1)
    out += b"".join(b"%010d 00000 n \n" % offset for offset in offsets)
    out += b"trailer\n<< /Size %d /Root 1 0 R >>\nstartxref\n%d\n%%%%EOF\n" % (
        len(objects) + 1,
        xref,
    )
    return bytes(out)


def amber_pdf() -> bytes:
    """An invented two-page Amber bill: header page and summary page."""
    plain = str.maketrans({OPEN: " ", CLOSE: " ", DASH: " ", "−": "-"})
    return make_pdf([page1().translate(plain), page2().translate(plain)])


def origin_pdf() -> bytes:
    """An invented three-page Origin Energy bill with one table cell per line."""
    from tests.test_origin import bill

    return make_pdf(bill())
