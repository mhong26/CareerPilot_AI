"""text_extract 單元測試（Phase 9 補死角）：真 PDF / DOCX 抽取、magic-byte
白名單、損毀檔與過短文字。"""

import pytest

from app.ai.parsers.text_extract import (
    TextExtractionError,
    UnsupportedFileTypeError,
    extract_plain_text,
    extract_text,
)
from tests.fakes import MINIMAL_PDF, make_docx_bytes


def test_extract_pdf_happy_path():
    text, source_type = extract_text(
        MINIMAL_PDF, content_type="application/pdf", filename="resume.pdf"
    )
    assert source_type == "pdf"
    assert "Hello resume world" in text


def test_extract_docx_happy_path():
    content = make_docx_bytes("Jane Smith — backend engineer with Python.")
    text, source_type = extract_text(
        content,
        content_type=("application/vnd.openxmlformats-officedocument.wordprocessingml.document"),
        filename="resume.docx",
    )
    assert source_type == "docx"
    assert "backend engineer" in text


def test_unsupported_extension_rejected():
    with pytest.raises(UnsupportedFileTypeError):
        extract_text(b"plain text", content_type="text/plain", filename="resume.txt")


def test_magic_byte_mismatch_rejected():
    """改名成 .pdf 的 ZIP（DOCX 檔頭）→ 415 類例外，不進 pypdf。"""
    with pytest.raises(UnsupportedFileTypeError):
        extract_text(b"PK\x03\x04 not actually a pdf", content_type=None, filename="fake.pdf")


def test_corrupt_pdf_with_valid_magic_rejected():
    with pytest.raises(TextExtractionError):
        extract_text(b"%PDF-1.4 garbage garbage", content_type=None, filename="bad.pdf")


def test_corrupt_docx_with_valid_magic_rejected():
    with pytest.raises(TextExtractionError):
        extract_text(b"PK\x03\x04 truncated zip", content_type=None, filename="bad.docx")


def test_scanned_pdf_without_text_rejected():
    """有效 PDF 但抽不出文字（如掃描影像）→ TextExtractionError。"""
    empty_page_pdf = MINIMAL_PDF.replace(
        b"BT /F1 12 Tf 72 720 Td (Hello resume world from a tiny PDF file) Tj ET",
        b"BT /F1 12 Tf 72 720 Td () Tj ET" + b" " * 36,
    )
    with pytest.raises(TextExtractionError):
        extract_text(empty_page_pdf, content_type=None, filename="scan.pdf")


def test_plain_text_too_short_rejected():
    with pytest.raises(TextExtractionError):
        extract_plain_text("   hi   ")
