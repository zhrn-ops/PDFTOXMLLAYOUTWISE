"""Elsevier ANI XML generation."""

from __future__ import annotations

from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path
import re
from typing import Any

try:
    from lxml import etree  # type: ignore
    LXML_AVAILABLE = True
except ModuleNotFoundError:
    import xml.etree.ElementTree as etree

    LXML_AVAILABLE = False

from pdf_to_jats.models.document import Document


class JATSGenerator:
    """Generate Elsevier ANI-style XML from the intermediate document."""

    ANI_NS = "http://www.elsevier.com/xml/ani/ani"
    XSI_NS = "http://www.w3.org/2001/XMLSchema-instance"
    CE_NS = "http://www.elsevier.com/xml/ani/common"
    SCHEMA_LOCATION = "http://www.elsevier.com/xml/ani/ani http://www.elsevier.com/xml/ani/ani515-input-CAR.xsd"

    def generate(self, document: Document, output_path: Path) -> Path:
        """Write an ANI XML file for the document."""

        root = self._build_root(document)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        if LXML_AVAILABLE:
            tree = etree.ElementTree(root)
            tree.write(
                str(output_path),
                pretty_print=True,
                xml_declaration=True,
                encoding="UTF-8",
                standalone=False,
            )
        else:
            tree = etree.ElementTree(root)
            tree.write(str(output_path), encoding="utf-8", xml_declaration=True)
        return output_path

    def _build_root(self, document: Document) -> Any:
        nsmap = {
            None: self.ANI_NS,
            "xsi": self.XSI_NS,
            "ce": self.CE_NS,
        }
        root = etree.Element(
            self._qname("units"),
            nsmap=nsmap if LXML_AVAILABLE else None,
        )
        root.set(self._qname_attr("schemaLocation", self.XSI_NS), self.SCHEMA_LOCATION)

        unit = etree.SubElement(root, self._qname("unit"))
        unit.set("type", "ARTICLE")

        self._append_unit_info(unit, document)
        content = etree.SubElement(unit, self._qname("unit-content"))
        bibrecord = etree.SubElement(content, self._qname("bibrecord"))
        self._append_item_info(bibrecord, document)
        self._append_head(bibrecord, document)
        self._append_body(content, document)
        return root

    def _append_body(self, parent: Any, document: Document) -> None:
        """Write only reviewed paragraph units; proposals are never exported."""

        paragraphs = [
            paragraph
            for paragraph in document.paragraphs
            if paragraph.status == "accepted" and paragraph.role in {"body", "unclassified"} and paragraph.text.strip()
        ]
        if not paragraphs:
            return
        body = etree.SubElement(parent, self._qname("body"))
        for paragraph in paragraphs:
            para = etree.SubElement(body, self._qname("para", self.CE_NS))
            para.text = self._sanitize_para_text(paragraph.text)

    def _append_unit_info(self, parent: Any, document: Document) -> None:
        unit_info = etree.SubElement(parent, self._qname("unit-info"))
        etree.SubElement(unit_info, self._qname("unit-id")).text = self._metadata_value(document, "unit_id", "00000000")
        etree.SubElement(unit_info, self._qname("order-id")).text = self._metadata_value(document, "order_id", "00000000")
        etree.SubElement(unit_info, self._qname("parcel-id")).text = self._metadata_value(document, "parcel_id", "none")
        etree.SubElement(unit_info, self._qname("supplier-id")).text = self._metadata_value(document, "supplier_id", "4")
        etree.SubElement(unit_info, self._qname("timestamp")).text = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")

    def _append_item_info(self, parent: Any, document: Document) -> None:
        item_info = etree.SubElement(parent, self._qname("item-info"))
        status = etree.SubElement(item_info, self._qname("status"))
        status.set("state", "new")
        status.set("stage", "S300")

        itemidlist = etree.SubElement(item_info, self._qname("itemidlist"))
        doi = self._metadata_value(document, "doi")
        if doi:
            etree.SubElement(itemidlist, self._qname("doi", self.CE_NS)).text = doi
        abstract_number = self._metadata_value(document, "abstract_number", "ABSN")
        default_itemids = [("00000000", "OSIN"), (abstract_number, "ABSN")]
        for item_id, idtype in self._metadata_list(document, "itemids", default_itemids):
            itemid = etree.SubElement(itemidlist, self._qname("itemid"))
            itemid.set("idtype", idtype)
            itemid.text = item_id

    def _append_head(self, parent: Any, document: Document) -> None:
        head = etree.SubElement(parent, self._qname("head"))
        citation_info = etree.SubElement(head, self._qname("citation-info"))
        etree.SubElement(citation_info, self._qname("citation-type")).set("code", "cb")
        citation_language = etree.SubElement(citation_info, self._qname("citation-language"))
        citation_language.set(self._qname_attr("lang", "http://www.w3.org/XML/1998/namespace"), self._metadata_value(document, "language", "ENG"))
        abstract_language = etree.SubElement(citation_info, self._qname("abstract-language"))
        abstract_language.set(self._qname_attr("lang", "http://www.w3.org/XML/1998/namespace"), self._metadata_value(document, "language", "ENG"))

        citation_title = etree.SubElement(head, self._qname("citation-title"))
        titletext = etree.SubElement(citation_title, self._qname("titletext"))
        titletext.set(self._qname_attr("lang", "http://www.w3.org/XML/1998/namespace"), self._metadata_value(document, "language", "ENG"))
        titletext.set("original", "y")
        titletext.text = self._sanitize_title(document.title, document.abstract_number()) or "Untitled"

        for seq, (affiliation, group_authors) in enumerate(self._author_groups(document), start=1):
            author_group = etree.SubElement(head, self._qname("author-group"))
            author_group.set("seq", str(seq))
            for author_seq, author in enumerate(group_authors, start=1):
                author_el = etree.SubElement(author_group, self._qname("author"))
                author_el.set("seq", str(author_seq))
                initials = self._sanitize_xml_text(author.initials)
                surname = self._sanitize_xml_text(author.surname)
                if not initials or not surname:
                    initials, surname = self._split_name(author.display_name or f"{author.initials} {author.surname}".strip())
                etree.SubElement(author_el, self._qname("initials", self.CE_NS)).text = initials
                etree.SubElement(author_el, self._qname("surname", self.CE_NS)).text = surname
            if affiliation is not None:
                affiliation_el = etree.SubElement(author_group, self._qname("affiliation"))
                self._append_affiliation_text(affiliation_el, affiliation.text)

        self._append_correspondence(head, document)

    def _author_groups(self, document: Document) -> list[tuple[Any, list[Any]]]:
        """Group authors under the affiliation each of them references.

        ANI expresses the author/affiliation link structurally, so every
        ``<author-group>`` carries one affiliation plus the authors pointing at
        it. An author with several affiliations is repeated in each of their
        groups, and authors with none are emitted last without an affiliation so
        they are not silently dropped.
        """

        groups: list[tuple[Any, list[Any]]] = []
        for affiliation in document.affiliations:
            # Unreferenced affiliations are still emitted; the linker reports
            # them rather than letting them vanish from the output.
            members = [
                author for author in document.authors if affiliation.id in author.affiliation_ids
            ]
            groups.append((affiliation, members))
        unassigned = [author for author in document.authors if not author.affiliation_ids]
        if unassigned:
            groups.append((None, unassigned))
        return groups

        if document.abstract:
            abstracts = etree.SubElement(head, self._qname("abstracts"))
            abstract = etree.SubElement(abstracts, self._qname("abstract"))
            abstract.set("original", "y")
            abstract.set(self._qname_attr("lang", "http://www.w3.org/XML/1998/namespace"), self._metadata_value(document, "language", "ENG"))
            para = etree.SubElement(abstract, self._qname("para", self.CE_NS))
            para.text = self._sanitize_para_text(document.abstract)

        source = etree.SubElement(head, self._qname("source"))
        source.set("srcid", self._metadata_value(document, "srcid", "000000000"))
        source.set("type", "journal")
        etree.SubElement(source, self._qname("sourcetitle")).text = self._metadata_value(document, "journal_title", "Unknown Journal")
        etree.SubElement(source, self._qname("sourcetitle-abbrev")).text = self._metadata_value(document, "journal_abbrev", self._metadata_value(document, "journal_title", "Unknown Journal"))
        etree.SubElement(source, self._qname("issn")).text = self._metadata_value(document, "issn", "")

        if document.metadata.get("volume") or document.metadata.get("issue") or document.metadata.get("pages"):
            volisspag = etree.SubElement(source, self._qname("volisspag"))
            vin = etree.SubElement(volisspag, self._qname("volume-issue-number"))
            if document.metadata.get("volume"):
                etree.SubElement(vin, self._qname("vol-first")).text = str(document.metadata["volume"])
            if document.metadata.get("issue"):
                etree.SubElement(vin, self._qname("suppl")).text = str(document.metadata["issue"])
            pages = document.metadata.get("pages")
            if isinstance(pages, dict):
                page_info = etree.SubElement(volisspag, self._qname("page-information"))
                pages_el = etree.SubElement(page_info, self._qname("pages"))
                etree.SubElement(pages_el, self._qname("first-page")).text = str(pages.get("first", ""))
                etree.SubElement(pages_el, self._qname("last-page")).text = str(pages.get("last", ""))

        if document.metadata.get("publication_year"):
            etree.SubElement(source, self._qname("publicationyear")).set("first", str(document.metadata["publication_year"]))

        if document.metadata.get("publication_date"):
            pub = document.metadata["publication_date"]
            publicationdate = etree.SubElement(source, self._qname("publicationdate"))
            etree.SubElement(publicationdate, self._qname("year")).text = str(pub.get("year", ""))
            etree.SubElement(publicationdate, self._qname("month")).text = str(pub.get("month", "")).zfill(2)
            etree.SubElement(publicationdate, self._qname("day")).text = str(pub.get("day", "")).zfill(2)
            etree.SubElement(publicationdate, self._qname("date-text")).text = str(pub.get("date_text", ""))

    def _append_affiliation_text(self, parent: Any, text: str) -> None:
        organization, city, country = self._split_affiliation(text)
        if organization:
            etree.SubElement(parent, self._qname("organization")).text = organization
        if city:
            etree.SubElement(parent, self._qname("city")).text = city
        if country:
            country_el = etree.SubElement(parent, self._qname("country"))
            country_el.set("iso-code", self._country_iso_code(country))
        if text.strip():
            etree.SubElement(parent, self._qname("source-text", self.CE_NS)).text = self._sanitize_xml_text(text)

    def _append_correspondence(self, parent: Any, document: Document) -> None:
        author = document.corresponding_author
        if author is None:
            return
        correspondence = etree.SubElement(parent, self._qname("correspondence"))
        person = etree.SubElement(correspondence, self._qname("person"))
        etree.SubElement(person, self._qname("initials", self.CE_NS)).text = self._sanitize_xml_text(author.initials)
        etree.SubElement(person, self._qname("surname", self.CE_NS)).text = self._sanitize_xml_text(author.surname)
        for affiliation_id in author.affiliation_ids:
            affiliation = next((item for item in document.affiliations if item.id == affiliation_id), None)
            if affiliation is None:
                continue
            affiliation_el = etree.SubElement(correspondence, self._qname("affiliation"))
            self._append_affiliation_text(affiliation_el, affiliation.text)

    def _split_name(self, full_name: str) -> tuple[str, str]:
        parts = [part for part in full_name.split() if part]
        if not parts:
            return "", ""
        if len(parts) == 1:
            return parts[0][:1].upper() + ".", parts[0]
        particles = {"da", "das", "de", "del", "della", "der", "di", "do", "dos", "du", "la", "le", "van", "vander", "von"}
        surname_start = next((index for index, part in enumerate(parts[1:], start=1) if part.rstrip(".").lower() in particles), len(parts) - 1)
        given = parts[:surname_start]
        initials = "".join(f"{part[0].upper()}." for part in given)
        return initials, " ".join(parts[surname_start:])

    def _split_affiliation(self, text: str) -> tuple[str, str, str]:
        parts = [part.strip() for part in text.split(",") if part.strip()]
        organization = parts[0] if parts else text
        city = parts[1] if len(parts) > 1 else ""
        country = parts[2] if len(parts) > 2 else ""
        return organization, city, country

    def _country_iso_code(self, country: str) -> str:
        country_codes = {
            "japan": "JPN",
            "united states": "USA",
            "united states of america": "USA",
            "uk": "GBR",
            "united kingdom": "GBR",
        }
        return country_codes.get(country.casefold(), "UNK")

    def _metadata_value(self, document: Document, key: str, default: str = "") -> str:
        value = document.metadata.get(key, default)
        return str(value) if value is not None else default

    def _sanitize_xml_text(self, value: str | None) -> str:
        """Remove control characters that are invalid in XML content."""

        if value is None:
            return ""
        text = str(value)
        return "".join(
            ch
            for ch in text
            if ch in "\t\n\r" or ord(ch) >= 0x20
        )

    def _sanitize_para_text(self, value: str | None) -> str:
        """Normalize paragraph whitespace so serialized paragraphs have no hard enters."""

        return re.sub(r"\s+", " ", self._sanitize_xml_text(value)).strip()

    def _sanitize_title(self, value: str | None, abstract_number: str = "") -> str:
        """Remove obvious DOI and section-heading noise from the title field."""

        text = re.sub(r"\s+", " ", self._sanitize_xml_text(value)).strip()
        if not text:
            return ""
        if abstract_number and abstract_number.upper() != "ABSN":
            text = re.sub(
                rf"^\s*[\(\[]?{re.escape(abstract_number)}[\)\]]?\s*(?:\||\u2502|[:.\-\u2013\u2014])?\s+",
                "",
                text,
                count=1,
                flags=re.IGNORECASE,
            )
        text = re.sub(r"^\s*doi:\s*\S+\s*", "", text, flags=re.IGNORECASE)
        text = re.sub(r"\bA\s*B\s*S\s*T\s*R\s*A\s*C\s*T\b", "", text, flags=re.IGNORECASE)
        text = re.sub(r"\s{2,}", " ", text).strip(" -:;,.")
        return text

    def _metadata_list(self, document: Document, key: str, default: list[tuple[str, str]]) -> list[tuple[str, str]]:
        value = document.metadata.get(key)
        if isinstance(value, list) and value:
            return [(str(item.get("value", "")), str(item.get("idtype", "OSIN"))) for item in value if isinstance(item, dict)]
        return default

    def _qname(self, tag: str, namespace: str | None = None) -> str:
        if namespace is None:
            namespace = self.ANI_NS
        return f"{{{namespace}}}{tag}"

    def _qname_attr(self, name: str, namespace: str) -> str:
        return f"{{{namespace}}}{name}"
