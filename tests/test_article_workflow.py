"""Tests for the per-article finish/lock workflow."""

from pdf_to_jats.core.pdf_extractor import PDFExtractor
from pdf_to_jats.core.author_linker import AuthorLinker
from pdf_to_jats.core.jats_generator import JATSGenerator
from pdf_to_jats.core.paragraph_reconstructor import ParagraphReconstructor
from pdf_to_jats.gui.main_window import MainWindow
from pdf_to_jats.models.block import TextBlock
from pdf_to_jats.models.document import Document, block_matches_segment


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
    return window


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
