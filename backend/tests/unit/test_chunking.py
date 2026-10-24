import pytest

from app.ingestion.chunking import chunk_hash, chunk_pages, estimate_tokens
from app.ingestion.parsing import Page


def _paragraph(word: str, words: int) -> str:
    return " ".join([word] * words)


def test_short_page_becomes_one_chunk() -> None:
    chunks = chunk_pages([Page(1, "A short page.")], target_tokens=100, overlap_tokens=10)

    assert len(chunks) == 1
    assert chunks[0].page == 1
    assert chunks[0].text == "A short page."


def test_chunks_never_cross_a_page_boundary() -> None:
    pages = [Page(1, "Page one text."), Page(2, "Page two text.")]

    chunks = chunk_pages(pages, target_tokens=1000, overlap_tokens=10)

    assert [(c.page, c.text) for c in chunks] == [(1, "Page one text."), (2, "Page two text.")]


def test_paragraphs_are_kept_whole_when_they_fit() -> None:
    # Each paragraph is about 25 tokens; a 60-token target fits two of them.
    paragraphs = [_paragraph(word, 20) for word in ("alpha", "bravo", "charlie", "delta")]
    page = Page(1, "\n\n".join(paragraphs))

    chunks = chunk_pages([page], target_tokens=60, overlap_tokens=0)

    for chunk in chunks:
        for paragraph in chunk.text.split("\n\n"):
            assert paragraph in paragraphs
    assert all(c.token_count <= 60 for c in chunks)


def test_consecutive_chunks_overlap() -> None:
    paragraphs = [_paragraph(word, 30) for word in ("alpha", "bravo", "charlie")]
    page = Page(1, "\n\n".join(paragraphs))

    chunks = chunk_pages([page], target_tokens=70, overlap_tokens=15)

    assert len(chunks) >= 2
    tail_of_first = chunks[0].text.split()[-3:]
    assert chunks[1].text.split()[:3] == tail_of_first


def test_long_paragraph_is_split_and_no_words_are_lost() -> None:
    sentences = [f"Sentence number {i} has a few words in it." for i in range(60)]
    page = Page(1, " ".join(sentences))

    chunks = chunk_pages([page], target_tokens=50, overlap_tokens=0)

    assert len(chunks) > 1
    assert all(c.token_count <= 50 for c in chunks)
    joined = " ".join(c.text for c in chunks)
    assert joined.split() == page.text.split()


def test_single_word_longer_than_a_chunk_does_not_loop_forever() -> None:
    page = Page(1, "x" * 2000)

    chunks = chunk_pages([page], target_tokens=100, overlap_tokens=10)

    assert len(chunks) == 1


def test_headings_set_the_section_of_following_chunks() -> None:
    text = "# Leave policy\n\nStaff get 20 days.\n\n## Sick leave\n\nTen days per year."
    pages = [Page(1, text), Page(2, "More about sick leave.")]

    chunks = chunk_pages(pages, target_tokens=8, overlap_tokens=0)

    def section_of(fragment: str) -> str | None:
        return next(c.section for c in chunks if fragment in c.text)

    assert section_of("Staff get 20 days.") == "Leave policy"
    assert section_of("Ten days per year.") == "Sick leave"
    # The section carries over to the next page.
    assert section_of("More about sick leave.") == "Sick leave"


def test_a_heading_stays_with_the_paragraph_after_it() -> None:
    text = "Intro paragraph that fills the first chunk.\n\n## Sick leave\n\nTen days per year."

    chunks = chunk_pages([Page(1, text)], target_tokens=12, overlap_tokens=0)

    assert any(c.text.startswith("## Sick leave") and "Ten days" in c.text for c in chunks)


def test_numbered_headings_are_recognised() -> None:
    chunks = chunk_pages(
        [Page(1, "3.2 Annual Leave\nStaff get 20 days.")], target_tokens=100, overlap_tokens=0
    )

    assert chunks[0].section == "3.2 Annual Leave"


def test_a_numbered_sentence_is_not_a_heading() -> None:
    chunks = chunk_pages(
        [Page(1, "1. Submit the form before Friday.")], target_tokens=100, overlap_tokens=0
    )

    assert chunks[0].section is None


def test_overlap_must_be_smaller_than_target() -> None:
    with pytest.raises(ValueError):
        chunk_pages([Page(1, "text")], target_tokens=10, overlap_tokens=10)


def test_chunk_hash_ignores_case_and_spacing() -> None:
    assert chunk_hash("Hello   World") == chunk_hash("hello world")
    assert chunk_hash("hello world") != chunk_hash("hello there")


def test_token_estimate_is_about_four_characters_per_token() -> None:
    assert estimate_tokens("") == 1
    assert estimate_tokens("x" * 400) == 100
