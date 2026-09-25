from pdf_to_jats.core.pdf_extractor import PDFExtractor
from pdf_to_jats.models.block import TextBlock


def _block(text: str, block_id: str = "block_001") -> TextBlock:
    return TextBlock(id=block_id, page=1, x=0, y=0, width=500, height=20, bbox=[0, 0, 500, 20], text=text)


def test_parenthesized_abstract_number_is_extracted():
    number, block_ids = PDFExtractor(use_docling=False)._extract_abstract_number(
        [_block("(S100) PHASE 3, RANDOMIZED STUDY OF TALQUETAMAB")]
    )

    assert number == "S100"
    assert block_ids == ["block_001"]


def test_normal_parenthesized_title_text_is_not_an_abstract_number():
    number, block_ids = PDFExtractor(use_docling=False)._extract_abstract_number(
        [_block("A Review of Treatment Options (2024)")]
    )

    assert number == ""
    assert block_ids == []


def test_header_words_before_pipe_are_not_abstract_numbers():
    number, block_ids = PDFExtractor(use_docling=False)._extract_abstract_number(
        [_block("Book | HemaSphere | (S100) PHASE 3")]
    )

    assert number == ""
    assert block_ids == []


def test_lowercase_body_token_is_not_an_abstract_number():
    number, block_ids = PDFExtractor(use_docling=False)._extract_abstract_number(
        [_block("mMCP1 | enzyme expression in tissue")]
    )

    assert number == ""
    assert block_ids == []


def test_parenthesized_drug_abbreviation_is_not_an_abstract_number():
    # A wrapped title line can start with a parenthesized drug abbreviation.
    number, block_ids = PDFExtractor(use_docling=False)._extract_abstract_number(
        [_block("(POM) VS DARA PLUS POM AND DEXAMETHASONE (DPD)")]
    )

    assert number == ""
    assert block_ids == []


def test_parenthesized_affiliation_is_not_an_abstract_number():
    number, block_ids = PDFExtractor(use_docling=False)._extract_abstract_number(
        [_block("(Ichilov) Medical Center, Tel Aviv, Israel")]
    )

    assert number == ""
    assert block_ids == []


def test_license_footer_saying_abstract_book_is_not_a_marker():
    markers = PDFExtractor(use_docling=False)._extract_abstract_numbers(
        [_block("Abstract Book distributed under the Attribution-NonCommercial-NoDerivs license")]
    )

    assert markers == []


def test_only_markers_containing_a_digit_are_detected():
    markers = PDFExtractor(use_docling=False)._extract_abstract_numbers(
        [_block("(S100) PHASE 3 STUDY", "block_001"), _block("(POM) CONTINUATION", "block_002")]
    )

    assert [marker["abstract_number"] for marker in markers] == ["S100"]


def test_inline_marker_beside_title_text_is_not_a_dedicated_block():
    extractor = PDFExtractor(use_docling=False)

    assert not extractor._is_abstract_number_only("(S100) PHASE 3, RANDOMIZED STUDY", "S100")
    assert extractor._is_abstract_number_only("Abstract No: 4349", "4349")
