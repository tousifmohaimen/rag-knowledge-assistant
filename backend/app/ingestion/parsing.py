"""Turn an uploaded file into text, page by page.

Page numbers are kept because every answer cites the page it came from.
"""

import io
import logging
from dataclasses import dataclass

from pypdf import PdfReader
from pypdf.errors import PdfReadError

logger = logging.getLogger(__name__)

PDF = "application/pdf"
PLAIN_TEXT = "text/plain"
MARKDOWN = "text/markdown"
SUPPORTED_TYPES = frozenset({PDF, PLAIN_TEXT, MARKDOWN})

_TEXT_EXTENSIONS = {".txt": PLAIN_TEXT, ".md": MARKDOWN, ".markdown": MARKDOWN}


class ParseError(Exception):
    """The file can't be turned into text. The message is safe to show to users."""


@dataclass(frozen=True, slots=True)
class Page:
    number: int  # 1-based, as people count pages
    text: str


def detect_content_type(head: bytes, filename: str) -> str | None:
    """Decide the file type from its first bytes, not from what the client claims.

    A client can send any Content-Type header, so it is ignored. PDFs are recognised by
    their magic bytes; text files by their extension plus a check that the bytes really
    are UTF-8 text.
    """
    if head.startswith(b"%PDF-"):
        return PDF
    extension = "." + filename.rsplit(".", 1)[-1].lower() if "." in filename else ""
    text_type = _TEXT_EXTENSIONS.get(extension)
    if text_type is None or b"\x00" in head:
        return None
    try:
        # The head may end in the middle of a multi-byte character, so allow that.
        head.decode("utf-8", errors="strict")
    except UnicodeDecodeError as exc:
        if exc.start < len(head) - 3:
            return None
    return text_type


def parse_document(data: bytes, content_type: str) -> list[Page]:
    if content_type == PDF:
        pages = _parse_pdf(data)
    elif content_type in (PLAIN_TEXT, MARKDOWN):
        pages = [Page(number=1, text=_decode_text(data))]
    else:
        raise ParseError(f"Unsupported file type: {content_type}")

    pages = [Page(p.number, _normalise(p.text)) for p in pages]
    if not any(p.text for p in pages):
        raise ParseError(
            "No text could be extracted. If this is a scanned PDF, it needs OCR, "
            "which isn't supported yet."
        )
    return pages


def _parse_pdf(data: bytes) -> list[Page]:
    try:
        reader = PdfReader(io.BytesIO(data))
        if reader.is_encrypted and not reader.decrypt(""):
            raise ParseError("The PDF is password-protected.")
        return [
            Page(number=index, text=page.extract_text() or "")
            for index, page in enumerate(reader.pages, start=1)
        ]
    except ParseError:
        raise
    except (PdfReadError, ValueError, KeyError, TypeError) as exc:
        logger.warning("could not read PDF: %s", exc)
        raise ParseError("The PDF is damaged or not a valid PDF.") from exc


def _decode_text(data: bytes) -> str:
    try:
        return data.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        raise ParseError("The text file is not valid UTF-8.") from exc


def _normalise(text: str) -> str:
    """Tidy whitespace while keeping blank lines, which mark paragraph breaks."""
    text = text.replace("\r\n", "\n").replace("\r", "\n").replace("\x00", "")
    lines = [" ".join(line.split()) for line in text.split("\n")]
    cleaned: list[str] = []
    for line in lines:
        if line or (cleaned and cleaned[-1]):
            cleaned.append(line)
    return "\n".join(cleaned).strip()
