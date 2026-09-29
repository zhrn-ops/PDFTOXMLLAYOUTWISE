"""Tests for the per-article finish/lock workflow."""

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
    exported = window._document_for_segment(
        _segment(columns=[1]),
        Document(blocks=continued, raw_blocks=[left, right]),
    )
    assert exported.abstract == "First half Second half"


def _window(document: Document) -> MainWindow:
    """Build the minimum MainWindow state the marker helpers rely on."""

    window = MainWindow.__new__(MainWindow)
    window.document = document
    window._current_segment_key = None
    window._finished_segment_keys = []
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
