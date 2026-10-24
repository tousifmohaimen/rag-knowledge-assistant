"""Split page text into chunks that are small enough to search well.

Rules:

* A chunk never crosses a page boundary, so every chunk has exactly one page number
  to cite. The trade-off is that a paragraph split across two PDF pages becomes two
  chunks.
* Chunks are built from whole paragraphs where possible. Only a paragraph that is
  longer than a chunk on its own gets split, first at sentence ends, then at words.
* Each chunk after the first on a page starts with the last ``overlap`` tokens of the
  previous one, so a sentence near a boundary is still found with its context.
* The most recent heading is remembered as the chunk's ``section``.

Token counts are estimated (about four characters per token for English). That keeps
the chunker fast and dependency-free; exact counts only matter at the LLM step.
"""

import hashlib
import re
from collections.abc import Iterable, Iterator
from dataclasses import dataclass

from app.ingestion.parsing import Page

_SENTENCE_END = re.compile(r"(?<=[.!?])\s+")
_MARKDOWN_HEADING = re.compile(r"^#{1,6}\s+(.+)$")
_NUMBERED_HEADING = re.compile(r"^(\d+(\.\d+)*\.?)\s+[A-Z].{0,80}$")
_MAX_SECTION_LENGTH = 500


def estimate_tokens(text: str) -> int:
    return max(1, round(len(text) / 4))


def chunk_hash(text: str) -> str:
    return hashlib.sha256(" ".join(text.split()).lower().encode("utf-8")).hexdigest()


@dataclass(frozen=True, slots=True)
class ChunkDraft:
    page: int
    section: str | None
    text: str
    token_count: int
    chunk_hash: str


def chunk_pages(
    pages: Iterable[Page], *, target_tokens: int, overlap_tokens: int
) -> list[ChunkDraft]:
    if overlap_tokens >= target_tokens:
        raise ValueError("overlap_tokens must be smaller than target_tokens")

    drafts: list[ChunkDraft] = []
    section: str | None = None
    for page in pages:
        current: list[str] = []
        current_tokens = 0
        current_section = section
        # False while ``current`` holds only overlap copied from the previous chunk.
        has_new_text = False

        for block in _blocks(page.text):
            heading = _heading_text(block)
            if heading is not None:
                section = heading[:_MAX_SECTION_LENGTH]

            for piece in _split_to_fit(block, target_tokens):
                piece_tokens = estimate_tokens(piece)
                if has_new_text and current_tokens + piece_tokens > target_tokens:
                    drafts.append(_draft(page.number, current_section, current))
                    current = _tail(current, overlap_tokens)
                    current_tokens = estimate_tokens("\n\n".join(current)) if current else 0
                    current_section = section
                    has_new_text = False
                if not has_new_text and not current:
                    current_section = section
                current.append(piece)
                current_tokens += piece_tokens
                has_new_text = True

        if has_new_text:
            drafts.append(_draft(page.number, current_section, current))
    return drafts


def _blocks(text: str) -> Iterator[str]:
    """Paragraphs, plus headings as their own blocks."""
    paragraph: list[str] = []
    for line in text.split("\n"):
        if not line.strip():
            if paragraph:
                yield "\n".join(paragraph)
                paragraph = []
            continue
        if _heading_text(line) is not None:
            if paragraph:
                yield "\n".join(paragraph)
                paragraph = []
            yield line
            continue
        paragraph.append(line)
    if paragraph:
        yield "\n".join(paragraph)


def _heading_text(block: str) -> str | None:
    if "\n" in block:
        return None
    stripped = block.strip()
    markdown = _MARKDOWN_HEADING.match(stripped)
    if markdown:
        return markdown.group(1).strip()
    if _NUMBERED_HEADING.match(stripped) and not stripped.endswith("."):
        return stripped
    return None


def _split_to_fit(block: str, target_tokens: int) -> list[str]:
    if estimate_tokens(block) <= target_tokens:
        return [block]
    pieces: list[str] = []
    for sentence in _SENTENCE_END.split(block):
        if estimate_tokens(sentence) <= target_tokens:
            pieces.append(sentence)
        else:
            pieces.extend(_split_words(sentence, target_tokens))
    return _merge_small(pieces, target_tokens)


def _split_words(text: str, target_tokens: int) -> list[str]:
    pieces: list[str] = []
    words: list[str] = []
    for word in text.split():
        candidate = " ".join([*words, word])
        if words and estimate_tokens(candidate) > target_tokens:
            pieces.append(" ".join(words))
            words = [word]
        else:
            words.append(word)
    if words:
        pieces.append(" ".join(words))
    return pieces


def _merge_small(pieces: list[str], target_tokens: int) -> list[str]:
    merged: list[str] = []
    for piece in pieces:
        if merged and estimate_tokens(merged[-1] + " " + piece) <= target_tokens:
            merged[-1] = merged[-1] + " " + piece
        else:
            merged.append(piece)
    return merged


def _tail(pieces: list[str], overlap_tokens: int) -> list[str]:
    """The last ~overlap_tokens of the previous chunk, cut at a word boundary."""
    if overlap_tokens == 0:
        return []
    words = "\n\n".join(pieces).split()
    tail: list[str] = []
    for word in reversed(words):
        if estimate_tokens(" ".join([word, *tail])) > overlap_tokens:
            break
        tail.insert(0, word)
    return [" ".join(tail)] if tail else []


def _draft(page: int, section: str | None, pieces: list[str]) -> ChunkDraft:
    text = "\n\n".join(pieces).strip()
    return ChunkDraft(
        page=page,
        section=section,
        text=text,
        token_count=estimate_tokens(text),
        chunk_hash=chunk_hash(text),
    )
