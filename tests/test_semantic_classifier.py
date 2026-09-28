from pdf_to_jats.core.semantic_classifier import SemanticClassifier
from pdf_to_jats.models.block import TextBlock


def _block(text: str, block_id: str = "block_001", page: int = 1, y: float = 50.0) -> TextBlock:
    return TextBlock(
        id=block_id,
        page=page,
        x=0,
        y=y,
        width=500,
        height=20,
        bbox=[0, y, 500, y + 20],
        text=text,
        font_size=10.0,
    )


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
