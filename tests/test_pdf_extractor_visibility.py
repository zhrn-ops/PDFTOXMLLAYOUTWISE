"""Regression tests for rendered-ink text visibility checks."""

import fitz
import pytest

from pdf_to_jats.core import pdf_extractor
from pdf_to_jats.core.pdf_extractor import PDFExtractor


def _write_mixed_pdf(path) -> None:
    """Write a page with visible, render-mode-3, and white text lines."""

    document = fitz.open()
    page = document.new_page()
    page.insert_text((72, 72), "VISIBLE LINE ONE", fontsize=12)
    writer = fitz.TextWriter(page.rect)
    writer.append((72, 120), "HIDDEN OCR LAYER LINE", fontsize=12)
    writer.write_text(page, render_mode=3)
    page.insert_text((72, 168), "WHITE INVISIBLE LINE", fontsize=12, color=(1, 1, 1))
    page.insert_text((72, 216), "VISIBLE LINE TWO", fontsize=12)
    document.save(str(path))
    document.close()


@pytest.mark.parametrize("use_numpy", [True, False])
def test_visible_text_only_drops_hidden_lines(tmp_path, monkeypatch, use_numpy):
    """Invisible and white lines are excluded; rendered lines are kept."""

    if not use_numpy:
        monkeypatch.setattr(pdf_extractor, "_numpy", None)
    path = tmp_path / "mixed.pdf"
    _write_mixed_pdf(path)

    document = PDFExtractor(use_docling=False, visible_text_only=True).extract(path)

    assert document.metadata["hidden_text_line_count"] == 2
    assert [block.text for block in document.blocks] == [
        "VISIBLE LINE ONE",
        "VISIBLE LINE TWO",
    ]


def test_numpy_and_fallback_paths_agree(tmp_path, monkeypatch):
    """The vectorized scan and its numpy-free fallback select the same lines."""

    path = tmp_path / "mixed.pdf"
    _write_mixed_pdf(path)

    vectorized = PDFExtractor(use_docling=False, visible_text_only=True).extract(path)
    monkeypatch.setattr(pdf_extractor, "_numpy", None)
    fallback = PDFExtractor(use_docling=False, visible_text_only=True).extract(path)

    assert [block.text for block in vectorized.blocks] == [
        block.text for block in fallback.blocks
    ]
    assert (
        vectorized.metadata["hidden_text_line_count"]
        == fallback.metadata["hidden_text_line_count"]
    )
