
from pdf_to_jats.core.auto_refiner import AutoRefiner, is_abstract_heading
from pdf_to_jats.models.block import TextBlock


def _block(
    text: str,
    role: str = "unclassified",
    font_size: float = 10.0,
    block_id: str = "block_001",
) -> TextBlock:
    return TextBlock(
        id=block_id,
        page=1,
        text=text,
        bbox=[0, 0, 500, 20],
        x=0,
        y=0,
        width=500,
        height=20,
        font_size=font_size,
        role=role,
    )


def test_funding_text_is_not_title():
    block = _block("This work was supported by NIH R01 GM114666 and GM145813 to DSK.", role="title")
    report = AutoRefiner().refine([block])

    assert block.role == "unclassified"
    assert report.changed[block.id] == "unclassified"


def test_doi_line_is_not_title():
    block = _block("112844, https://doi.org/10.1016/j.jbc.2026.112844", role="title")
    AutoRefiner().refine([block])

    assert block.role == "unclassified"


def test_keywords_line_is_not_title():
    block = _block("Keywords: peptides, protein structure, antimicrobial", role="title")
    AutoRefiner().refine([block])

    assert block.role == "unclassified"


def test_author_institution_line_is_reassigned_to_author():
    block = _block("Sarah Smith, Bucknell University", role="title")
    AutoRefiner().refine([block])

    assert block.role == "author"


def test_author_comma_list_is_reassigned_to_author():
    block = _block(
        "Josephina Vermillion, Joseph Feudale, Christopher Feudale, Marla Forfar",
        role="title",
    )
    AutoRefiner().refine([block])

    assert block.role == "author"


def test_real_title_stays_title():
    block = _block(
        "The Relation of Sequence, Structure, and Function of Native Peptides",
        role="title",
        font_size=14.0,
    )
    AutoRefiner().refine([block])

    assert block.role == "title"


def test_sentence_like_body_text_near_abstract_heading_becomes_abstract():
    heading = _block("ABSTRACT", role="abstract", block_id="block_h")
    body = _block(
        "cecropins are a class of antimicrobial peptides that are natively expressed in insects",
        role="title",
        block_id="block_b",
    )
    AutoRefiner().refine([heading, body])

    assert body.role == "abstract"


def test_abstract_heading_is_kept_as_abstract():
    block = _block("Abstract 4349", role="title")
    AutoRefiner().refine([block])

    assert block.role == "abstract"


def test_manual_role_source_is_never_overwritten():
    block = _block("Keywords: peptides, protein structure", role="title")
    block.metadata["role_source"] = "manual"
    report = AutoRefiner().refine([block])

    assert block.role == "title"
    assert block.id not in report.changed


def test_is_abstract_heading_matches_expected_forms():
    assert is_abstract_heading("ABSTRACT")
    assert is_abstract_heading("Abstract 4349")
    assert is_abstract_heading("abstract no. S120")
    assert not is_abstract_heading("Abstract: we studied peptides in depth")
    assert not is_abstract_heading("Antimicrobial Peptides")


def test_changed_metadata_records_previous_role():
    block = _block("Keywords: peptides", role="title")
    AutoRefiner().refine([block])

    assert block.metadata["role_source"] == "auto_refine"
    assert block.metadata["role_before_refine"] == "title"
