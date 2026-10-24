import pytest

from app.services.documents import clean_filename


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("handbook.pdf", "handbook.pdf"),
        ("../../etc/passwd", "passwd"),
        ("C:\\Users\\me\\report.pdf", "report.pdf"),
        ("bad\x00name\n.txt", "badname.txt"),
        ("", "untitled"),
        (None, "untitled"),
    ],
)
def test_clean_filename(raw: str | None, expected: str) -> None:
    assert clean_filename(raw) == expected


def test_long_filenames_are_cut() -> None:
    assert len(clean_filename("a" * 1000 + ".pdf")) == 255
