"""Document model and JSON serialization helpers."""

from __future__ import annotations

from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Any
import json

from pdf_to_jats.models.affiliation import Affiliation
from pdf_to_jats.models.author import Author
from pdf_to_jats.models.block import TextBlock, reading_order_key
from pdf_to_jats.models.paragraph import Paragraph


REFINE_ROLES = frozenset(
    {"title", "author", "corresponding_author", "affiliation", "abstract", "abstract_number", "keywords"}
)


def segment_reading_bounds(segment: dict[str, Any] | None) -> tuple[int | None, int | None]:
    """Return the reading-order bounds a manually split article was given.

    Conference PDFs print several complete articles on one page, sometimes
    stacked inside the same column, so neither the page range nor the column of
    a segment can separate them. A manual split therefore records the
    document-wide reading position of the article's first line (``start_order``)
    and of its last line (``end_order``); either bound may be missing, meaning
    the article runs to the start or the end of the document.
    """

    if not segment:
        return None, None

    def bound(key: str) -> int | None:
        value = segment.get(key)
        if value is None:
            return None
        try:
            return int(value)
        except (TypeError, ValueError):
            return None

    return bound("start_order"), bound("end_order")


def segment_is_manual(segment: dict[str, Any] | None) -> bool:
    """Return whether a segment was created by hand rather than detected."""

    start_order, end_order = segment_reading_bounds(segment)
    return start_order is not None or end_order is not None


def block_matches_segment(block: TextBlock, segment: dict[str, Any] | None) -> bool:
    """Return whether a block belongs to an article segment.

    A page range alone cannot tell apart two articles that share a page, so
    the block's recorded column is matched against the segment's columns when
    both are known. Blocks without column information still match by page, and
    a segment without columns matches every block in its page range.

    Reading-order bounds narrow that result further: a segment created by hand
    only owns the blocks between its two bounds, which is the only way to keep
    articles that share a page *and* a column apart. Bounds are ignored for a
    block that carries no reading position, because dropping such a block would
    silently lose content.
    """

    if segment is None:
        return False
    start_page = int(segment.get("start_page", 1))
    end_page = int(segment.get("end_page", start_page))
    if not start_page <= int(block.page) <= end_page:
        return False
    columns = segment.get("columns")
    block_column = block.metadata.get("column") if block.metadata else None
    if columns is not None and block_column is not None:
        if int(block_column) not in {int(column) for column in columns}:
            return False
    # Full-width lines (mastheads, abstract-number markers) have no column and
    # are attributed to the segment whose range contains them.
    start_order, end_order = segment_reading_bounds(segment)
    if start_order is None and end_order is None:
        return True
    order = (block.metadata or {}).get("reading_order")
    if order is None:
        return True
    try:
        position = int(order)
    except (TypeError, ValueError):
        return True
    if start_order is not None and position < start_order:
        return False
    if end_order is not None and position > end_order:
        return False
    return True


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
    corresponding_author: Author | None = None
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
        for block in sorted(self.blocks, key=reading_order_key):
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
                    "given_names": author.given_names,
                    "surname": author.surname,
                    "display_name": author.display_name,
                    "affiliation_ids": list(author.affiliation_ids),
                }
                for author in self.authors
            ],
            "affiliations": [asdict(aff) for aff in self.affiliations],
            "link_issues": list(self.metadata.get("link_issues", [])),
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
