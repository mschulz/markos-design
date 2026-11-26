"""Isolated PDF page reading and PDF originals, on generated PDFs only."""

import asyncio
import hashlib
import io
from pathlib import Path
from uuid import UUID

import pytest

from markos_runtime.knowledge.amber import extract_amber, is_amber
from markos_runtime.knowledge.objects import BillObjectError, BillObjects
from markos_runtime.knowledge.pdf import MAX_PDF_BYTES, PdfError, read_pages
from tests.pdf_support import amber_pdf, make_pdf

OWNER = UUID("00000000-0000-4000-8000-000000000001")


def error(body: bytes) -> str:
    with pytest.raises(PdfError) as caught:
        asyncio.run(read_pages(body))
    return caught.value.code


def test_generated_amber_pdf_reads_into_reconciled_facts() -> None:
    body = amber_pdf()
    pages = asyncio.run(read_pages(body))
    assert len(pages) == 2 and is_amber(pages)
    report = extract_amber(pages, hashlib.sha256(body).hexdigest())
    assert {fact.state for fact in report.facts} == {"found"}
    total = next(fact for fact in report.facts if fact.key == "total_due")
    assert total.value == "47.99" and total.evidence[0].page == 2
    # Reading the same original again gives the same report, so stored facts can be rechecked.
    assert extract_amber(asyncio.run(read_pages(body)), report.source_sha256) == report


def test_bad_input_is_rejected_with_a_bounded_code() -> None:
    from pypdf import PdfWriter

    writer = PdfWriter()
    writer.add_blank_page(595, 842)
    writer.encrypt("invented-password")
    encrypted = io.BytesIO()
    writer.write(encrypted)
    assert error(b"not a pdf") == "not-pdf"
    assert error(b"%PDF-" + b"0" * MAX_PDF_BYTES) == "too-large"
    assert error(b"%PDF-1.4\nnot really a pdf") == "unreadable"
    assert error(encrypted.getvalue()) == "encrypted"
    assert error(make_pdf(["page"] * 21)) == "too-many-pages"
    assert error(make_pdf(["x" * 70000])) == "too-much-text"
    # A page with no text stands in for a scanned bill.
    assert error(make_pdf([""])) == "no-text"


def test_pdf_originals_have_their_own_suffix_and_limit(tmp_path: Path) -> None:
    root = tmp_path / "objects"
    root.mkdir(mode=0o700)
    objects = BillObjects(root)
    body = amber_pdf()
    digest = objects.put(OWNER, "personal", body, ".pdf")
    assert objects.read(OWNER, "personal", digest, ".pdf") == body
    assert [p.name for p in root.rglob("*.pdf")] == [digest + ".pdf"]
    with pytest.raises(OSError):
        objects.read(OWNER, "personal", digest)
    with pytest.raises(BillObjectError):
        objects.put(OWNER, "personal", b"x" * (MAX_PDF_BYTES + 1), ".pdf")
    with pytest.raises(BillObjectError):
        objects.put(OWNER, "personal", b"x" * 20000)
