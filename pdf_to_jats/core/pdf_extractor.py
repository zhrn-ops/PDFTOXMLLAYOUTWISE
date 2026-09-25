"""PDF extraction using PyMuPDF."""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path
import logging
import re
from difflib import SequenceMatcher
from typing import Any

import fitz

from pdf_to_jats.models.block import TextBlock, reading_order_key
from pdf_to_jats.models.document import Document

LOGGER = logging.getLogger(__name__)


class PDFExtractor:
    """Extract text, images, and layout information from a PDF."""

    # A vertical whitespace gutter this wide (in points, and as a fraction of
    # the page width) separates the columns of a multi-column page.
    COLUMN_GUTTER_MIN_POINTS = 8.0
    COLUMN_GUTTER_MIN_RATIO = 0.02
    # Guards against splitting a single-column page on one stray gap.
    COLUMN_MIN_BLOCKS = 4
    COLUMN_MIN_BLOCK_RATIO = 0.10
    COLUMN_MIN_Y_OVERLAP = 0.30

    def __init__(
        self,
        visible_text_only: bool = True,
        dark_threshold: int = 240,
        use_docling: bool = True,
    ) -> None:
        self.visible_text_only = visible_text_only
        self.dark_threshold = dark_threshold
        self.use_docling = use_docling
        # Marker blocks from the last extract() call, used to infer the column
        # each article segment owns.
        self._marker_blocks: list[TextBlock] = []

    def extract(self, pdf_path: str | Path) -> Document:
        path = Path(pdf_path)
        if not path.exists():
            raise FileNotFoundError(path)

        document = Document(metadata={"source_pdf": str(path)})
        hidden_text_line_count = 0
        docling_items, docling_status = self._extract_docling_structure(path)
        document.metadata.update(docling_status)
        with fitz.open(path) as pdf:
            for page_index, page in enumerate(pdf, start=1):
                visible_area = self._visible_page_rect(page)
                text_dict = page.get_text("dict", clip=visible_area)
                page_pixmap = self._render_visible_page(page, visible_area) if self.visible_text_only else None
                page_blocks: list[TextBlock] = []
                for block in text_dict.get("blocks", []):
                    if block.get("type") != 0:
                        continue

                    bbox = fitz.Rect(block.get("bbox", [0, 0, 0, 0]))
                    if not bbox.intersects(visible_area):
                        continue

                    lines = block.get("lines", [])

                    for line_index, line in enumerate(lines, start=1):
                        line_bbox = fitz.Rect(line.get("bbox", bbox))
                        if not line_bbox.intersects(visible_area):
                            continue
                        spans = line.get("spans", [])
                        text = self._line_text(spans)
                        if not text.strip():
                            continue
                        if page_pixmap is not None and not self._block_is_human_visible(line_bbox, page_pixmap, visible_area):
                            hidden_text_line_count += 1
                            continue

                        font_name = spans[0].get("font", "") if spans else ""
                        font_size = float(spans[0].get("size", 0.0)) if spans else 0.0
                        flags = int(spans[0].get("flags", 0)) if spans else 0
                        color = str(spans[0].get("color", "")) if spans else ""

                        # The final id is assigned once the page is put into
                        # reading order, so this is only a placeholder.
                        block_id = f"pending_{page_index:03d}_{len(page_blocks) + 1:03d}"
                        extracted = TextBlock(
                            id=block_id,
                            page=page_index,
                            text=text.strip(),
                            bbox=[float(line_bbox.x0), float(line_bbox.y0), float(line_bbox.x1), float(line_bbox.y1)],
                            x=float(line_bbox.x0),
                            y=float(line_bbox.y0),
                            width=float(line_bbox.width),
                            height=float(line_bbox.height),
                            font_name=font_name,
                            font_size=font_size,
                            bold=bool(flags & 2),
                            italic=bool(flags & 1),
                            color=color,
                            alignment=self._infer_line_alignment(line, visible_area),
                            metadata={
                                "raw_block": block,
                                "raw_line": line,
                                "line_index": line_index,
                                "visible_area": [float(visible_area.x0), float(visible_area.y0), float(visible_area.x1), float(visible_area.y1)],
                                "visible_text_only": self.visible_text_only,
                                "noise_candidate": self._is_noise_candidate(text),
                            },
                        )
                        self._attach_docling_evidence(extracted, docling_items)
                        page_blocks.append(extracted)

                # PyMuPDF returns lines in content-stream order, which
                # interleaves the columns of a two-column journal page. Restore
                # reading order before the rest of the pipeline sees them.
                for extracted in self._order_page_blocks(page_blocks, visible_area):
                    extracted.id = f"block_{page_index:03d}_{len(document.blocks) + 1:03d}"
                    extracted.metadata["reading_order"] = len(document.blocks)
                    document.blocks.append(extracted)

                for image_index, image in enumerate(page.get_images(full=True), start=1):
                    document.figures.append(
                        {
                            "id": f"image_{page_index:03d}_{image_index:03d}",
                            "page": page_index,
                            "xref": image[0],
                            "bbox": None,
                        }
                    )

        document.metadata["extracted_text_line_count"] = len(document.blocks)
        document.metadata["hidden_text_line_count"] = hidden_text_line_count
        # The UI may replace display blocks during review; keep extraction immutable.
        document.raw_blocks = [replace(block, metadata=dict(block.metadata)) for block in document.blocks]
        abstract_numbers = self._extract_abstract_numbers(document.blocks)
        if abstract_numbers:
            first_marker = abstract_numbers[0]
            document.metadata["abstract_number"] = first_marker["abstract_number"]
            # Only a block holding nothing but the marker may be dropped from the
            # article body; an inline marker such as "(S100) PHASE 3 ..." lives
            # inside the title line and must stay.
            marker_block = next(
                (block for block in document.blocks if block.id == first_marker["block_id"]), None
            )
            document.metadata["abstract_number_block_ids"] = (
                [first_marker["block_id"]]
                if marker_block is not None
                and self._is_abstract_number_only(marker_block.text, first_marker["abstract_number"])
                else []
            )
            document.metadata["article_segments"] = self._build_article_segments(
                abstract_numbers,
                max((block.page for block in document.blocks), default=1),
            )
        elif "abstract_number" not in document.metadata:
            document.metadata["abstract_number"] = "ABSN"
        LOGGER.info("Extracted %d blocks from %s", len(document.blocks), path.name)
        return document

    def _extract_docling_structure(self, path: Path) -> tuple[dict[int, list[dict[str, Any]]], dict[str, Any]]:
        """Extract semantic structure without making it the spatial source of truth."""

        if not self.use_docling:
            return {}, {"docling_enabled": False, "docling_status": "disabled"}

        try:
            from docling.document_converter import DocumentConverter
        except ImportError:
            LOGGER.info("Docling is not installed; continuing with PyMuPDF only")
            return {}, {"docling_enabled": True, "docling_status": "unavailable"}

        try:
            result = DocumentConverter().convert(str(path))
            items_by_page: dict[int, list[dict[str, Any]]] = {}
            for item, _level in result.document.iterate_items():
                text = str(getattr(item, "text", "") or "").strip()
                label = getattr(getattr(item, "label", None), "value", None) or str(getattr(item, "label", ""))
                if not text or not label:
                    continue
                for provenance in getattr(item, "prov", []) or []:
                    page = int(getattr(provenance, "page_no", 0) or 0)
                    if page < 1:
                        continue
                    items_by_page.setdefault(page, []).append(
                        {
                            "label": label,
                            "text": text,
                            "item_type": type(item).__name__,
                        }
                    )
            return items_by_page, {
                "docling_enabled": True,
                "docling_status": "ok",
                "docling_item_count": sum(len(items) for items in items_by_page.values()),
            }
        except Exception as error:  # Docling is advisory; PyMuPDF must remain available.
            LOGGER.warning("Docling structure extraction failed: %s", error)
            return {}, {"docling_enabled": True, "docling_status": "error", "docling_error": str(error)}

    def _attach_docling_evidence(
        self, block: TextBlock, items_by_page: dict[int, list[dict[str, Any]]]
    ) -> None:
        """Attach the best structural match to a PyMuPDF line."""

        candidates = items_by_page.get(block.page, [])
        if not candidates:
            return
        normalized_block = self._normalize_text(block.text)
        best_match: dict[str, Any] | None = None
        best_score = 0.0
        for candidate in candidates:
            normalized_item = self._normalize_text(str(candidate["text"]))
            if normalized_block in normalized_item or normalized_item in normalized_block:
                score = 1.0
            else:
                score = SequenceMatcher(None, normalized_block, normalized_item).ratio()
            if score > best_score:
                best_score = score
                best_match = candidate
        if best_match is not None and best_score >= 0.45:
            block.metadata["docling"] = {
                "label": best_match["label"],
                "item_type": best_match["item_type"],
                "match_score": round(best_score, 3),
                "text": best_match["text"],
            }

    def _normalize_text(self, text: str) -> str:
        return " ".join(text.casefold().split())

    def _visible_page_rect(self, page: fitz.Page) -> fitz.Rect:
        """Return the region that should be treated as visible content."""

        cropbox = fitz.Rect(page.cropbox)
        rect = fitz.Rect(page.rect)
        if cropbox.is_empty or cropbox.width <= 0 or cropbox.height <= 0:
            return rect
        if cropbox.intersects(rect):
            return cropbox & rect
        return rect

    def _line_text(self, spans: list[dict[str, Any]]) -> str:
        return "".join(span.get("text", "") for span in spans)

    def _is_noise_candidate(self, text: str) -> bool:
        """Flag likely noise without deleting it from the editable document."""

        cleaned = " ".join(text.replace("\u00a0", " ").replace("\ufeff", " ").split()).strip()
        if not cleaned:
            return True
        lowered = cleaned.lower()
        if cleaned in {"|", "||", "·", "•", "©", "®"}:
            return True
        if re.fullmatch(r"[^\w]+", cleaned):
            return True
        if "copyright" in lowered or "all rights reserved" in lowered:
            return True
        if lowered.startswith("the authors.") or "published by" in lowered:
            return True
        if cleaned.upper() in {"ABSTRACT", "A B S T R A C T"}:
            return True
        if re.fullmatch(r"doi:\s*\S+.*", lowered):
            return True
        if len(cleaned) <= 2 and not any(ch.isalnum() for ch in cleaned):
            return True
        return False

    @staticmethod
    def _is_plausible_abstract_number(value: str) -> bool:
        """Return true only for tokens that look like a conference identifier.

        Abstract numbers such as ``S100``, ``4349``, or ``P12.3`` carry a digit.
        Stray parenthesized words from a title or affiliation (``(POM)``,
        ``(Ichilov)``) and prose like ``Abstract Book`` do not, and must not be
        mistaken for markers that split one PDF into several articles.
        """

        if not value or not (value[0].isupper() or value[0].isdigit()):
            return False
        if not re.fullmatch(r"[A-Z0-9][A-Z0-9./-]{1,30}", value, flags=re.IGNORECASE):
            return False
        return any(character.isdigit() for character in value)

    @staticmethod
    def _is_abstract_number_only(text: str, abstract_number: str) -> bool:
        """Return true when a block holds nothing but the abstract-number marker."""

        remainder = re.sub(
            r"(?:abstract\s*(?:no\.?|number|nr\.?|id)?|absn)\s*[:#-]?",
            " ",
            text,
            flags=re.IGNORECASE,
        )
        remainder = re.sub(re.escape(abstract_number), " ", remainder, flags=re.IGNORECASE)
        return not re.search(r"[A-Za-z0-9]", remainder)

    def _extract_abstract_number(self, blocks: list[TextBlock]) -> tuple[str, list[str]]:
        """Find a conference abstract number from the earliest content blocks."""

        page_one_blocks = [block for block in sorted(blocks, key=reading_order_key) if block.page == 1]
        page_one_text = " ".join(" ".join(block.text.replace("\n", " ").split()) for block in page_one_blocks)
        if not page_one_text:
            return "", []

        patterns = [
            r"^\s*\(([A-Z][A-Z0-9./-]{1,30})\)\s+",
            r"^\s*(?=[A-Z0-9./-]{1,30}\d)([A-Z0-9][A-Z0-9./-]{1,30})\s*(?:\||\u2502)\s*",
            r"^\s*(\d{3,6})\s*$",
            r"\b(?:abstract\s*(?:no\.?|number|nr\.?|id)?|absn)\s*[:#-]?\s*([A-Z0-9][A-Z0-9./-]{1,30})\b",
        ]
        for pattern in patterns:
            for block in page_one_blocks:
                cleaned = " ".join(block.text.replace("\n", " ").split())
                match = re.search(pattern, cleaned, flags=re.IGNORECASE)
                if match:
                    value = match.group(1).strip(" ,;:.-")
                    if self._is_plausible_abstract_number(value):
                        return value, [block.id]
        return "", []

    def _extract_abstract_numbers(self, blocks: list[TextBlock]) -> list[dict[str, Any]]:
        """Find distinct abstract-number markers across the complete PDF."""

        markers: list[dict[str, Any]] = []
        seen: set[str] = set()
        patterns = (
            r"^\s*\(([A-Z][A-Z0-9./-]{1,30})\)\s+",
            r"^\s*(?=[A-Z0-9./-]{1,30}\d)([A-Z0-9][A-Z0-9./-]{1,30})\s*(?:\||\u2502)\s*",
            r"\b(?:abstract\s*(?:no\.?|number|nr\.?|id)?|absn)\s*[:#-]?\s*([A-Z0-9][A-Z0-9./-]{1,30})\b",
        )
        for block in sorted(blocks, key=reading_order_key):
            cleaned = " ".join(block.text.replace("\n", " ").split())
            for pattern in patterns:
                match = re.search(pattern, cleaned, flags=re.IGNORECASE)
                if not match:
                    continue
                value = match.group(1).strip(" ,;:.-")
                key = value.casefold()
                if self._is_plausible_abstract_number(value) and key not in seen:
                    markers.append({"abstract_number": value, "page": block.page, "block_id": block.id})
                    seen.add(key)
                break
        # _build_article_segments needs the marker blocks' column metadata.
        self._marker_blocks = blocks
        return markers

    def _build_article_segments(
        self, markers: list[dict[str, Any]], page_count: int
    ) -> list[dict[str, Any]]:
        """Create page ranges beginning at each distinct abstract marker.

        Conference journals lay out several abstracts side by side on one page,
        so each segment also records the columns it spans. Column-less blocks
        (full-width headers, abstract-number lines) match by page alone.
        """

        segments: list[dict[str, Any]] = []
        blocks_by_id = {block.id: block for block in self._marker_blocks}
        marker_blocks_by_page: dict[int, list[TextBlock]] = {}
        for block in self._marker_blocks:
            marker_blocks_by_page.setdefault(int(block.page), []).append(block)

        for index, marker in enumerate(markers, start=1):
            next_page = markers[index]["page"] if index < len(markers) else page_count + 1
            marker_block = blocks_by_id.get(marker["block_id"])
            columns = self._segment_columns(
                marker_block, marker_blocks_by_page.get(int(marker["page"]), [])
            )
            segments.append(
                {
                    "index": index,
                    "abstract_number": marker["abstract_number"],
                    "start_page": marker["page"],
                    "end_page": max(marker["page"], next_page - 1),
                    "abstract_number_block_id": marker["block_id"],
                    "columns": columns,
                }
            )
        return segments

    def _segment_columns(
        self, marker_block: TextBlock | None, page_marker_blocks: list[TextBlock]
    ) -> list[int] | None:
        """Infer which column one segment owns from its abstract-number marker.

        A marker is a line at the very top of an article's column, so its own
        column assignment is the strongest signal. When another marker on the
        same page claims the same column, the column has no consistent owner
        and matching falls back to the page range for every segment there.
        """

        if marker_block is None:
            return None
        own_column = marker_block.metadata.get("column")
        if own_column is None:
            return None
        own_column = int(own_column)
        for other in page_marker_blocks:
            if other is marker_block:
                continue
            other_column = other.metadata.get("column")
            if other_column is not None and int(other_column) == own_column:
                return None
        return [own_column]

    def _block_is_human_visible(self, bbox: fitz.Rect, pixmap: fitz.Pixmap, visible_area: fitz.Rect) -> bool:
        """Return whether a text line has rendered ink in its own bounding box."""

        if bbox.width <= 0 or bbox.height <= 0:
            return False

        scale_x = pixmap.width / max(visible_area.width, 1.0)
        scale_y = pixmap.height / max(visible_area.height, 1.0)
        x0 = max(0, min(pixmap.width - 1, int((bbox.x0 - visible_area.x0) * scale_x)))
        x1 = max(0, min(pixmap.width, int((bbox.x1 - visible_area.x0) * scale_x)))
        y0 = max(0, min(pixmap.height - 1, int((bbox.y0 - visible_area.y0) * scale_y)))
        y1 = max(0, min(pixmap.height, int((bbox.y1 - visible_area.y0) * scale_y)))

        if x1 <= x0 or y1 <= y0:
            return False

        samples = pixmap.samples
        stride = pixmap.stride
        dark_count = 0
        total_count = 0

        for yy in range(y0, y1):
            row_offset = yy * stride
            for xx in range(x0, x1):
                index = row_offset + xx
                if index >= len(samples):
                    continue
                total_count += 1
                if samples[index] < self.dark_threshold:
                    dark_count += 1

        if total_count == 0:
            return False

        # A small line of text can cover less than 1% of its PDF bbox. Require
        # a minimum number of dark pixels as well as a low density threshold.
        dark_ratio = dark_count / total_count
        return dark_count >= 4 and dark_ratio >= 0.003

    def _order_page_blocks(
        self, blocks: list[TextBlock], visible_area: fitz.Rect
    ) -> list[TextBlock]:
        """Return one page's lines in reading order.

        Journal pages place several articles side by side. PyMuPDF emits their
        lines interleaved by vertical position, which merges unrelated articles
        into one abstract and splits author/affiliation lines across columns.
        Detecting the gutter lets one column be read top-to-bottom before the
        next one starts.
        """

        if len(blocks) < 2:
            return list(blocks)
        gutter = self._find_column_gutter(blocks, visible_area)
        if gutter is None:
            return sorted(blocks, key=lambda block: (block.y, block.x))

        left: list[TextBlock] = []
        right: list[TextBlock] = []
        spanning: list[TextBlock] = []
        for block in blocks:
            if block.bbox[2] <= gutter[0] + 0.5:
                block.metadata["column"] = 0
                left.append(block)
            elif block.bbox[0] >= gutter[1] - 0.5:
                block.metadata["column"] = 1
                right.append(block)
            else:
                # Full-width lines keep no column: they span every column.
                spanning.append(block)

        minimum = max(self.COLUMN_MIN_BLOCKS, int(len(blocks) * self.COLUMN_MIN_BLOCK_RATIO))
        if len(left) < minimum or len(right) < minimum:
            return sorted(blocks, key=lambda block: (block.y, block.x))

        left.sort(key=lambda block: (block.y, block.x))
        right.sort(key=lambda block: (block.y, block.x))
        spanning.sort(key=lambda block: (block.y, block.x))

        # Full-width lines above the columns (mastheads, section headings) belong
        # before them; anything lower spans both columns and is kept last.
        first_column_y = min(left[0].y, right[0].y)
        leading = [block for block in spanning if block.y < first_column_y]
        trailing = [block for block in spanning if block.y >= first_column_y]
        return [*leading, *left, *right, *trailing]

    def _find_column_gutter(
        self, blocks: list[TextBlock], visible_area: fitz.Rect
    ) -> tuple[float, float] | None:
        """Locate the vertical whitespace gap between two text columns."""

        page_width = visible_area.width
        if page_width <= 0:
            return None
        intervals = sorted(
            (block.bbox[0], block.bbox[2])
            for block in blocks
            if block.bbox[2] > block.bbox[0]
        )
        if len(intervals) < 2:
            return None

        best: tuple[float, float] | None = None
        cursor = visible_area.x0
        for start, end in intervals:
            if start - cursor > 0:
                middle = (cursor + start) / 2.0
                ratio = (middle - visible_area.x0) / page_width
                if 0.25 <= ratio <= 0.75:
                    if best is None or (start - cursor) > (best[1] - best[0]):
                        best = (cursor, start)
            cursor = max(cursor, end)

        if best is None:
            return None
        gap = best[1] - best[0]
        if gap < max(self.COLUMN_GUTTER_MIN_POINTS, page_width * self.COLUMN_GUTTER_MIN_RATIO):
            return None

        # Real columns run alongside each other, so require overlapping vertical
        # extents; otherwise this is just a ragged right margin.
        left = [block for block in blocks if block.bbox[2] <= best[0] + 0.5]
        right = [block for block in blocks if block.bbox[0] >= best[1] - 0.5]
        if not left or not right:
            return None
        left_span = (min(b.y for b in left), max(b.y + b.height for b in left))
        right_span = (min(b.y for b in right), max(b.y + b.height for b in right))
        overlap = min(left_span[1], right_span[1]) - max(left_span[0], right_span[0])
        shorter = min(left_span[1] - left_span[0], right_span[1] - right_span[0])
        if shorter <= 0 or overlap / shorter < self.COLUMN_MIN_Y_OVERLAP:
            return None
        return best

    def _infer_line_alignment(self, line: dict[str, Any], visible_area: fitz.Rect) -> str:
        bbox = line.get("bbox")
        if not bbox:
            return "unknown"
        x0 = float(bbox[0])
        x1 = float(bbox[2])
        if x1 <= x0:
            return "unknown"
        avg_x = x0 + ((x1 - x0) / 2.0)
        page_center = visible_area.x0 + (visible_area.width / 2.0)
        return "center" if abs(avg_x - page_center) < visible_area.width * 0.15 else "left"
    def _render_visible_page(self, page: fitz.Page, visible_area: fitz.Rect) -> fitz.Pixmap:
        """Render at high resolution so small but visible text still has detectable ink."""

        return page.get_pixmap(matrix=fitz.Matrix(3, 3), clip=visible_area, colorspace=fitz.csGRAY, alpha=False)
