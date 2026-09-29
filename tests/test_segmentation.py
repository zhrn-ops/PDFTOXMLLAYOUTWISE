"""Tests for the boundary-based article segmentation model."""

from pdf_to_jats.models.block import TextBlock
from pdf_to_jats.models.segmentation import (
    SOURCE_AUTO,
    SOURCE_MANUAL,
    ArticleBoundary,
    Segmentation,
    common_column,
    legacy_segment_boundaries,
    reading_stream,
)


def _block(
    block_id: str,
    text: str = "text",
    page: int = 1,
    column: int | None = None,
    order: int | None = None,
    y: float = 0.0,
    x: float = 0.0,
) -> TextBlock:
    metadata: dict = {}
    if column is not None:
        metadata["column"] = column
    if order is not None:
        metadata["reading_order"] = order
    return TextBlock(
        id=block_id,
        page=page,
        text=text,
        bbox=[x, y, x + 270.0, y + 20.0],
        x=x,
        y=y,
        width=270.0,
        height=20.0,
        metadata=metadata,
    )


def _segment(
    index: int = 1,
    number: str = "S100",
    start_page: int = 1,
    end_page: int = 1,
    columns: list[int] | None = None,
    start_order: int | None = None,
    end_order: int | None = None,
    marker_block_id: str = "",
) -> dict:
    segment = {
        "index": index,
        "abstract_number": number,
        "start_page": start_page,
        "end_page": end_page,
        "columns": columns,
        "abstract_number_block_id": marker_block_id,
    }
    if start_order is not None:
        segment["start_order"] = start_order
    if end_order is not None:
        segment["end_order"] = end_order
    return segment


def _stacked_document() -> tuple[list[TextBlock], list[dict]]:
    """One page holding three articles, two of them stacked in one column.

    This is the shape the real conference PDF has: ``P-605`` and ``P-606`` share
    column 0, and ``P-607`` sits in column 1, so only a reading-order cut can
    tell them apart.
    """

    blocks = [
        _block("b0", "P-605", column=0, order=0, y=10),
        _block("b1", "First title", column=0, order=1, y=30),
        _block("b2", "First body", column=0, order=2, y=50),
        _block("b3", "First body", column=0, order=3, y=70),
        _block("b4", "P-606", column=0, order=4, y=90),
        _block("b5", "Second title", column=0, order=5, y=110),
        _block("b6", "P-607", column=1, order=6, y=10, x=300),
        _block("b7", "Third title", column=1, order=7, y=30, x=300),
        _block("b8", "Third body", column=1, order=8, y=50, x=300),
    ]
    segments = [
        _segment(1, "P-605", end_order=3),
        _segment(2, "P-606", start_order=4, end_order=5),
        _segment(3, "P-607", start_order=6),
    ]
    return blocks, segments


def test_reading_stream_follows_recorded_reading_order():
    blocks = [
        _block("c", order=2),
        _block("a", order=0),
        _block("b", order=1),
    ]
    assert [block.id for block in reading_stream(blocks)] == ["a", "b", "c"]


def test_reading_stream_falls_back_to_page_and_column():
    blocks = [
        _block("right", page=1, column=1, y=10),
        _block("second_page", page=2, column=0, y=99),
        _block("left", page=1, column=0, y=50),
    ]
    assert [block.id for block in reading_stream(blocks)] == ["left", "right", "second_page"]


def test_empty_document_has_no_articles():
    segmentation = Segmentation(())
    assert not segmentation
    assert len(segmentation) == 0
    assert segmentation.validate([]) == []


def test_first_block_always_starts_the_first_article():
    segmentation = Segmentation(["b0", "b1", "b2"], [ArticleBoundary("b1")])
    assert segmentation.first_block_id == "b0"
    assert [boundary.block_id for boundary in segmentation.boundaries] == ["b0", "b1"]
    assert segmentation.block_ids(0) == ("b0",)


def test_from_legacy_segments_without_detection_is_one_article():
    blocks, _segments = _stacked_document()
    segmentation = Segmentation.from_legacy_segments(blocks, [], number="ABSN")
    assert len(segmentation) == 1
    assert segmentation.block_ids(0) == tuple(block.id for block in blocks)
    assert segmentation.boundaries[0].number == "ABSN"
    assert segmentation.boundaries[0].source == SOURCE_AUTO
    assert any("one article" in issue for issue in segmentation.issues)
    assert segmentation.validate(blocks) == []


def test_from_legacy_segments_partitions_a_two_page_document():
    blocks = [
        _block("a0", page=1),
        _block("a1", page=1),
        _block("b0", page=2),
        _block("b1", page=2),
    ]
    segments = [
        _segment(1, "S100", start_page=1, end_page=1),
        _segment(2, "S200", start_page=2, end_page=2),
    ]
    segmentation = Segmentation.from_legacy_segments(blocks, segments)
    assert [boundary.block_id for boundary in segmentation.boundaries] == ["a0", "b0"]
    assert segmentation.article_numbers == ("S100", "S200")
    assert segmentation.block_ids(1) == ("b0", "b1")
    assert segmentation.issues == []
    assert segmentation.validate(blocks) == []


def test_from_legacy_segments_splits_articles_stacked_in_one_column():
    """The reading-order cut is the only thing separating ``P-605``/``P-606``."""

    blocks, segments = _stacked_document()
    segmentation = Segmentation.from_legacy_segments(blocks, segments)

    assert [boundary.block_id for boundary in segmentation.boundaries] == ["b0", "b4", "b6"]
    assert segmentation.article_numbers == ("P-605", "P-606", "P-607")
    assert [len(segmentation.block_ids(index)) for index in range(3)] == [4, 2, 3]
    assert segmentation.article_position("b5") == 1
    assert segmentation.article_position("b8") == 2
    assert segmentation.issues == []
    assert segmentation.validate(blocks) == []


def test_auto_and_manual_boundaries_keep_their_source():
    blocks = [_block(f"b{index}", order=index) for index in range(4)]
    segments = [
        _segment(1, "S100", columns=None),
        _segment(2, "P-606", columns=None, start_order=2),
    ]
    segmentation = Segmentation.from_legacy_segments(blocks, segments)
    assert [boundary.source for boundary in segmentation.boundaries] == [
        SOURCE_AUTO,
        SOURCE_MANUAL,
    ]


def test_block_claimed_by_two_segments_goes_to_the_latest_start():
    blocks = [_block(f"b{index}", order=index, page=1) for index in range(4)]
    segments = [
        _segment(1, "S100", columns=None),
        _segment(2, "S200", columns=None, start_order=2),
    ]
    segmentation = Segmentation.from_legacy_segments(blocks, segments)
    assert [boundary.block_id for boundary in segmentation.boundaries] == ["b0", "b2"]
    assert segmentation.article_numbers == ("S100", "S200")
    assert any("more than one detected article" in issue for issue in segmentation.issues)
    assert segmentation.validate(blocks) == []


def test_block_claimed_by_no_segment_stays_with_the_article_it_follows():
    blocks = [
        _block("a0", page=1, order=0),
        _block("a1", page=1, order=1),
        _block("b0", page=2, order=2),
        _block("orphan", page=4, order=3),
    ]
    segments = [
        _segment(1, "S100", start_page=1, end_page=1),
        _segment(2, "S200", start_page=2, end_page=3),
    ]
    segmentation = Segmentation.from_legacy_segments(blocks, segments)
    assert [boundary.block_id for boundary in segmentation.boundaries] == ["a0", "b0"]
    assert segmentation.block_ids(1) == ("b0", "orphan")
    assert any("stayed with the article they follow" in issue for issue in segmentation.issues)
    assert segmentation.validate(blocks) == []


def test_segment_matching_no_block_is_dropped_with_an_issue():
    blocks = [_block("a0", page=1, order=0), _block("b0", page=2, order=1)]
    segments = [
        _segment(1, "S100", start_page=1, end_page=1),
        _segment(2, "S200", start_page=2, end_page=2),
        _segment(3, "S300", start_page=9, end_page=9),
    ]
    segmentation = Segmentation.from_legacy_segments(blocks, segments)
    assert segmentation.article_numbers == ("S100", "S200")
    assert any("S300 does not match any block" in issue for issue in segmentation.issues)
    assert segmentation.validate(blocks) == []


def test_segment_that_covers_two_ranges_becomes_two_articles():
    """A page-wide article interrupted by another one cannot stay contiguous."""

    blocks = [
        _block("a0", page=1, column=0, order=0),
        _block("b0", page=1, column=1, order=1, x=300),
        _block("a1", page=2, column=0, order=2),
        _block("a2", page=2, column=1, order=3, x=300),
    ]
    segments = [
        _segment(1, "S100", start_page=1, end_page=2),
        _segment(2, "S200", start_page=1, end_page=1, columns=[1]),
    ]
    segmentation = Segmentation.from_legacy_segments(blocks, segments)
    assert [boundary.block_id for boundary in segmentation.boundaries] == ["a0", "b0", "a1"]
    assert [len(segmentation.block_ids(index)) for index in range(3)] == [1, 1, 2]
    assert any("covers more than one reading range" in issue for issue in segmentation.issues)
    assert segmentation.validate(blocks) == []


def test_duplicate_and_unknown_boundaries_are_cleaned_up():
    segmentation = Segmentation(
        ["b0", "b1", "b2"],
        [ArticleBoundary("b1"), ArticleBoundary("b1"), ArticleBoundary("ghost")],
    )
    assert [boundary.block_id for boundary in segmentation.boundaries] == ["b0", "b1"]
    assert any("more than one article" in issue for issue in segmentation.issues)
    assert any("ghost is not in the reading stream" in issue for issue in segmentation.issues)


def test_repeated_stream_entry_keeps_the_first_position():
    segmentation = Segmentation(["b0", "b1", "b0"])
    assert segmentation.stream == ("b0", "b1")
    assert any("appears more than once" in issue for issue in segmentation.issues)


def test_insert_boundary_refuses_the_first_block_and_existing_boundaries():
    segmentation = Segmentation(["b0", "b1", "b2"])
    assert not segmentation.insert_boundary("b0")
    assert segmentation.insert_boundary("b1")
    assert not segmentation.insert_boundary("b1")
    assert not segmentation.insert_boundary("ghost")


def test_insert_and_remove_boundary_change_the_partition():
    segmentation = Segmentation(["b0", "b1", "b2", "b3"])
    assert segmentation.insert_boundary("b2", number="P-606") is True
    assert [segmentation.block_ids(index) for index in range(2)] == [("b0", "b1"), ("b2", "b3")]
    assert segmentation.number_of("b3") == "P-606"
    assert segmentation.is_manual("b3")
    assert segmentation.remove_boundary("b2") is True
    assert len(segmentation) == 1
    assert segmentation.block_ids(0) == ("b0", "b1", "b2", "b3")
    assert not segmentation.remove_boundary("b0")
    assert not segmentation.remove_boundary("b2")


def test_set_number_names_the_article_containing_a_block():
    segmentation = Segmentation(["b0", "b1", "b2"], [ArticleBoundary("b0"), ArticleBoundary("b2")])
    assert segmentation.set_number("b1", "P-605")
    assert segmentation.number_of("b1") == "P-605"
    assert segmentation.number_of("b2") == ""
    assert not segmentation.set_number("ghost", "P-999")


def test_to_legacy_segments_round_trips_the_partition():
    blocks, segments = _stacked_document()
    segmentation = Segmentation.from_legacy_segments(blocks, segments)
    rendered = segmentation.to_legacy_segments(blocks)
    again = Segmentation.from_legacy_segments(blocks, rendered)
    assert [boundary.block_id for boundary in again.boundaries] == ["b0", "b4", "b6"]
    assert again.article_numbers == segmentation.article_numbers
    assert again.validate(blocks) == []
    assert rendered[1]["start_order"] == 4
    assert rendered[1]["end_order"] == 5
    assert rendered[2]["boundary_source"] == SOURCE_MANUAL


def test_a_rendered_origin_survives_the_round_trip():
    """The origin written by to_legacy_segments is read back by from_legacy_segments.

    Without this, a document whose articles were detected and then edited would
    report every article as hand-made, because rendering a partition writes the
    reading-order bounds the legacy vocabulary calls a manual split.
    """

    blocks = [_block("b0", order=0), _block("b1", order=1), _block("b2", order=2)]
    segments = [
        _segment(1, "S100", end_page=1),
        _segment(2, "S200", start_order=2, end_order=2),
    ]
    segmentation = Segmentation.from_legacy_segments(blocks, segments)
    assert [boundary.source for boundary in segmentation.boundaries] == [
        SOURCE_AUTO,
        SOURCE_MANUAL,
    ]

    again = Segmentation.from_legacy_segments(
        blocks, segmentation.to_legacy_segments(blocks)
    )

    assert [boundary.block_id for boundary in again.boundaries] == ["b0", "b2"]
    assert [boundary.source for boundary in again.boundaries] == [
        SOURCE_AUTO,
        SOURCE_MANUAL,
    ]


def test_geometric_segments_render_as_manual_bounds_but_keep_the_partition():
    blocks = [_block("a0", page=1, order=0), _block("b0", page=2, order=1)]
    segments = [
        _segment(1, "S100", start_page=1, end_page=1),
        _segment(2, "S200", start_page=2, end_page=2),
    ]
    segmentation = Segmentation.from_legacy_segments(blocks, segments)
    rendered = segmentation.to_legacy_segments(blocks)
    again = Segmentation.from_legacy_segments(blocks, rendered)
    assert [boundary.block_id for boundary in again.boundaries] == ["a0", "b0"]
    # Reading-order bounds are what binds the rendering, and the legacy
    # vocabulary calls those a manual split.
    assert [segment.get("start_order") for segment in rendered] == [0, 1]
    # Rendering still writes the origin, so the second trip reads these back as
    # detected rather than as articles somebody drew by hand.
    assert [boundary.source for boundary in again.boundaries] == [SOURCE_AUTO, SOURCE_AUTO]


def test_to_dict_and_from_dict_round_trip():
    blocks, segments = _stacked_document()
    segmentation = Segmentation.from_legacy_segments(blocks, segments)
    restored = Segmentation.from_dict(segmentation.to_dict())
    assert restored is not None
    assert restored.stream == segmentation.stream
    assert restored.article_numbers == segmentation.article_numbers
    assert [boundary.block_id for boundary in restored.boundaries] == ["b0", "b4", "b6"]
    assert Segmentation.from_dict({"boundaries": []}) is None
    assert Segmentation.from_dict("nonsense") is None


def test_validate_reports_where_the_stream_and_the_document_disagree():
    blocks = [_block("b0", order=0), _block("b1", order=1), _block("b2", order=2)]
    segmentation = Segmentation(["b0", "b1", "ghost"])
    problems = segmentation.validate(blocks)
    assert any("b2 is not in the reading stream" in problem for problem in problems)
    assert any("ghost is not a block of this document" in problem for problem in problems)


def test_summaries_describe_pages_columns_and_slices():
    blocks, segments = _stacked_document()
    segmentation = Segmentation.from_legacy_segments(blocks, segments)
    summaries = segmentation.summaries(blocks)
    assert [summary.index for summary in summaries] == [1, 2, 3]
    assert [summary.number for summary in summaries] == ["P-605", "P-606", "P-607"]
    assert [summary.start_page for summary in summaries] == [1, 1, 1]
    assert all(summary.block_ids[0] == summary.first_block_id for summary in summaries)
    assert [summary.columns for summary in summaries] == [(0,), (0,), (1,)]
    assert summaries[0].block_ids == ("b0", "b1", "b2", "b3")


def test_common_column_is_none_for_pages_spanning_several_columns():
    assert common_column([_block("a", column=0), _block("b", column=0)]) == (0,)
    assert common_column([_block("a", column=0), _block("b", column=1)]) is None
    assert common_column([_block("a")]) is None


def test_legacy_segment_boundaries_name_the_block_that_starts_each_article():
    blocks, segments = _stacked_document()
    assert legacy_segment_boundaries(blocks, segments) == ["b0", "b4", "b6"]
    assert legacy_segment_boundaries(blocks, []) == []


def test_legacy_segment_boundaries_skip_an_indistinguishable_segment():
    """Two segments claiming the same first block are one article, not two."""

    blocks = [_block(f"b{index}", order=index) for index in range(3)]
    segments = [
        _segment(1, "S100", columns=None),
        _segment(2, "S200", columns=None),
    ]
    assert legacy_segment_boundaries(blocks, segments) == ["b0", None]
    assert len(Segmentation.from_legacy_segments(blocks, segments)) == 1


def test_block_ids_of_returns_the_blocks_of_the_containing_article():
    blocks, segments = _stacked_document()
    segmentation = Segmentation.from_legacy_segments(blocks, segments)
    assert segmentation.block_ids_of("b2") == ("b0", "b1", "b2", "b3")
    assert segmentation.block_ids_of("b5") == ("b4", "b5")
    assert segmentation.block_ids_of("ghost") == ()
    assert segmentation.article_position("b6") == 2
