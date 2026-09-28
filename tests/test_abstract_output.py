"""Tests that the abstract and journal source reach the generated ANI XML."""

from lxml import etree

from pdf_to_jats.core.jats_generator import JATSGenerator
from pdf_to_jats.models.document import Document

ANI_NS = JATSGenerator.ANI_NS
CE_NS = JATSGenerator.CE_NS


def _root(document: Document, tmp_path):
    output = tmp_path / "article.xml"
    JATSGenerator().generate(document, output)
    return etree.parse(str(output)).getroot()


def _abstract(root):
    return root.find(f".//{{{ANI_NS}}}abstracts")


def _abstract_para(root):
    return root.find(f".//{{{ANI_NS}}}abstracts/{{{ANI_NS}}}abstract/{{{CE_NS}}}para")


def test_abstract_is_written_to_the_head(tmp_path):
    """Regression: the abstract block used to sit after an unreachable return."""

    root = _root(Document(title="A Study", abstract="We studied peptides in depth."), tmp_path)

    para = _abstract_para(root)

    assert para is not None
    assert para.text == "We studied peptides in depth."


def test_missing_abstract_writes_no_abstract_element(tmp_path):
    root = _root(Document(title="A Study"), tmp_path)

    assert _abstract(root) is None


def test_journal_source_is_written(tmp_path):
    """The journal <source> block was orphaned together with the abstract."""

    root = _root(Document(title="A Study", metadata={"journal_title": "HemaSphere"}), tmp_path)

    source = root.find(f".//{{{ANI_NS}}}source/{{{ANI_NS}}}sourcetitle")

    assert source is not None
    assert source.text == "HemaSphere"


def test_abstract_number_is_written_as_the_absn_itemid(tmp_path):
    root = _root(
        Document(title="A Study", abstract="Text.", metadata={"abstract_number": "S100"}),
        tmp_path,
    )

    itemids = {
        element.get("idtype"): element.text
        for element in root.findall(f".//{{{ANI_NS}}}itemidlist/{{{ANI_NS}}}itemid")
    }

    assert itemids["ABSN"] == "S100"
