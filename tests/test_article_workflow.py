"""Tests for the per-article finish/lock workflow."""

from pdf_to_jats.core.pdf_extractor import PDFExtractor
from pdf_to_jats.models.block import TextBlock
from pdf_to_jats.models.document import block_matches_segment


def _block(
    text: str,
    block_id: str = "block_001",
    page: int = 1,
    column: int | None = None,
    metadata: dict | None = None,
) -> TextBlock:
    base_metadata: dict = {}
    if column is not None:
        base_metadata["column"] = column
    if metadata:
        base_metadata.update(metadata)
    return TextBlock(
        id=block_id,
        page=page,
        x=0,
        y=0,
        width=500,
        height=20,
        bbox=[0, 0, 500, 20],
        text=text,
        metadata=base_metadata,
    )


def _segment(
    index: int = 1,
    abstract_number: str = "S100",
    start_page: int = 1,
    end_page: int = 2,
    columns: list[int] | None = None,
) -> dict:
    return {
        "index": index,
        "abstract_number": abstract_number,
        "start_page": start_page,
        "end_page": end_page,
        "columns": columns,
    }


def test_block_matches_segment_by_page_range():
    segment = _segment(start_page=1, end_page=2, columns=None)
    assert block_matches_segment(_block("text", page=1), segment)
    assert block_matches_segment(_block("text", page=2), segment)
    assert not block_matches_segment(_block("text", page=3), segment)


def test_block_matches_segment_respects_columns():
    segment = _segment(columns=[0])
    assert block_matches_segment(_block("text", page=1, column=0), segment)
    assert not block_matches_segment(_block("text", page=1, column=1), segment)


def test_full_width_block_matches_any_column():
    segment = _segment(columns=[1])
    assert block_matches_segment(_block("text", page=1, column=None), segment)


def test_block_matches_segment_rejects_none():
    assert not block_matches_segment(_block("text"), None)


def test_extractor_infers_segment_columns_from_markers():
    extractor = PDFExtractor(use_docling=False)
    left_marker = _block("(S100) Some title", block_id="block_001", column=0)
    right_marker = _block("(S200) Other title", block_id="block_002", column=1)
    markers = extractor._extract_abstract_numbers([left_marker, right_marker])
    segments = extractor._build_article_segments(markers, page_count=1)

    assert len(segments) == 2
    assert segments[0]["columns"] == [0]
    assert segments[1]["columns"] == [1]


def test_extractor_without_column_metadata_uses_page_ranges_only():
    extractor = PDFExtractor(use_docling=False)
    markers = extractor._extract_abstract_numbers(
        [
            _block("(S100) Some title", block_id="block_001"),
            _block("(S200) Other title", block_id="block_002", page=2),
        ]
    )
    segments = extractor._build_article_segments(markers, page_count=2)

    assert segments[0]["columns"] is None
    assert segments[1]["columns"] is None
    assert block_matches_segment(_block("text", page=1, column=None), segments[0])


def test_shared_column_falls_back_to_page_range_matching():
    extractor = PDFExtractor(use_docling=False)
    markers = extractor._extract_abstract_numbers(
        [
            _block("(S100) First", block_id="block_001", column=0),
            _block("(S200) Second", block_id="block_002", column=0),
        ]
    )
    segments = extractor._build_article_segments(markers, page_count=1)

    # Two markers claim the same column, so no column can be attributed.
    assert segments[0]["columns"] is None
    assert segments[1]["columns"] is None
