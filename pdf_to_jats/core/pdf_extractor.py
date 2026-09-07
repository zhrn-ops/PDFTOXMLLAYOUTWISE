"""PDF extraction using PyMuPDF."""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path
import logging
import re
from difflib import SequenceMatcher
from typing import Any

import fitz

from pdf_to_jats.models.block import TextBlock
from pdf_to_jats.models.document import Document

LOGGER = logging.getLogger(__name__)


class PDFExtractor:
    """Extract text, images, and layout information from a PDF."""

    def __init__(
        self,
        visible_text_only: bool = True,
        dark_threshold: int = 240,
        use_docling: bool = True,
    ) -> None:
        self.visible_text_only = visible_text_only
        self.dark_threshold = dark_threshold
        self.use_docling = use_docling

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

                        block_id = f"block_{page_index:03d}_{len(document.blocks) + 1:03d}"
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
            first_number = abstract_numbers[0]["abstract_number"]
            document.metadata["abstract_number"] = first_number
            document.metadata["abstract_number_block_ids"] = [abstract_numbers[0]["block_id"]]
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

    def _extract_abstract_number(self, blocks: list[TextBlock]) -> tuple[str, list[str]]:
        """Find a conference abstract number from the earliest content blocks."""

        page_one_blocks = [block for block in sorted(blocks, key=lambda item: (item.page, item.y, item.x)) if block.page == 1]
        page_one_text = " ".join(" ".join(block.text.replace("\n", " ").split()) for block in page_one_blocks)
        if not page_one_text:
            return "", []

        patterns = [
            r"^\s*([A-Z0-9][A-Z0-9./-]{1,30})\s*(?:\||\u2502)\s*",
            r"^\s*(\d{3,6})\s*$",
            r"\b(?:abstract\s*(?:no\.?|number|nr\.?|id)?|absn)\s*[:#-]?\s*([A-Z0-9][A-Z0-9./-]{1,30})\b",
        ]
        for pattern in patterns:
            for block in page_one_blocks:
                cleaned = " ".join(block.text.replace("\n", " ").split())
                match = re.search(pattern, cleaned, flags=re.IGNORECASE)
                if match:
                    value = match.group(1).strip(" ,;:.-")
                    if value and (pattern.startswith("^\\s*(\\d{3,6})") or re.fullmatch(r"[A-Z0-9][A-Z0-9./-]{1,30}", value)):
                        return value, [block.id]
        return "", []

    def _extract_abstract_numbers(self, blocks: list[TextBlock]) -> list[dict[str, Any]]:
        """Find distinct abstract-number markers across the complete PDF."""

        markers: list[dict[str, Any]] = []
        seen: set[str] = set()
        patterns = (
            r"^\s*([A-Z0-9][A-Z0-9./-]{1,30})\s*(?:\||\u2502)\s*",
            r"\b(?:abstract\s*(?:no\.?|number|nr\.?|id)?|absn)\s*[:#-]?\s*([A-Z0-9][A-Z0-9./-]{1,30})\b",
        )
        for block in sorted(blocks, key=lambda item: (item.page, item.y, item.x)):
            cleaned = " ".join(block.text.replace("\n", " ").split())
            for pattern in patterns:
                match = re.search(pattern, cleaned, flags=re.IGNORECASE)
                if not match:
                    continue
                value = match.group(1).strip(" ,;:.-")
                key = value.casefold()
                if value and key not in seen:
                    markers.append({"abstract_number": value, "page": block.page, "block_id": block.id})
                    seen.add(key)
                break
        return markers

    def _build_article_segments(
        self, markers: list[dict[str, Any]], page_count: int
    ) -> list[dict[str, Any]]:
        """Create page ranges beginning at each distinct abstract marker."""

        segments: list[dict[str, Any]] = []
        for index, marker in enumerate(markers, start=1):
            next_page = markers[index]["page"] if index < len(markers) else page_count + 1
            segments.append(
                {
                    "index": index,
                    "abstract_number": marker["abstract_number"],
                    "start_page": marker["page"],
                    "end_page": max(marker["page"], next_page - 1),
                    "abstract_number_block_id": marker["block_id"],
                }
            )
        return segments

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
