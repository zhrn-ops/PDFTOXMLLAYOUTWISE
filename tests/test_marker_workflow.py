"""Marker-driven article workflow tests.

The requested pipeline: the user classifies the roles of an article, and when
they tag a fresh ``abstract_number`` marker the classification jump-cuts there —
a new article starts at that marker, everything before it belongs to the
article just finished classifying.
"""

from pdf_to_jats.core.author_linker import AuthorLinker
from pdf_to_jats.gui.main_window import MainWindow
from pdf_to_jats.models.block import TextBlock
from pdf_to_jats.models.document import Document, block_matches_segment

from tests.test_article_workflow import _block, _window


def _document_with_blocks() -> Document:
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


def _marker(text: str, block_id: str, order: int, column: int = 0, x: float = 28.0, y: float = 0.0):
    """A line the user is tagging as ``abstract_number``.

    The real call flow assigns ``block.role = "abstract_number"`` before the
    sync hook runs, so the fixture does the same.
    """

    block = _block(
        text,
        block_id=block_id,
        column=column,
        metadata={"reading_order": order},
        x=x,
        y=y,
    )
    block.role = "abstract_number"
    return block


def test_tagging_a_fresh_marker_starts_a_new_article_at_it():
    document = _document_with_blocks()
    window = _window(document)
    marker = _marker("P-607", "block_901", 9, column=1, x=306.0, y=160.0)
    document.blocks.append(marker)
    document.raw_blocks.append(marker)

    window._sync_block_abstract_number_role(marker, "unclassified")

    segments = document.metadata["article_segments"]
    assert len(segments) == 2
    # The new article starts AT the marker, so it follows the article that
    # owned everything before it. The previous article is named from its own
    # first line, the same way a hand-made split names articles.
    assert [segment["abstract_number"] for segment in segments] == ["P-605", "P-607"]
    # The marker ends the article before it: the new article starts at the
    # marker and everything the stream holds after it follows.
    assert [block.id for block in document.blocks if block_matches_segment(block, segments[1])] == [
        "block_901"
    ]
    first = document.blocks[0]
    assert block_matches_segment(first, segments[0])


def test_tagging_a_marker_inside_a_single_article_splits_before_it():
    """The marker between two articles in one column is the common case."""

    document = _document_with_blocks()
    window = _window(document)
    document.metadata["article_segments"] = [
        {
            "index": 1,
            "abstract_number": "P-605",
            "start_page": 1,
            "end_page": 1,
            "columns": None,
            "start_order": 1,
            "end_order": 3,
            "boundary_source": "manual",
        }
    ]

    marker = _marker("Abstract No: P-606", "block_004", 4)
    window._sync_block_abstract_number_role(marker, "unclassified")

    segments = document.metadata["article_segments"]
    assert [segment["abstract_number"] for segment in segments] == ["P-605", "P-606"]
    assert [segment["start_order"] for segment in segments] == [1, 4]
    assert segments[1]["abstract_number_block_id"] == "block_004"


def test_untagging_the_split_marker_restores_the_previous_articles():
    """The tag and the article boundary live and die together."""

    document = _document_with_blocks()
    window = _window(document)
    marker = _marker("P-607", "block_901", 9, column=1, x=306.0, y=160.0)
    document.blocks.append(marker)
    document.raw_blocks.append(marker)

    window._sync_block_abstract_number_role(marker, "unclassified")
    marker.role = "unclassified"
    window._sync_block_abstract_number_role(marker, "abstract_number")

    segments = document.metadata["article_segments"]
    assert len(segments) == 1
    assert document.metadata.get("abstract_number_block_ids") in ([], None)


def test_tagging_the_first_marker_only_names_the_document():
    """The first article already starts at the stream's first block."""

    document = _document_with_blocks()
    window = _window(document)
    marker = document.blocks[0]
    marker.role = "abstract_number"

    window._sync_block_abstract_number_role(marker, "unclassified")

    segments = document.metadata["article_segments"]
    assert len(segments) == 1
    assert segments[0]["abstract_number"] == "P-605"
    assert segments[0]["abstract_number_block_id"] == "block_001"
    # The document default is replaced by the marker's number.
    assert document.abstract_number() == "P-605"


def test_retagging_an_existing_boundary_renames_its_article():
    document = _document_with_blocks()
    window = _window(document)
    marker = _marker("P-607", "block_901", 9, column=1, x=306.0, y=160.0)
    document.blocks.append(marker)
    document.raw_blocks.append(marker)
    window._sync_block_abstract_number_role(marker, "unclassified")
    before = [dict(segment) for segment in document.metadata["article_segments"]]

    marker.text = "P-999"
    window._sync_block_abstract_number_role(marker, "unclassified")

    segments = document.metadata["article_segments"]
    assert len(segments) == 2  # no second cut
    assert segments[1]["abstract_number"] == "P-999"
    assert [dict(segment) for segment in segments] != before


def test_split_marker_skips_the_recognised_noise_lines():
    """A running head tagged by mistake must not cut the article there."""

    document = _document_with_blocks()
    window = _window(document)

    banner = _marker("2026 ANNUAL SYMPOSIUM • HELSINKI FINLAND", "block_905", 5)
    window._sync_block_abstract_number_role(banner, "unclassified")

    # The marker is recorded, but no new article starts at the banner.
    segments = document.metadata.get("article_segments")
    assert not segments
    assert document.metadata.get("abstract_number_block_ids") == ["block_905"]


def test_marker_workflow_updates_the_document_model():
    """Tagging a marker flows into the exportable document model."""

    document = _document_with_blocks()
    window = _window(document)
    window.linker = AuthorLinker()
    window._refresh_link_review = lambda: None
    window._rebuild_paragraph_model = lambda: None

    marker = _marker("P-607", "block_901", 9, column=1, x=306.0, y=160.0)
    document.blocks.append(marker)
    document.raw_blocks.append(marker)
    window._sync_block_abstract_number_role(marker, "unclassified")
    window._update_document_model()

    segments = document.metadata["article_segments"]
    assert len(segments) == 2
    assert segments[1]["abstract_number"] == "P-607"
    assert segments[1]["abstract_number_block_id"] == "block_901"


def test_full_name_author_list_is_classified_as_author():
    """Regression: bold-italic author lines used to glue onto the title."""

    from pdf_to_jats.core.semantic_classifier import SemanticClassifier

    line = TextBlock(
        id="block_auth",
        page=1,
        x=306.0,
        y=725.0,
        width=250.0,
        height=12.5,
        bbox=[306.0, 725.0, 556.0, 737.5],
        text="Alessandro La Rosa², Marco Scaglione¹, Francesca Nastasia",
        font_size=9.0,
        bold=True,
        italic=True,
        metadata={"reading_order": 40},
    )
    body = TextBlock(
        id="block_body",
        page=1,
        x=306.0,
        y=800.0,
        width=250.0,
        height=12.5,
        bbox=[306.0, 800.0, 556.0, 812.5],
        text="Mild urea cycle disorders are identified by screening.",
        font_size=9.0,
        metadata={"reading_order": 41},
    )

    results = SemanticClassifier().classify_blocks([line, body])

    assert results[0].role == "author"
