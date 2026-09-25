"""Tests for affiliation author-group output and label parsing."""

from lxml import etree

from pdf_to_jats.core.author_linker import AuthorLinker
from pdf_to_jats.core.jats_generator import JATSGenerator
from pdf_to_jats.models.affiliation import Affiliation
from pdf_to_jats.models.author import Author
from pdf_to_jats.models.document import Document

ANI_NS = JATSGenerator.ANI_NS
CE_NS = JATSGenerator.CE_NS


def _q(tag: str) -> str:
    return f"{{{ANI_NS}}}{tag}"


def _ce(tag: str) -> str:
    return f"{{{CE_NS}}}{tag}"


def _author(initials: str, surname: str, affiliation_ids: list[str]) -> Author:
    return Author(
        initials=initials,
        surname=surname,
        display_name=f"{initials} {surname}",
        affiliation_ids=affiliation_ids,
    )


def _groups(tmp_path, document: Document):
    output = tmp_path / "article.xml"
    JATSGenerator().generate(document, output)
    root = etree.parse(str(output)).getroot()
    return root.findall(f".//{_q('author-group')}")


def _organization(group) -> str | None:
    element = group.find(f"{_q('affiliation')}/{_q('organization')}")
    return element.text if element is not None else None


def _surnames(group) -> list[str | None]:
    return [element.text for element in group.findall(f"{_q('author')}/{_ce('surname')}")]


def _document(authors: list[Author], affiliations: list[Affiliation]) -> Document:
    return Document(title="A Study", authors=authors, affiliations=affiliations)


def _affiliation(identifier: str, organization: str) -> Affiliation:
    return Affiliation(
        id=identifier,
        text=f"{organization}, Somewhere, USA",
        marker=identifier,
        raw_marker=identifier,
        markers=[identifier],
    )


def test_each_affiliation_gets_its_own_author_group(tmp_path):
    groups = _groups(
        tmp_path,
        _document(
            [_author("A.", "Alpha", ["1"]), _author("B.", "Beta", ["2"])],
            [_affiliation("1", "First University"), _affiliation("2", "Second University")],
        ),
    )

    assert len(groups) == 2
    assert _organization(groups[0]) == "First University"
    assert _surnames(groups[0]) == ["Alpha"]
    assert _organization(groups[1]) == "Second University"
    assert _surnames(groups[1]) == ["Beta"]


def test_author_with_two_affiliations_is_repeated_in_both_groups(tmp_path):
    groups = _groups(
        tmp_path,
        _document(
            [_author("A.", "Alpha", ["1", "2"]), _author("B.", "Beta", ["1"])],
            [_affiliation("1", "First University"), _affiliation("2", "Second University")],
        ),
    )

    assert len(groups) == 2
    assert _surnames(groups[0]) == ["Alpha", "Beta"]
    assert _surnames(groups[1]) == ["Alpha"]


def test_authors_without_affiliations_are_not_dropped(tmp_path):
    groups = _groups(
        tmp_path,
        _document([_author("A.", "Alpha", [])], [_affiliation("1", "First University")]),
    )

    assert len(groups) == 2
    assert _organization(groups[0]) == "First University"
    assert _surnames(groups[0]) == []
    assert _organization(groups[1]) is None
    assert _surnames(groups[1]) == ["Alpha"]


def test_linker_order_drives_the_group_order(tmp_path):
    linked = AuthorLinker().link_from_texts(
        ["Jane Doe2", "John Smith1"],
        ["1 Alpha University, Somewhere, USA; 2 Beta University, Elsewhere, USA"],
    )

    groups = _groups(tmp_path, _document(linked.authors, linked.affiliations))

    assert [_organization(group) for group in groups] == ["Beta University", "Alpha University"]


def test_glued_affiliation_labels_are_detected():
    linked = AuthorLinker().link_from_texts(
        ["Jane Doe1", "John Smith21"],
        ["1Atrium Health, Charlotte, USA; 21Department of Internal Medicine, City, USA"],
    )

    assert [affiliation.id for affiliation in linked.affiliations] == ["1", "21"]
    assert linked.authors[0].affiliation_ids == ["1"]
    assert linked.authors[1].affiliation_ids == ["21"]


def test_ambiguously_glued_label_prefers_a_cited_marker():
    """\"214th Department\" is marker 21 followed by \"4th Department\"."""

    linked = AuthorLinker().link_from_texts(
        ["Jane Doe21"],
        ["214th Department of Internal Medicine, City, USA"],
    )

    assert linked.affiliations[0].id == "21"
    assert linked.authors[0].affiliation_ids == ["21"]


def test_wrapped_affiliation_lines_are_rejoined():
    linked = AuthorLinker().link_from_texts(
        ["Jane Doe10"],
        ["10Seoul National University Hospital, Seoul,", "South Korea"],
    )

    assert len(linked.affiliations) == 1
    assert linked.affiliations[0].text.endswith("South Korea")
