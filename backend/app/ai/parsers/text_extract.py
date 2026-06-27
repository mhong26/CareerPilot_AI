"""從上傳檔案 / 貼上文字抽取純文字（FR-8）。

支援 PDF（pypdf）、DOCX（python-docx）、純文字。抽不到有效文字（不支援格式、
掃描影像 PDF、空白 / 損毀檔）時丟例外，由呼叫端拒絕上傳、不建立 Resume。
注意：這與「LLM 解析失敗」不同——後者仍建 Resume 並存 parse_error（FR-10）。
"""

import io

import pypdf
from docx import Document


class UnsupportedFileTypeError(Exception):
    """副檔名 / MIME 不在 pdf / docx 白名單。"""


class TextExtractionError(Exception):
    """檔案可辨識但抽取後沒有有效文字（空白 / 掃描影像 / 損毀）。"""


# 少於此字數視為「沒抽到東西」。
_MIN_CHARS = 10


def _extract_pdf(content: bytes) -> str:
    reader = pypdf.PdfReader(io.BytesIO(content))
    return "\n".join(page.extract_text() or "" for page in reader.pages)


def _extract_docx(content: bytes) -> str:
    doc = Document(io.BytesIO(content))
    return "\n".join(p.text for p in doc.paragraphs)


def extract_text(
    content: bytes, *, content_type: str | None, filename: str | None
) -> tuple[str, str]:
    """回傳 (raw_text, source_type)，source_type ∈ {pdf, docx}。

    依副檔名判型（fallback 用 MIME）。不支援的型別丟 ``UnsupportedFileTypeError``；
    檔案損毀或抽不到可用文字丟 ``TextExtractionError``。
    """
    name = (filename or "").lower()
    ctype = (content_type or "").lower()
    if name.endswith(".pdf") or "pdf" in ctype:
        source_type = "pdf"
    elif name.endswith(".docx") or "wordprocessingml" in ctype:
        source_type = "docx"
    else:
        raise UnsupportedFileTypeError(filename or content_type or "unknown")

    try:
        raw = _extract_pdf(content) if source_type == "pdf" else _extract_docx(content)
    except Exception as exc:  # pypdf / docx 對損毀檔丟的各式例外
        raise TextExtractionError(f"檔案解析失敗：{exc}") from exc

    raw = raw.strip()
    if len(raw) < _MIN_CHARS:
        raise TextExtractionError("檔案中找不到可用文字（可能是掃描影像或空白檔）")
    return raw, source_type


def extract_plain_text(text: str) -> tuple[str, str]:
    """貼上文字路徑：strip 後驗證非空，回 (text, 'text')。"""
    cleaned = text.strip()
    if len(cleaned) < _MIN_CHARS:
        raise TextExtractionError("貼上的文字內容太短或為空")
    return cleaned, "text"
