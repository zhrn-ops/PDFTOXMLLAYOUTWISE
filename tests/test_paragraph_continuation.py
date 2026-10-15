"""Tests for paragraph-continuation validation.

A continuation is decided by structure, not by per-line styling: the lines of
one paragraph can mix alignment and inline emphasis, so those must not block a
continuation. Clearly different font sizes and non-paragraph blocks still must.
"""

from pdf_to_jats.gui.main_window import MainWindow
from pdf_to_jats.models.block import TextBlock
from pdf_to_jats.models.document import Document


def _window() -> MainWindow:
    """Build the minimal MainWindow state the validation helper needs."""

    window = MainWindow.__new__(MainWindow)
    window.document = Document()
    return window


def _unit(
    block_id: str,
    order: int,
    *,
    y: float = 100.0,
    x: float = 28.0,
    font_size: float = 10.0,
    alignment: str = "left",
    bold: bool = False,
    italic: bool = False,
    paragraph_unit: bool = True,
) -> TextBlock:
    metadata = {"reading_order": order}
    if paragraph_unit:
        metadata["is_paragraph_unit"] = True
        metadata["merged_block_ids"] = [f"{block_id}_src"]
    return TextBlock(
        id=block_id,
        page=1,
        text="this is a reasonably long paragraph line of text",
        bbox=[x, y, x + 270.0, y + 20.0],
        x=x,
        y=y,
        width=270.0,
        height=20.0,
        font_size=font_size,
        alignment=alignment,
        bold=bold,
        italic=italic,
        metadata=metadata,
    )


def test_continuation_allows_alignment_and_style_variation():
    """A justified line and a bold lead-in must not block a continuation."""

    window = _window()
    first = _unit("block_001", 0, alignment="left")
    second = _unit("block_002", 1, y=124.0, alignment="center", bold=True, italic=True)

    valid, reason = window._selection_looks_like_paragraph_continuation([first, second])

    assert valid, reason


def test_continuation_still_rejects_mismatched_font_sizes():
    window = _window()
    first = _unit("block_001", 0, font_size=10.0)
    second = _unit("block_002", 1, y=124.0, font_size=20.0)

    valid, reason = window._selection_looks_like_paragraph_continuation([first, second])

    assert not valid
    assert "font sizes" in reason


def test_continuation_still_rejects_plain_blocks():
    window = _window()
    first = _unit("block_001", 0)
    second = _unit("block_002", 1, y=124.0, paragraph_unit=False)

    valid, reason = window._selection_looks_like_paragraph_continuation([first, second])

    assert not valid
    assert "paragraph units" in reason
