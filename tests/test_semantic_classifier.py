from pdf_to_jats.core.semantic_classifier import SemanticClassifier
from pdf_to_jats.models.block import TextBlock

# A4, the page the running-head and multi-article cases were observed on.
PAGE_SURFACE = [0.0, 0.0, 595.2760009765625, 841.8900146484375]


def _block(
    text: str,
    block_id: str = "block_001",
    page: int = 1,
    y: float = 50.0,
    font_size: float = 10.0,
    bold: bool = False,
    metadata: dict | None = None,
) -> TextBlock:
    return TextBlock(
        id=block_id,
        page=page,
        x=0,
        y=y,
        width=500,
        height=20,
        bbox=[0, y, 500, y + 20],
        text=text,
        font_size=font_size,
        bold=bold,
        metadata=dict(metadata or {}),
    )


def _on_page(text: str, **kwargs) -> TextBlock:
    return _block(text, metadata={"visible_area": PAGE_SURFACE}, **kwargs)


def test_labelled_marker_is_classified_as_abstract_number():
    results = SemanticClassifier().classify_blocks([_block("Abstract No: 4349")])

    assert results[0].role == "abstract_number"
    assert results[0].confidence == 1.0


def test_parenthesised_marker_is_classified_as_abstract_number():
    results = SemanticClassifier().classify_blocks([_block("(S100)")])

    assert results[0].role == "abstract_number"


def test_title_with_parenthesised_year_is_not_a_marker():
    results = SemanticClassifier().classify_blocks([_block("A Review of Treatment Options (2024)")])

    assert results[0].role != "abstract_number"


def test_bare_number_is_footer_noise_not_a_marker():
    results = SemanticClassifier().classify_blocks([_block("4349")])

    assert results[0].role == "unclassified"


def test_running_head_banner_is_not_a_title():
    """A banner set far below the body size sits in the margin, not the title."""

    banner = _on_page(
        "2026 ANNUAL SYMPOSIUM • HELSINKI FINLAND",
        font_size=4.14,
        y=52.2,
    )
    body = _on_page(
        "Mild urea cycle disorders are identified by screening.",
        block_id="block_002",
        font_size=9.0,
        y=200.0,
    )

    results = SemanticClassifier().classify_blocks([banner, body])

    assert results[0].role == "unclassified"


def test_title_low_on_a_two_column_page_is_still_a_title():
    """The bottom half of the page is not page furniture: articles continue there."""

    title = _on_page(
        "LONGITUDINAL ASSESSMENT OF PROTEIN TOLERANCE",
        block_id="block_001",
        font_size=11.0,
        y=694.8,
    )
    body_blocks = [
        _on_page(
            "Mild urea cycle disorders are identified by screening.",
            block_id=f"block_{position:03d}",
            font_size=9.0,
            y=200.0 + position * 12,
        )
        for position in range(2, 5)
    ]

    results = SemanticClassifier().classify_blocks([title, *body_blocks])

    assert results[0].role == "title"


def test_footer_in_the_bottom_margin_is_not_a_title():
    footer = _on_page(
        "Proceedings of the Annual Meeting 2026",
        block_id="block_001",
        font_size=11.0,
        y=820.0,
    )
    body = _on_page(
        "Mild urea cycle disorders are identified by screening.",
        block_id="block_002",
        font_size=9.0,
        y=200.0,
    )

    results = SemanticClassifier().classify_blocks([footer, body])

    assert results[0].role == "unclassified"


def test_marker_carrying_author_list_is_classified_as_authors():
    line = _on_page(
        "Alessandro La Rosa², Marco Scaglione¹, Francesca Nastasia",
        bold=True,
        font_size=9.0,
        y=725.0,
    )

    results = SemanticClassifier().classify_blocks([line])

    assert results[0].role == "author"


def test_affiliation_line_with_markers_is_not_read_as_authors():
    line = _on_page(
        "IRCCS Giannina Gaslini, Genoa, Italy, ⁵Biochemical Unit",
        font_size=9.0,
        y=71.0,
    )

    results = SemanticClassifier().classify_blocks([line])

    assert results[0].role != "author"


def test_comma_separated_capitalised_words_without_markers_stay_a_title():
    """Only marker-carrying lists are trusted as authors; titles use commas too."""

    block = _on_page(
        "Growth, Puberty, and Endocrine Outcomes",
        bold=True,
        font_size=11.0,
    )

    results = SemanticClassifier().classify_blocks([block])

    assert results[0].role == "title"
