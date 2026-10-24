import pytest

from app.ingestion.parsing import (
    MARKDOWN,
    PDF,
    PLAIN_TEXT,
    ParseError,
    detect_content_type,
    parse_document,
)
from tests.pdf_factory import make_pdf


def test_pdf_is_detected_from_its_bytes_not_its_name() -> None:
    assert detect_content_type(make_pdf(["hello"])[:8192], "notes.txt") == PDF


@pytest.mark.parametrize(
    ("filename", "expected"),
    [("notes.txt", PLAIN_TEXT), ("README.md", MARKDOWN), ("Guide.MARKDOWN", MARKDOWN)],
)
def test_text_files_are_detected_by_extension(filename: str, expected: str) -> None:
    assert detect_content_type(b"Plain words", filename) == expected


@pytest.mark.parametrize(
    ("head", "filename"),
    [
        (b"MZ\x90\x00binary", "virus.txt"),  # binary pretending to be text
        (b"\xff\xfe\xfa not utf-8 at all", "notes.txt"),
        (b"Plain words", "photo.png"),
        (b"Plain words", "no-extension"),
    ],
)
def test_unsupported_files_are_rejected(head: bytes, filename: str) -> None:
    assert detect_content_type(head, filename) is None


def test_text_cut_mid_character_is_still_text() -> None:
    head = "café".encode()[:-1]  # the last byte of "é" is missing

    assert detect_content_type(head, "menu.txt") == PLAIN_TEXT


def test_pdf_pages_keep_their_page_numbers() -> None:
    pdf = make_pdf(["First page text", "Second page text"])

    pages = parse_document(pdf, PDF)

    assert [(p.number, p.text) for p in pages] == [
        (1, "First page text"),
        (2, "Second page text"),
    ]


def test_whitespace_is_tidied_but_paragraph_breaks_are_kept() -> None:
    raw = "Line   one\r\n\r\n\r\n\r\nLine\ttwo  \n"

    pages = parse_document(raw.encode(), PLAIN_TEXT)

    assert pages[0].text == "Line one\n\nLine two"


def test_utf8_byte_order_mark_is_removed() -> None:
    pages = parse_document("﻿Hello".encode(), PLAIN_TEXT)

    assert pages[0].text == "Hello"


def test_pdf_without_text_explains_that_ocr_is_needed() -> None:
    with pytest.raises(ParseError, match="OCR"):
        parse_document(make_pdf([""]), PDF)


def test_damaged_pdf_gives_a_clear_error() -> None:
    with pytest.raises(ParseError, match="damaged"):
        parse_document(b"%PDF-1.4\nthis is not really a pdf", PDF)


def test_invalid_utf8_text_gives_a_clear_error() -> None:
    with pytest.raises(ParseError, match="UTF-8"):
        parse_document(b"\xff\xfe\xfa", PLAIN_TEXT)
