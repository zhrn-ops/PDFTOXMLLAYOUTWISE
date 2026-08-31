"""XML and content validation."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

try:
    from lxml import etree  # type: ignore
    LXML_AVAILABLE = True
except ModuleNotFoundError:
    import xml.etree.ElementTree as etree

    LXML_AVAILABLE = False

from pdf_to_jats.models.document import Document


@dataclass(slots=True)
class ValidationError:
    """Validation issue."""

    message: str


class Validator:
    """Validate generated JATS XML and required content."""

    def __init__(self, schema_path: Path | None = None) -> None:
        self.schema_path = schema_path or Path(__file__).resolve().parents[1] / "schemas" / "jats_1_4.xsd"

    def validate(self, xml_path: Path) -> list[ValidationError]:
        errors: list[ValidationError] = []
        try:
            tree = etree.parse(str(xml_path))
        except Exception as exc:
            return [ValidationError(message=f"XML parse error: {exc}")]
        root = tree.getroot()
        if LXML_AVAILABLE and self.schema_path.exists():
            try:
                schema_doc = etree.parse(str(self.schema_path))
                schema = etree.XMLSchema(schema_doc)
                if not schema.validate(tree):
                    for entry in schema.error_log:
                        errors.append(ValidationError(f"Schema error: {entry.message}"))
            except Exception as exc:
                errors.append(ValidationError(f"Schema load error: {exc}"))
        elif self.schema_path.exists() and not LXML_AVAILABLE:
            errors.append(ValidationError("Schema validation skipped: lxml is not installed"))
        if root.find(".//{http://www.elsevier.com/xml/ani/ani}titletext") is None and root.find(".//article-title") is None:
            errors.append(ValidationError("Missing article title"))
        if not root.findall(".//contrib") and not root.findall(".//{http://www.elsevier.com/xml/ani/ani}author"):
            errors.append(ValidationError("Missing authors"))
        if root.find(".//{http://www.elsevier.com/xml/ani/ani}abstract") is None and root.find(".//abstract") is None:
            errors.append(ValidationError("Missing abstract"))
        return errors

    def validate_document(self, document: Document) -> list[ValidationError]:
        """Validate the intermediate document before XML export."""

        errors: list[ValidationError] = []
        if not document.title.strip():
            errors.append(ValidationError("Missing title"))
        lowered_title = document.title.lower()
        if "doi:" in lowered_title or "abstract" in lowered_title and "flash talks" in lowered_title:
            errors.append(ValidationError("Title still contains DOI or section-heading noise"))
        if not document.authors:
            errors.append(ValidationError("Missing authors"))
        for author in document.authors:
            combined = f"{author.initials} {author.surname} {author.display_name}".strip()
            lowered = combined.lower()
            if not combined or not any(ch.isalpha() for ch in combined):
                errors.append(ValidationError("Invalid author entry: missing alphabetic content"))
                break
            if any(token in lowered for token in ("copyright", "published by", "all rights reserved")):
                errors.append(ValidationError("Invalid author entry: footer or copyright text detected"))
                break
            if combined.strip() in {"|", "||", "©", "®"}:
                errors.append(ValidationError("Invalid author entry: symbol-only author detected"))
                break
        if not document.affiliations:
            errors.append(ValidationError("Missing affiliations"))
        if not document.abstract.strip():
            errors.append(ValidationError("Missing abstract"))
        absn = document.abstract_number()
        if absn and absn != "ABSN" and any(ch.isspace() for ch in absn):
            errors.append(ValidationError("Abstract number contains whitespace"))
        return errors
