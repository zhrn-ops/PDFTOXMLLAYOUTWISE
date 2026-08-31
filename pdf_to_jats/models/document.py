"""Document model and JSON serialization helpers."""

from __future__ import annotations

from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Any
import json

from pdf_to_jats.models.affiliation import Affiliation
from pdf_to_jats.models.author import Author
from pdf_to_jats.models.block import TextBlock
from pdf_to_jats.models.paragraph import Paragraph


@dataclass(slots=True)
class DocumentZone:
    """User-editable region on a PDF page."""

    id: str
    page: int
    label: str
    bbox: list[float]
    source: str = "auto"
    bboxes: list[list[float]] = field(default_factory=list)


@dataclass(slots=True)
class Document:
    """Intermediate document representation used across the pipeline."""

    title: str = ""
    authors: list[Author] = field(default_factory=list)
    affiliations: list[Affiliation] = field(default_factory=list)
    abstract: str = ""
    raw_blocks: list[TextBlock] = field(default_factory=list)
    blocks: list[TextBlock] = field(default_factory=list)
    paragraphs: list[Paragraph] = field(default_factory=list)
    zones: list[DocumentZone] = field(default_factory=list)
    figures: list[dict[str, Any]] = field(default_factory=list)
    tables: list[dict[str, Any]] = field(default_factory=list)
    references: list[str] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        """Serialize the document into a JSON-compatible dictionary."""

        return asdict(self)

    def to_layout_json(self) -> dict[str, Any]:
        """Serialize the extracted layout for downstream semantic processing."""

        return {
            "blocks": [asdict(block) for block in self.blocks],
            "paragraphs": [asdict(paragraph) for paragraph in self.paragraphs],
            "zones": [asdict(zone) for zone in self.zones],
            "metadata": dict(self.metadata),
            "figures": list(self.figures),
            "tables": list(self.tables),
            "references": list(self.references),
        }

    def to_markdown(self) -> str:
        """Serialize extracted blocks into a review-friendly markdown transcript."""

        excluded_ids = set(self.metadata.get("abstract_number_block_ids", []))
        lines: list[str] = []
        current_page: int | None = None
        for block in sorted(self.blocks, key=lambda item: (item.page, item.y, item.x)):
            if block.id in excluded_ids:
                continue
            if block.page != current_page:
                current_page = block.page
                lines.append(f"# Page {current_page}")
            text = " ".join(block.text.replace("\n", " ").split()).strip()
            if not text:
                continue
            lines.append(f"- `{block.id}`: {text}")
        return "\n".join(lines).strip() + ("\n" if lines else "")

    def to_json(self, path: Path) -> None:
        """Write the document JSON representation to disk."""

        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(self.to_dict(), indent=2, ensure_ascii=False), encoding="utf-8")

    def to_pipeline_report(self) -> dict[str, Any]:
        """Summarize the end-to-end extraction and classification pipeline."""

        excluded_ids = set(self.metadata.get("abstract_number_block_ids", []))
        abstract_number = self.abstract_number()
        return {
            "source_pdf": self.metadata.get("source_pdf", ""),
            "block_count": len(self.blocks),
            "proposed_paragraph_count": sum(paragraph.status == "proposed" for paragraph in self.paragraphs),
            "accepted_paragraph_count": sum(paragraph.status == "accepted" for paragraph in self.paragraphs),
            "zone_count": len(self.zones),
            "title": self.title,
            "abstract_number": abstract_number,
            "abstract_number_block_ids": sorted(excluded_ids),
            "authors": [
                {
                    "initials": author.initials,
                    "surname": author.surname,
                    "display_name": author.display_name,
                    "affiliation_ids": list(author.affiliation_ids),
                }
                for author in self.authors
            ],
            "affiliations": [asdict(aff) for aff in self.affiliations],
            "filtered_block_ids": sorted(excluded_ids),
            "metadata": dict(self.metadata),
        }

    def abstract_number(self) -> str:
        """Return the normalized conference abstract number if one was detected."""

        value = self.metadata.get("abstract_number", "")
        return str(value).strip()

    def abstract_number_block_ids(self) -> set[str]:
        """Return the block ids that contributed to the abstract number."""

        values = self.metadata.get("abstract_number_block_ids", [])
        if not isinstance(values, list):
            return set()
        return {str(value) for value in values}
