"""Tests for the per-article finish/lock workflow."""

from types import SimpleNamespace

from pdf_to_jats.core.pdf_extractor import PDFExtractor
from pdf_to_jats.core.author_linker import AuthorLinker
from pdf_to_jats.core.jats_generator import JATSGenerator
from pdf_to_jats.core.paragraph_reconstructor import ParagraphReconstructor
from pdf_to_jats.gui.main_window import MainWindow
from pdf_to_jats.models.block import TextBlock, reading_order_key
from pdf_to_jats.models.document import (
    Document,
    block_matches_segment,
    segment_is_manual,
)
from pdf_to_jats.models.paragraph import Paragraph


def _block(
    text: str,
    block_id: str = "block_001",
    page: int = 1,
    column: int | None = None,
    metadata: dict | None = None,
    x: float = 0,
    y: float = 0,
) -> TextBlock:
    base_metadata: dict = {}
    if column is not None:
        base_metadata["column"] = column
    if metadata:
        base_metadata.update(metadata)
    return TextBlock(
        id=block_id,
        page=page,
        x=x,
        y=y,
        width=270,
        height=20,
        bbox=[x, y, x + 270, y + 20],
        text=text,
        metadata=base_metadata,
    )


def _segment(
    index: int = 1,
    abstract_number: str = "S100",
    start_page: int = 1,
    end_page: int = 2,
    columns: list[int] | None = None,
    start_order: int | None = None,
    end_order: int | None = None,
) -> dict:
    segment = {
        "index": index,
        "abstract_number": abstract_number,
        "start_page": start_page,
        "end_page": end_page,
        "columns": columns,
    }
    if start_order is not None:
        segment["start_order"] = start_order
    if end_order is not None:
        segment["end_order"] = end_order
    return segment


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


def test_reading_order_bounds_split_articles_sharing_a_page_and_column():
    """Two articles stacked in one column need the reading-order cut."""

    first = _segment(start_order=None, end_order=3, columns=None)
    second = _segment(index=2, abstract_number="P-606", start_order=4, columns=None)

    first_blocks = [_block(text, block_id=f"a{order}", metadata={"reading_order": order}, column=0) for order, text in ((1, "P-605"), (2, "body"), (3, "body"), (4, "P-606"))]

    assert [block_matches_segment(block, first) for block in first_blocks] == [
        True,
        True,
        True,
        False,
    ]
    assert [block_matches_segment(block, second) for block in first_blocks] == [
        False,
        False,
        False,
        True,
    ]


def test_reading_order_bounds_still_respect_the_page_range():
    """A bound narrows the page range; it does not replace it."""

    segment = _segment(start_page=2, end_page=2, start_order=1, columns=None)

    assert not block_matches_segment(
        _block("text", page=1, metadata={"reading_order": 5}), segment
    )
    assert block_matches_segment(_block("text", page=2, metadata={"reading_order": 5}), segment)


def test_block_without_reading_order_is_kept_by_page_matching():
    """Dropping an unnumbered block would silently lose content."""

    segment = _segment(start_order=10, end_order=20, columns=None)

    assert block_matches_segment(_block("text", page=1), segment)


def test_segment_is_manual_reports_the_reading_order_cut():
    assert not segment_is_manual(_segment(columns=[0]))
    assert segment_is_manual(_segment(start_order=4))
    assert segment_is_manual(_segment(end_order=3))


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


def test_session_style_codes_wm1_1_wm1_2_produce_correct_segments():
    """Session-style abstract codes like 'WM1.1' must be detected as markers."""

    extractor = PDFExtractor(use_docling=False)
    blocks = [
        _block("WM1.1 Some Abstract Title", block_id="block_a", column=0),
        _block("WM1.2 Another Abstract Title", block_id="block_b", column=1),
    ]
    markers = extractor._extract_abstract_numbers(blocks)
    assert len(markers) == 2
    assert markers[0]["abstract_number"] == "WM1.1"
    assert markers[1]["abstract_number"] == "WM1.2"

    segments = extractor._build_article_segments(markers, page_count=1)
    assert len(segments) == 2
    assert segments[0]["columns"] == [0]
    assert segments[1]["columns"] == [1]


def test_html_preview_continuation_keeps_source_ids_for_article_export():
    """A virtual continuation must retain segment membership of its source."""

    window = MainWindow.__new__(MainWindow)
    window._html_preview_merges = [("left", "right")]
    left = _block("First half", block_id="left", column=0)
    right = _block("Second half", block_id="right", column=1)

    continued = window._blocks_with_html_preview_continuations([left, right])

    assert len(continued) == 1
    assert continued[0].metadata["merged_block_ids"] == ["left", "right"]

    # The virtual block inherits the first block's column, but it still must
    # be included when exporting the article represented by the other source.
    continued[0].role = "abstract"
    window.linker = AuthorLinker()
    window.document = None
    # Simulate the accepted paragraph that ParagraphReconstructor.accepted_from_blocks
    # would create from the virtual block: it carries the source line ids and the
    # user-assigned role.
    accepted_paragraph = Paragraph(
        id="paragraph_left",
        page=1,
        column=0,
        text="First half Second half",
        bbox=[0, 0, 270, 20],
        source_line_ids=["left", "right"],
        role="abstract",
        status="accepted",
    )
    exported = window._document_for_segment(
        _segment(columns=[1]),
        Document(
            blocks=continued,
            raw_blocks=[left, right],
            paragraphs=[accepted_paragraph],
        ),
    )
    assert exported.abstract == "First half Second half"
    # The continuation paragraph must inherit the user-assigned "abstract" role,
    # not be left as "unclassified" from the raw extraction blocks.    assert exported.paragraphs[0].role == "abstract"


def _window(document: Document) -> MainWindow:
    """Build the minimum MainWindow state the marker helpers rely on."""

    window = MainWindow.__new__(MainWindow)
    window.document = document
    window._current_segment_key = None
    window._finished_segment_keys = []
    window._deleted_segment_keys = []
    window._llm_links_by_segment = {}
    window._segment_identity_cache = None
    window._undo_stack = []
    # The articles are re-rendered into the real widgets only in a live window.
    window._refresh_article_buttons = lambda: None
    return window


def _stacked_page_document() -> Document:
    """One page holding three articles, two of which share the left column.

    This is the layout of a conference proceedings page: marker, title, and
    body per article, with the second article continuing below the first in the
    same column.
    """

    layout = [
        ("P-605", 0),
        ("GROWTH AND PUBERTAL OUTCOMES IN PEDIATRIC PATIENTS", 0),
        ("Background: urea cycle disorders are rare.", 0),
        ("P-606", 0),
        ("LONGITUDINAL ASSESSMENT OF PROTEIN TOLERANCE", 0),
        ("Mild urea cycle disorders are identified by screening.", 0),
        ("P-607", 1),
        ("GENERATING PATIENT-DERIVED STEM CELLS", 1),
    ]
    blocks = [
        _block(
            text,
            block_id=f"block_{position:03d}",
            column=column,
            metadata={"reading_order": position},
            x=28.0 if column == 0 else 306.0,
            y=float(position * 20),
        )
        for position, (text, column) in enumerate(layout, start=1)
    ]
    return Document(blocks=blocks, raw_blocks=list(blocks), metadata={"abstract_number": "ABSN"})


def test_split_article_at_block_cuts_one_page_into_three_articles():
    """A page whose articles share a column must still split into three."""

    document = _stacked_page_document()
    window = _window(document)

    # The document's own first line already starts the first article.
    assert window.split_article_at_block("block_001") is None

    assert window.split_article_at_block("block_004") is not None
    # Splitting at the same line twice would create an empty article.
    assert window.split_article_at_block("block_004") is None
    assert window.split_article_at_block("block_007") is not None

    segments = document.metadata["article_segments"]
    assert [segment["abstract_number"] for segment in segments] == ["P-605", "P-606", "P-607"]
    assert [segment["index"] for segment in segments] == [1, 2, 3]
    assert [segment_is_manual(segment) for segment in segments] == [True, True, True]
    owned = [
        [block.id for block in document.blocks if block_matches_segment(block, segment)]
        for segment in segments
    ]
    assert owned == [
        ["block_001", "block_002", "block_003"],
        ["block_004", "block_005", "block_006"],
        ["block_007", "block_008"],
    ]


def test_split_article_at_block_is_undone_by_merging_the_segments():
    """The split must survive the segment bookkeeping the UI relies on."""

    document = _stacked_page_document()
    window = _window(document)
    window.split_article_at_block("block_004")
    window.split_article_at_block("block_007")

    # Every article keeps a key of its own, including the working article.
    keys = [window._segment_key(segment) for segment in document.metadata["article_segments"]]
    assert len(set(keys)) == 3
    assert window._current_segment_key == keys[2]


def test_article_identity_is_the_block_that_starts_it():
    """An article is identified by its first block, not by its position."""

    document = _stacked_page_document()
    window = _window(document)
    window.split_article_at_block("block_004")
    window.split_article_at_block("block_007")

    segments = document.metadata["article_segments"]
    assert [window._segment_key(segment) for segment in segments] == [
        "block_001",
        "block_004",
        "block_007",
    ]
    assert window._current_segment_key == "block_007"


def test_selected_blocks_panel_shows_only_the_current_article():
    """Choosing an article scopes the Selected Blocks panel to its blocks."""

    document = _stacked_page_document()
    window = _window(document)
    window.split_article_at_block("block_004")
    window.split_article_at_block("block_007")

    # The last split leaves the third article current, and only its blocks
    # belong in the panel.
    assert window._current_article_block_ids() == frozenset({"block_007", "block_008"})

    window._current_segment_key = "block_004"
    assert window._current_article_block_ids() == frozenset(
        {"block_004", "block_005", "block_006"}
    )


def test_no_current_article_leaves_every_block_visible():
    """Before a tab is chosen nothing is hidden from the panel."""

    document = _stacked_page_document()
    window = _window(document)
    window._current_segment_key = None

    assert window._current_article_block_ids() is None


def test_a_split_before_a_finished_article_keeps_its_identity():
    """Renumbering used to change the key of every article after the cut."""

    document = _stacked_page_document()
    window = _window(document)
    window.split_article_at_block("block_004")
    window.split_article_at_block("block_007")
    window._finished_segment_keys = [
        window._segment_key(document.metadata["article_segments"][1])
    ]

    # Cutting the first article in two pushes the finished article from index 2
    # to index 3. Its identity is the block it starts at, so the finished article
    # follows the renumbering instead of being left behind.
    window.split_article_at_block("block_002")

    finished = next(
        segment
        for segment in document.metadata["article_segments"]
        if window._segment_key(segment) == "block_004"
    )
    assert finished["index"] == 3
    assert window._finished_segment_keys == ["block_004"]


def test_naming_an_article_keeps_its_identity():
    """A rename must not detach the working or finished article bookkeeping."""

    document = _stacked_page_document()
    window = _window(document)
    window.split_article_at_block("block_004")
    second = document.metadata["article_segments"][1]
    window._finished_segment_keys = [window._segment_key(second)]
    window._current_segment_key = window._segment_key(second)

    window._set_abstract_number("P-608", second)

    assert second["abstract_number"] == "P-608"
    assert window._segment_key(second) == "block_004"
    assert window._finished_segment_keys == ["block_004"]
    assert window._current_segment_key == "block_004"


def _owned_block_ids(document: Document) -> list[list[str]]:
    """The blocks each stored article claims, in reading order."""

    ordered = sorted(document.blocks, key=reading_order_key)
    return [
        [block.id for block in ordered if block_matches_segment(block, segment)]
        for segment in document.metadata["article_segments"]
    ]


def test_merging_an_article_into_the_previous_one_restores_the_partition():
    """Repairing a wrong split must leave every block with exactly one article."""

    document = _stacked_page_document()
    window = _window(document)
    window.split_article_at_block("block_004")
    window.split_article_at_block("block_007")

    assert window.merge_article_with_previous(document.metadata["article_segments"][2]) is not None

    segments = document.metadata["article_segments"]
    assert [window._segment_key(segment) for segment in segments] == ["block_001", "block_004"]
    assert _owned_block_ids(document) == [
        ["block_001", "block_002", "block_003"],
        ["block_004", "block_005", "block_006", "block_007", "block_008"],
    ]
    # The merged article keeps the number and the marker of the one it started at.
    assert segments[1]["abstract_number"] == "P-606"
    assert segments[1]["abstract_number_block_id"] == "block_004"


def test_merging_back_to_one_article_orphans_no_block():
    """Two cuts and two merges must return the document to one whole article."""

    document = _stacked_page_document()
    window = _window(document)
    window.split_article_at_block("block_004")
    window.split_article_at_block("block_007")
    window.merge_article_with_previous(document.metadata["article_segments"][2])
    window.merge_article_with_previous(document.metadata["article_segments"][1])

    segments = document.metadata["article_segments"]
    assert len(segments) == 1
    assert _owned_block_ids(document) == [
        [f"block_{position:03d}" for position in range(1, 9)]
    ]


def test_merging_stops_at_a_finished_article():
    """Locked blocks must not be swallowed by a neighbouring article."""

    document = _stacked_page_document()
    window = _window(document)
    window.split_article_at_block("block_004")
    window.split_article_at_block("block_007")
    window._finished_segment_keys = [
        window._segment_key(document.metadata["article_segments"][1])
    ]

    assert window.merge_article_with_previous(document.metadata["article_segments"][2]) is None
    assert len(document.metadata["article_segments"]) == 3


def test_the_first_article_has_nothing_to_merge_into():
    document = _stacked_page_document()
    window = _window(document)
    window.split_article_at_block("block_004")

    assert window.merge_article_with_previous(document.metadata["article_segments"][0]) is None
    assert len(document.metadata["article_segments"]) == 2


def test_a_split_can_be_undone_with_the_article_snapshot():
    """Ctrl+Z after a split has to put the articles back as they were."""

    document = _stacked_page_document()
    window = _window(document)
    window.split_article_at_block("block_004")
    window.split_article_at_block("block_007")
    before = [dict(segment) for segment in document.metadata["article_segments"]]
    window.split_article_at_block("block_002")

    window.undo_last_action()

    assert [dict(segment) for segment in document.metadata["article_segments"]] == before
    # The two earlier splits stay on the stack, so they can be undone too.
    assert len(window._undo_stack) == 2


def test_a_merge_can_be_undone_with_the_article_snapshot():
    """Undoing a merge restores the article that was folded in."""

    document = _stacked_page_document()
    window = _window(document)
    window.split_article_at_block("block_004")
    window.split_article_at_block("block_007")
    before = [dict(segment) for segment in document.metadata["article_segments"]]

    window.merge_article_with_previous(document.metadata["article_segments"][2])
    assert len(document.metadata["article_segments"]) == 2

    window.undo_last_action()

    assert [dict(segment) for segment in document.metadata["article_segments"]] == before
    assert window._current_segment_key == "block_007"


def test_delete_article_hides_it_from_export_but_keeps_its_blocks():
    """Deleting is an export decision, never a loss of content."""

    document = _stacked_page_document()
    window = _window(document)
    window.split_article_at_block("block_004")
    window.split_article_at_block("block_007")
    deleted_segment = document.metadata["article_segments"][1]

    assert window.delete_article(deleted_segment) is True
    assert window._is_segment_deleted(deleted_segment) is True
    assert window._exportable_segments(document.metadata["article_segments"])[0]["abstract_number"] == "P-605"
    assert [segment["abstract_number"] for segment in window._exportable_segments(
        document.metadata["article_segments"]
    )] == ["P-605", "P-607"]
    # The partition is untouched: no boundary moved, no block lost its owner.
    assert len(document.metadata["article_segments"]) == 3


def test_restore_article_puts_it_back_into_the_export():
    document = _stacked_page_document()
    window = _window(document)
    window.split_article_at_block("block_004")
    window.split_article_at_block("block_007")
    deleted_segment = document.metadata["article_segments"][1]
    window.delete_article(deleted_segment)

    assert window.restore_article(deleted_segment) is True
    assert window._is_segment_deleted(deleted_segment) is False
    assert len(window._exportable_segments(document.metadata["article_segments"])) == 3


def test_a_delete_can_be_undone_with_the_article_snapshot():
    """Ctrl+Z after a delete brings the article back like a split or merge."""

    document = _stacked_page_document()
    window = _window(document)
    window.split_article_at_block("block_004")
    deleted_segment = document.metadata["article_segments"][1]
    window.delete_article(deleted_segment)
    assert window._deleted_segment_keys == ["block_004"]

    window.undo_last_action()

    assert window._deleted_segment_keys == []


def test_delete_article_refuses_a_finished_article():
    document = _stacked_page_document()
    window = _window(document)
    window.split_article_at_block("block_004")
    window.split_article_at_block("block_007")
    target = document.metadata["article_segments"][0]
    key = window._segment_key(target)
    window._finished_segment_keys.append(key)

    assert window.delete_article(target) is False
    assert window._is_segment_deleted(target) is False


def test_finish_skips_deleted_articles_and_current_falls_to_a_live_one():
    """A deleted article must not become the working article or be finished."""

    document = _stacked_page_document()
    window = _window(document)
    window.split_article_at_block("block_004")
    window.split_article_at_block("block_007")
    window.delete_article(document.metadata["article_segments"][0])
    # Clear the working article so _current_segment has to fall back.
    window._current_segment_key = None

    # The deleted first article is skipped as the fallback working article.
    current = window._current_segment()
    assert current is not None
    assert current[1] == "block_004"
    assert current[0]["abstract_number"] == "P-606"


def test_the_deleted_article_view_covers_its_blocks_only():
    """The page overlay and preview must recede exactly the deleted article."""

    document = _stacked_page_document()
    window = _window(document)
    window.split_article_at_block("block_004")
    window.split_article_at_block("block_007")
    window.delete_article(document.metadata["article_segments"][1])

    assert window._deleted_view_block_ids == frozenset({"block_004", "block_005", "block_006"})

    window.restore_article(document.metadata["article_segments"][1])
    assert window._deleted_view_block_ids == frozenset()


def test_tagging_marker_block_sets_abstract_number_and_exclusion():
    marker = _block("Abstract No: 4349", block_id="m1")
    marker.role = "abstract_number"
    window = _window(Document(blocks=[marker], raw_blocks=[marker], metadata={}))

    window._apply_abstract_number_role(marker)

    assert window.document.abstract_number() == "4349"
    assert window.document.abstract_number_block_ids() == {"m1"}


def test_untagging_marker_drops_the_exclusion():
    marker = _block("Abstract No: 4349", block_id="m1")
    window = _window(
        Document(
            blocks=[marker],
            raw_blocks=[marker],
            metadata={"abstract_number_block_ids": ["m1"]},
        )
    )

    window._clear_abstract_number_role("m1")

    assert window.document.abstract_number_block_ids() == set()


def test_update_document_model_excludes_role_tagged_marker():
    marker = _block("Abstract No: 4349", block_id="m1")
    marker.role = "abstract_number"
    body = _block("We studied peptides in depth.", block_id="b1")
    body.role = "abstract"
    window = _window(Document(blocks=[marker, body], raw_blocks=[marker, body], metadata={}))
    window.linker = AuthorLinker()
    window._refresh_link_review = lambda: None
    window._rebuild_paragraph_model = lambda: None

    window._update_document_model()

    # The marker is remembered as excluded and never reaches the abstract.
    assert window.document.abstract_number_block_ids() == {"m1"}
    assert window.document.abstract == "We studied peptides in depth."


def test_exported_articles_are_named_after_their_abstract_number():
    """A folder of exports should be matchable to the articles it holds."""

    named = Document(blocks=[], raw_blocks=[], metadata={"abstract_number": "P-605"})
    unnamed = Document(blocks=[], raw_blocks=[], metadata={"abstract_number": "ABSN"})

    assert MainWindow._article_filename(named, 1, 3) == "article_P-605.xml"
    assert MainWindow._article_filename(unnamed, 2, 3) == "article_002.xml"
    assert MainWindow._article_filename(unnamed, 1, 1) == "article.xml"


def test_article_tab_title_drops_the_number_the_user_entered():
    """The entered article number is an id, not part of the title a tab shows."""

    title = _block(
        "P-605 GROWTH AND PUBERTAL OUTCOMES",
        block_id="block_001",
        metadata={"reading_order": 1},
    )
    title.role = "title"
    document = Document(
        blocks=[title], raw_blocks=[title], metadata={"abstract_number": "ABSN"}
    )
    window = _window(document)
    segment = {
        "index": 1,
        "abstract_number": "P-605",
        "start_page": 1,
        "end_page": 1,
    }
    document.metadata["article_segments"] = [segment]

    summary = window._segment_summary(segment)

    # The label still names the article, but the previewed title does not.
    assert "ABSN P-605" in summary
    snippet = summary.rsplit("|", 1)[-1]
    assert "P-605" not in snippet
    assert snippet.strip() == "GROWTH AND PUBERTAL OUTCOMES"


def test_article_tab_title_drops_a_parenthesised_marker():
    """A "(S100)" prefix is a conference marker, not the start of the title."""

    title = _block(
        "(S100) A Study Of Peptides", block_id="block_001", metadata={"reading_order": 1}
    )
    title.role = "title"
    document = Document(
        blocks=[title], raw_blocks=[title], metadata={"abstract_number": "ABSN"}
    )
    window = _window(document)
    segment = {"index": 1, "abstract_number": "ABSN", "start_page": 1, "end_page": 1}
    document.metadata["article_segments"] = [segment]

    summary = window._segment_summary(segment)

    assert "(S100)" not in summary
    assert summary.endswith("A Study Of Peptides")


def test_marker_lines_are_dropped_from_the_paragraph_model():
    marker = _block("Abstract No: 4349", block_id="m1")
    marker.role = "abstract_number"
    body = _block("We studied peptides in depth.", block_id="b1")
    document = Document(
        blocks=[marker, body],
        raw_blocks=[marker, body],
        metadata={"abstract_number_block_ids": ["m1"]},
    )
    window = _window(document)
    window.paragraph_reconstructor = ParagraphReconstructor()

    window._rebuild_paragraph_model()

    assert all("4349" not in paragraph.text for paragraph in document.paragraphs)


def test_sync_from_roles_adopts_the_number_extraction_missed():
    # The extractor only matches "(S100)" when more text follows it, so a
    # marker-only block needs the tagged role to supply the identifier.
    marker = _block("(S100)", block_id="m1")
    marker.role = "abstract_number"
    window = _window(
        Document(blocks=[marker], raw_blocks=[marker], metadata={"abstract_number": "ABSN"})
    )

    adopted = window._sync_abstract_number_from_roles()

    assert adopted == "S100"
    assert window.document.abstract_number() == "S100"


def test_abstract_role_blocks_reach_the_generated_xml(tmp_path):
    """Blocks tagged abstract must come out as <abstract> in the export."""

    from lxml import etree

    title = _block("PHASE 3, RANDOMIZED STUDY", block_id="t1")
    title.role = "title"
    body = _block("We treated patients at several dose levels.", block_id="s1")
    body.role = "abstract"
    document = Document(blocks=[title, body], raw_blocks=[title, body], metadata={})
    window = _window(document)
    window.linker = AuthorLinker()
    window.paragraph_reconstructor = ParagraphReconstructor()
    window._html_preview_merges = []
    window._refresh_link_review = lambda: None

    window._update_document_model()
    export_document = window._document_with_html_preview_continuations()
    output = tmp_path / "article.xml"
    JATSGenerator().generate(export_document, output)

    root = etree.parse(str(output)).getroot()
    para = root.find(
        f".//{{{JATSGenerator.ANI_NS}}}abstracts/"
        f"{{{JATSGenerator.ANI_NS}}}abstract/{{{JATSGenerator.CE_NS}}}para"
    )
    assert para is not None
    assert para.text == "We treated patients at several dose levels."


def _finishable_window(document: Document, output) -> MainWindow:
    """A window whose XML export is stubbed so the finish flow runs headless."""

    window = _window(document)
    window.config = SimpleNamespace(generated_xml_dir=output, output_dir=output)
    window.linker = AuthorLinker()
    window.viewer = None
    window._export_article_xml = lambda segment: (output / "article.xml", [])
    window._refresh_after_article_change = lambda: None
    window._warn_locked_blocks = lambda blocks: None
    window._startup_flow = True
    return window


def _headless_window(document: Document) -> MainWindow:
    """A window with the Qt panes stubbed out for the role workflow."""

    window = _window(document)
    window._selected_zone_id = None
    window._selected_zone_ids = set()
    window._update_document_model = lambda: None
    window._populate_tree = lambda: None
    window._refresh_article_buttons = lambda: None
    window._sync_tree_selection = lambda: None
    window._update_selection_status = lambda: None
    window._update_selection_panel = lambda: None
    window._select_block_by_id = lambda block_id, preserve_selection=False: None
    window.viewer = SimpleNamespace(
        set_blocks=lambda *args, **kwargs: None,
        set_zones=lambda *args, **kwargs: None,
        set_selected_blocks=lambda *args, **kwargs: None,
    )
    return window


def test_finishing_a_selection_exports_only_its_blocks(tmp_path):
    """Selection is the article: the export holds exactly the selected lines."""

    document = _stacked_page_document()
    window = _finishable_window(document, tmp_path)
    window._selected_block_ids = {"block_004", "block_005", "block_006"}

    window.finish_selected_article()

    segments = document.metadata["article_segments"]
    assert [window._segment_key(segment) for segment in segments] == [
        "block_001",
        "block_004",
        "block_007",
    ]
    exported = window._document_for_segment(window._segment_for_key("block_004"))
    assert [block.id for block in exported.blocks] == [
        "block_004",
        "block_005",
        "block_006",
    ]
    assert window._finished_segment_keys == ["block_004"]


def test_selection_export_document_uses_only_selected_block_ids():
    """An export-without-number scopes XML to the selected article blocks."""

    document = _stacked_page_document()
    window = _window(document)
    window.linker = AuthorLinker()
    segment = {
        "abstract_number": "ABSN",
        "start_page": 1,
        "end_page": 1,
        "block_ids": ["block_004", "block_005"],
    }

    exported = window._document_for_segment(segment)

    assert [block.id for block in exported.blocks] == ["block_004", "block_005"]
    assert exported.metadata["abstract_number"] == "ABSN"


def test_placeholder_absn_can_start_an_article_from_a_selected_block():
    """The explicit no-number action must create a normal working article."""

    document = _stacked_page_document()
    window = _window(document)
    window._startup_flow = True
    window._explicit_number_workflow = True
    window._selected_block_ids = {"block_001"}
    window._refresh_selected_blocks_view = lambda: None

    assert window._start_article_from_number("ABSN", allow_placeholder=True)

    segment = document.metadata["article_segments"][0]
    assert segment["abstract_number"] == "ABSN"
    assert segment["block_ids"] == ["block_001"]


def test_article_export_keeps_a_merged_block_owned_by_its_merged_id():
    """Role assignment after a merge remains visible to that article's XML."""

    document = _stacked_page_document()
    merged = _block(
        "FIRST ARTICLE TITLE Jane Doe",
        block_id="merged_001_149",
        metadata={"reading_order": 1, "merged_block_ids": ["block_001", "block_002"]},
    )
    merged.role = "title"
    document.blocks = [merged, *document.blocks[2:]]
    window = _window(document)
    window.linker = AuthorLinker()
    segment = {
        "abstract_number": "5583",
        "start_page": 1,
        "end_page": 1,
        "block_ids": ["merged_001_149"],
    }

    exported = window._document_for_segment(segment)

    assert [block.id for block in exported.blocks] == ["merged_001_149"]
    assert {block.id for block in exported.raw_blocks} == {"block_001", "block_002"}


def test_exported_paragraph_drops_lines_outside_the_article(tmp_path):
    """A paragraph reconstructed across the PDF must be trimmed to the article.

    The exported view narrows a paragraph's source lines to the article's
    blocks; its text has to be rebuilt from those same lines, or a continuation
    that spans the boundary spells out the next article's words in the XML.
    """

    document = _stacked_page_document()
    window = _window(document)
    window.linker = AuthorLinker()
    window.paragraph_reconstructor = ParagraphReconstructor()
    # A continuation that wrongly stretches across the article boundary.
    window._html_preview_merges = [("block_003", "block_004")]
    window._update_document_model = lambda: None
    window.split_article_at_block("block_004")

    segment = window._segment_for_key("block_001")
    exported = window._document_for_segment(
        segment, window._document_with_html_preview_continuations()
    )

    text = " ".join(paragraph.text for paragraph in exported.paragraphs)
    assert "Background: urea cycle disorders are rare." in text
    # block_004 starts the next article, so none of its text may leak in.
    assert "P-606" not in text
    assert "LONGITUDINAL ASSESSMENT OF PROTEIN TOLERANCE" not in text


def test_finishing_a_selection_starts_the_next_article_after_it(tmp_path):
    """The working article moves to the first line that was not selected."""

    document = _stacked_page_document()
    window = _finishable_window(document, tmp_path)
    window._selected_block_ids = {"block_001", "block_002", "block_003"}

    window.finish_selected_article()

    segments = document.metadata["article_segments"]
    assert [window._segment_key(segment) for segment in segments] == ["block_001", "block_004"]
    assert window._current_segment_key == "block_004"


def test_finishing_a_selection_locks_exactly_those_blocks(tmp_path):
    """Only the exported lines are locked; neighbours stay editable."""

    document = _stacked_page_document()
    window = _finishable_window(document, tmp_path)
    window._selected_block_ids = {"block_004", "block_005"}

    window.finish_selected_article()

    by_id = {block.id: block for block in document.blocks}
    assert by_id["block_004"].metadata["article_locked"] is True
    assert by_id["block_005"].metadata["article_locked"] is True
    assert "article_locked" not in by_id["block_006"].metadata


def test_a_non_contiguous_selection_is_refused(tmp_path):
    """An article is one slice of the reading order, so a gap is not one."""

    document = _stacked_page_document()
    window = _finishable_window(document, tmp_path)
    window._selected_block_ids = {"block_004", "block_006"}

    window.finish_selected_article()

    assert window._finished_segment_keys == []
    assert document.metadata.get("article_segments", []) == []


def test_a_refused_finish_leaves_the_articles_unsplit(tmp_path):
    """A finish that fails validation must not leave extra splits behind."""

    document = _stacked_page_document()
    window = _finishable_window(document, tmp_path)
    window._export_article_xml = lambda segment: (None, [SimpleNamespace(message="no title")])
    window._selected_block_ids = {"block_004", "block_005"}

    window.finish_selected_article()

    assert document.metadata.get("article_segments", []) == []
    assert window._finished_segment_keys == []
    assert window._undo_stack == []


def test_finishing_a_selection_refuses_locked_blocks(tmp_path):
    """A finished article must be reopened before it can be finished again."""

    document = _stacked_page_document()
    window = _finishable_window(document, tmp_path)
    document.blocks[3].metadata["article_locked"] = True  # block_004
    window._selected_block_ids = {"block_004", "block_005"}

    window.finish_selected_article()

    assert window._finished_segment_keys == []


def _detected_document() -> Document:
    """A two-article PDF whose boundaries were only *detected*, not confirmed."""

    extractor = PDFExtractor(use_docling=False)
    blocks = [
        _block("(S100) First paper", block_id="block_001", column=0, metadata={"reading_order": 1}),
        _block("First body.", block_id="block_002", column=0, metadata={"reading_order": 2}),
        _block("(S200) Second paper", block_id="block_003", column=1, metadata={"reading_order": 3}),
        _block("Second body.", block_id="block_004", column=1, metadata={"reading_order": 4}),
    ]
    markers = extractor._extract_abstract_numbers(blocks)
    segments = extractor._build_article_segments(markers, page_count=1)
    return Document(blocks=blocks, raw_blocks=list(blocks), metadata={"article_segments": segments})


def test_detected_articles_need_confirmation_before_export():
    """Detection is a suggestion: unconfirmed articles must not be exported."""

    document = _detected_document()
    window = _window(document)
    segments = document.metadata["article_segments"]

    assert len(segments) == 2
    assert window._exportable_segments(segments) == []


def test_explicit_number_workflow_exports_only_finished_articles():
    """Started articles stay out of a later batch export until they are finished."""

    document = _stacked_page_document()
    segment = _segment(index=1, abstract_number="P-605", start_page=1, end_page=1)
    segment["article_key"] = "block_001"
    document.metadata["article_segments"] = [segment]
    window = _window(document)
    window._startup_flow = True
    window._explicit_number_workflow = True

    assert window._exportable_segments([segment]) == []

    window._finished_segment_keys = ["block_001"]
    assert window._exportable_segments([segment]) == [segment]


def test_finishing_a_detected_article_confirms_it_for_export():
    """Finishing an article vouches for the boundary detection suggested."""

    document = _detected_document()
    window = _window(document)
    segments = document.metadata["article_segments"]
    window._finished_segment_keys = [window._segment_key(segments[0])]

    exportable = window._exportable_segments(segments)
    assert [segment["abstract_number"] for segment in exportable] == ["S100"]


def test_a_hand_made_cut_confirms_both_neighbours():
    """Where an article starts also settles where the previous one ends."""

    document = _stacked_page_document()
    window = _window(document)
    window.split_article_at_block("block_004")

    segments = document.metadata["article_segments"]
    exportable = window._exportable_segments(segments)
    assert [window._segment_key(segment) for segment in exportable] == ["block_001", "block_004"]


def test_a_pdf_without_real_segmentation_exports_as_one_article():
    """One article has no boundary to confirm, so it is not held back."""

    document = _stacked_page_document()
    window = _window(document)
    segments = [
        {"index": 1, "abstract_number": "ABSN", "start_page": 1, "end_page": 1, "columns": None}
    ]

    assert window._exportable_segments(segments) == segments


def test_no_detected_segments_exports_the_whole_document():
    """A PDF with no boundaries is a single article and exports whole."""

    document = _stacked_page_document()
    window = _window(document)

    documents = window._documents_to_export(document)

    assert documents == [document]


def test_a_single_detected_segment_is_scoped_and_not_the_whole_pdf():
    """One detected article is still a range; only its own blocks may export.

    The export used to fall back to the whole document whenever there was at
    most one segment, which dumped every excluded block - other articles'
    lines included - into the XML.
    """

    document = _stacked_page_document()
    window = _window(document)
    window.linker = AuthorLinker()
    document.metadata["article_segments"] = [
        {"index": 1, "abstract_number": "ABSN", "start_page": 1, "end_page": 1, "columns": [1]}
    ]

    documents = window._documents_to_export(document)

    assert len(documents) == 1
    assert [block.id for block in documents[0].blocks] == ["block_007", "block_008"]


def test_a_deleted_unconfirmed_article_stays_out_of_the_export():
    """Deleting removes an article even after its boundary was confirmed."""

    document = _stacked_page_document()
    window = _window(document)
    window.split_article_at_block("block_004")
    window.delete_article(document.metadata["article_segments"][0])

    exportable = window._exportable_segments(document.metadata["article_segments"])
    assert [window._segment_key(segment) for segment in exportable] == ["block_004"]


def test_role_assignment_applies_to_a_multi_block_selection():
    """A selection is assigned its roles before it is finished."""

    document = _stacked_page_document()
    window = _headless_window(document)
    window.props = SimpleNamespace(role_editor=SimpleNamespace(currentText=lambda: "abstract"))
    window._selected_block_ids = {"block_002", "block_003"}

    window.apply_selected_block_role()

    by_id = {block.id: block for block in document.blocks}
    assert by_id["block_002"].role == "abstract"
    assert by_id["block_003"].role == "abstract"
    assert by_id["block_001"].role != "abstract"


def _titled_window() -> tuple[MainWindow, Document]:
    """A window over the stacked page, ready to classify titles by hand."""

    document = _stacked_page_document()
    window = _headless_window(document)
    window.props = SimpleNamespace(role_editor=SimpleNamespace(currentText=lambda: "title"))
    return window, document


def test_a_classified_title_creates_the_article_it_owns():
    """Classifying a title is the moment the article is created.

    The article starts as exactly the title block: the user then attaches the
    rest of the roles, and nothing else is pulled in.
    """

    window, document = _titled_window()
    window._selected_block_ids = {"block_004"}

    window.apply_selected_block_role()

    by_id = {block.id: block for block in document.blocks}
    assert by_id["block_004"].role == "title"
    segments = document.metadata["article_segments"]
    assert len(segments) == 1
    assert window._segment_key(segments[0]) == "block_004"
    assert window._segment_member_ids(segments[0]) == ["block_004"]
    # The article that owns the title is the one the user is now working on.
    assert window._current_segment_key == "block_004"


def test_each_title_is_its_own_article():
    """Every classified title creates a separate article of its own."""

    window, document = _titled_window()
    for title_id in ("block_004", "block_007"):
        window._selected_block_ids = {title_id}
        window.apply_selected_block_role()

    segments = document.metadata["article_segments"]
    assert [window._segment_key(segment) for segment in segments] == ["block_004", "block_007"]
    assert [window._segment_member_ids(segment) for segment in segments] == [
        ["block_004"],
        ["block_007"],
    ]
    # Working on the title just classified makes its article the active one.
    assert window._current_segment_key == "block_007"


def test_assigning_a_role_attaches_the_block_to_the_active_article():
    """Roles the user classifies join the working article, block by block."""

    window, document = _titled_window()
    window._selected_block_ids = {"block_002"}
    window.apply_selected_block_role()
    window.props = SimpleNamespace(role_editor=SimpleNamespace(currentText=lambda: "author"))

    window._selected_block_ids = {"block_003"}
    window.apply_selected_block_role()

    segments = document.metadata["article_segments"]
    assert len(segments) == 1
    assert window._segment_member_ids(segments[0]) == ["block_002", "block_003"]
    by_id = {block.id: block for block in document.blocks}
    assert by_id["block_003"].role == "author"


def test_classifying_a_title_refreshes_the_article_tabs():
    """Creating an article in the model must update the Detected Articles panel.

    The panel used to keep showing "No article boundary was detected" after a
    title was classified, with Finish/Split/Delete still disabled, because the
    role paths never re-rendered the article list.
    """

    document = _stacked_page_document()
    window = _headless_window(document)
    window.props = SimpleNamespace(role_editor=SimpleNamespace(currentText=lambda: "title"))
    refreshed: list[bool] = []
    window._refresh_article_buttons = lambda: refreshed.append(True)
    window._selected_block_ids = {"block_004"}

    window.apply_selected_block_role()

    assert refreshed, "the article panel was not refreshed after classifying a title"
    assert document.metadata["article_segments"]


def test_reclassifying_a_title_away_removes_its_article():
    """Taking the title role off a block removes the article it owned."""

    window, document = _titled_window()
    for title_id in ("block_004", "block_007"):
        window._selected_block_ids = {title_id}
        window.apply_selected_block_role()
    assert len(document.metadata["article_segments"]) == 2

    window.props = SimpleNamespace(role_editor=SimpleNamespace(currentText=lambda: "abstract"))
    window._selected_block_ids = {"block_007"}
    window.apply_selected_block_role()

    assert len(document.metadata["article_segments"]) == 1
    by_id = {block.id: block for block in document.blocks}
    assert by_id["block_007"].role == "abstract"


def _two_column_detected_document() -> Document:
    """A two-article PDF whose detected ranges really separate the columns."""

    blocks = [
        _block("(S100) First paper", block_id="block_001", column=0, metadata={"reading_order": 1}),
        _block("First body.", block_id="block_002", column=0, metadata={"reading_order": 2}),
        _block("(S200) Second paper", block_id="block_003", column=1, metadata={"reading_order": 3}),
        _block("Second body.", block_id="block_004", column=1, metadata={"reading_order": 4}),
    ]
    segments = [
        {
            "index": 1,
            "abstract_number": "S100",
            "start_page": 1,
            "end_page": 1,
            "columns": [0],
            "abstract_number_block_id": "block_001",
        },
        {
            "index": 2,
            "abstract_number": "S200",
            "start_page": 1,
            "end_page": 1,
            "columns": [1],
            "abstract_number_block_id": "block_003",
        },
    ]
    return Document(
        blocks=blocks,
        raw_blocks=list(blocks),
        metadata={"detected_article_segments": segments},
    )


def _title_window_with_two_articles() -> tuple[MainWindow, Document]:
    """Two title-created articles: block_002 owns one, block_008 the other."""

    window, document = _titled_window()
    for title_id in ("block_002", "block_008"):
        window._selected_block_ids = {title_id}
        window.apply_selected_block_role()
    return window, document


def test_a_title_article_exports_only_its_members():
    """An article exports exactly the blocks attached to it, nothing else."""

    window, document = _title_window_with_two_articles()
    window.props = SimpleNamespace(role_editor=SimpleNamespace(currentText=lambda: "abstract"))
    window._selected_block_ids = {"block_006"}
    window.apply_selected_block_role()
    window.linker = AuthorLinker()

    first = window._document_for_segment(window._segment_for_key("block_002"))
    assert [block.id for block in first.blocks] == ["block_002"]

    second = window._document_for_segment(window._segment_for_key("block_008"))
    assert [block.id for block in second.blocks] == ["block_006", "block_008"]
    assert second.title == "GENERATING PATIENT-DERIVED STEM CELLS"


def test_a_title_inherits_the_detected_article_number():
    """A title keeps the Abstract No extraction attributed to its range."""

    document = _two_column_detected_document()
    window = _headless_window(document)
    window.props = SimpleNamespace(role_editor=SimpleNamespace(currentText=lambda: "title"))
    window._selected_block_ids = {"block_003"}

    window.apply_selected_block_role()

    segments = document.metadata["article_segments"]
    assert [segment["abstract_number"] for segment in segments] == ["S200"]


def test_role_assignment_outside_the_current_article_is_refused(monkeypatch):
    """A block another article owns cannot be reassigned or attached."""

    from PySide6.QtWidgets import QMessageBox

    window, document = _title_window_with_two_articles()
    # block_008's article is active, so block_002 belongs to its neighbour.
    window.props = SimpleNamespace(role_editor=SimpleNamespace(currentText=lambda: "abstract"))
    monkeypatch.setattr(QMessageBox, "information", lambda *args, **kwargs: QMessageBox.Ok)

    window._selected_block_ids = {"block_002"}
    window.apply_selected_block_role()

    by_id = {block.id: block for block in document.blocks}
    assert by_id["block_002"].role != "abstract"
    assert window._segment_member_ids(document.metadata["article_segments"][0]) == ["block_002"]


def test_role_assignment_inside_the_current_article_is_allowed(monkeypatch):
    """An unclaimed block joins the working article when its role is assigned."""

    from PySide6.QtWidgets import QMessageBox

    window, document = _title_window_with_two_articles()
    window.props = SimpleNamespace(role_editor=SimpleNamespace(currentText=lambda: "affiliation"))
    monkeypatch.setattr(QMessageBox, "information", lambda *args, **kwargs: QMessageBox.Ok)

    window._selected_block_ids = {"block_006"}
    window.apply_selected_block_role()

    by_id = {block.id: block for block in document.blocks}
    assert by_id["block_006"].role == "affiliation"
    owner = window._article_owning("block_006")
    assert owner is not None and window._segment_member_ids(owner) == ["block_006", "block_008"]


def test_the_scope_restriction_only_covers_a_title_driven_document():
    """Without a classified title there are no articles to confine roles to."""

    document = _stacked_page_document()
    window = _window(document)

    assert window._article_scope_block_ids() is None
    assert window._blocks_outside_active_article(document.blocks) == []


def test_segment_blocks_lists_only_each_articles_own_blocks():
    """The Detected Articles list shows one article's blocks, not the document's."""

    document = Document(
        blocks=[
            _block("First article body.", block_id="block_001", page=1, metadata={"reading_order": 1}),
            _block("Second article body.", block_id="block_002", page=2, metadata={"reading_order": 2}),
        ],
        raw_blocks=[],
        metadata={
            "article_segments": [
                {"index": 1, "abstract_number": "S1", "start_page": 1, "end_page": 1, "columns": None},
                {"index": 2, "abstract_number": "S2", "start_page": 2, "end_page": 2, "columns": None},
            ]
        },
    )
    window = _window(document)

    segments = document.metadata["article_segments"]
    assert [
        [block.id for block in window._segment_blocks(segment)] for segment in segments
    ] == [["block_001"], ["block_002"]]


def test_deleting_an_article_hides_it_from_the_visible_list():
    """A deleted article's tab must leave Detected Articles, and return on restore."""

    document = _stacked_page_document()
    window = _window(document)
    window.split_article_at_block("block_004")
    window.split_article_at_block("block_007")
    segments = document.metadata["article_segments"]

    assert len(window._visible_segments()) == 3
    window.delete_article(segments[1])
    assert len(window._visible_segments()) == 2
    assert all(segment is not segments[1] for segment in window._visible_segments())
    window.restore_article(segments[1])
    assert len(window._visible_segments()) == 3


def test_deleting_the_current_article_moves_the_working_article_on():
    """With its tab gone, the deleted article cannot stay the working one."""

    document = _stacked_page_document()
    window = _window(document)
    window.split_article_at_block("block_004")
    window.split_article_at_block("block_007")
    segments = document.metadata["article_segments"]
    window._current_segment_key = window._segment_key(segments[1])

    window.delete_article(segments[1])

    assert window._current_segment_key == window._segment_key(segments[0])


def _two_article_authors_document() -> Document:
    """One PDF holding two articles, each with its own author and affiliation."""

    blocks = [
        _block("Jane Doe1", block_id="a1", column=0, metadata={"reading_order": 1}),
        _block(
            "1 Alpha University, USA",
            block_id="f1",
            column=0,
            metadata={"reading_order": 2},
        ),
        _block("John Smith2", block_id="a2", column=0, metadata={"reading_order": 3}),
        _block(
            "2 Beta University, UK",
            block_id="f2",
            column=0,
            metadata={"reading_order": 4},
        ),
    ]
    blocks[0].role = "author"
    blocks[1].role = "affiliation"
    blocks[2].role = "author"
    blocks[3].role = "affiliation"
    segments = [
        {
            "index": 1,
            "abstract_number": "P-605",
            "start_page": 1,
            "end_page": 1,
            "columns": [0],
            "start_order": 1,
            "end_order": 2,
        },
        {
            "index": 2,
            "abstract_number": "P-606",
            "start_page": 1,
            "end_page": 1,
            "columns": [0],
            "start_order": 3,
            "end_order": 4,
        },
    ]
    return Document(
        blocks=blocks, raw_blocks=list(blocks), metadata={"article_segments": segments}
    )


def test_link_review_shows_only_the_working_articles_authors():
    """A multi-article PDF must not mix the articles' bylines in the review."""

    document = _two_article_authors_document()
    window = _window(document)
    window.linker = AuthorLinker()

    window._current_segment_key = "a1"
    authors, affiliations, _issues = window._link_review_scope()
    assert [author.display_name for author in authors] == ["Jane Doe"]
    assert [affiliation.text for affiliation in affiliations] == ["Alpha University, USA"]

    window._current_segment_key = "a2"
    authors, affiliations, _issues = window._link_review_scope()
    assert [author.display_name for author in authors] == ["John Smith"]
    assert [affiliation.text for affiliation in affiliations] == ["Beta University, UK"]


def test_finished_article_authors_leave_the_review_panel():
    """Once an article is finished its byline must not stay in the review."""

    document = _two_article_authors_document()
    window = _window(document)
    window.linker = AuthorLinker()
    window._finished_segment_keys = ["a1"]
    window._current_segment_key = None

    # The finished first article is skipped; the review moves to the second.
    authors, _affiliations, _issues = window._link_review_scope()
    assert [author.display_name for author in authors] == ["John Smith"]

    window._finished_segment_keys = ["a1", "a2"]
    assert window._link_review_scope() == ([], [], [])


class _FakeOpenRouter:
    """Stand-in LLM client that returns a canned author/affiliation parse."""

    _available = True

    def __init__(self, response: dict) -> None:
        self.response = response
        self.calls: list[tuple] = []

    def classify(self, prompt: str, payload: dict) -> dict:
        self.calls.append((prompt, payload))
        return self.response


def test_llm_parse_and_link_replaces_the_heuristic_links(tmp_path):
    """Finishing an article links its authors from the model's own parse."""

    document = _two_article_authors_document()
    window = _finishable_window(document, tmp_path)
    window.openrouter_client = _FakeOpenRouter(
        {
            "authors": [
                {
                    "display_name": "Jane Q. Doe",
                    "given_names": "Jane Q.",
                    "surname": "Doe",
                    "initials": "J.Q.",
                    "markers": ["1"],
                    "affiliations": ["1"],
                }
            ],
            "affiliations": [{"marker": "1", "text": "Alpha University, USA"}],
        }
    )

    segment = window._segment_for_key("a1")
    segment_document = window._document_for_segment(segment)
    assert window._link_authors_with_llm(segment, segment_document) is True

    assert [author.display_name for author in segment_document.authors] == ["Jane Q. Doe"]
    assert segment_document.metadata["author_link_source"] == "llm"
    assert window._llm_links_by_segment["a1"].authors[0].display_name == "Jane Q. Doe"
    # The model only ever sees this article's own lines, never its neighbour's.
    _prompt, payload = window.openrouter_client.calls[0]
    assert [entry["block_id"] for entry in payload["author_blocks"]] == ["a1"]
    assert [entry["block_id"] for entry in payload["affiliation_blocks"]] == ["f1"]


def test_export_article_xml_validates_and_writes_the_linked_document(tmp_path):
    """The XML written is the same LLM-linked document that was validated."""

    document = _two_article_authors_document()
    window = _finishable_window(document, tmp_path)
    window._html_preview_merges = []
    window.paragraph_reconstructor = ParagraphReconstructor()
    window._refresh_link_review = lambda: None
    window._update_document_model = lambda: None
    captured: dict = {}

    def fake_validate(doc):
        captured["document"] = doc
        return []

    def fake_export(segment, output_dir, segment_document=None):
        captured["exported"] = segment_document
        return tmp_path / "article.xml"

    window.validator = SimpleNamespace(validate_document=fake_validate)
    window._export_segment_document = fake_export
    window.openrouter_client = _FakeOpenRouter(
        {
            "authors": [
                {
                    "display_name": "Jane Q. Doe",
                    "given_names": "Jane Q.",
                    "surname": "Doe",
                    "initials": "J.Q.",
                    "affiliations": ["1"],
                }
            ],
            "affiliations": [{"marker": "1", "text": "Alpha University, USA"}],
        }
    )
    # The stubbed finish path is bypassed so the real export runs.
    del window._export_article_xml

    segment = window._segment_for_key("a1")
    _path, validation_errors = window._export_article_xml(segment)

    assert validation_errors == []
    assert captured["exported"] is captured["document"]
    assert captured["document"].metadata["author_link_source"] == "llm"
    assert [author.display_name for author in captured["document"].authors] == [
        "Jane Q. Doe"
    ]
