"""Heuristics for merging adjacent sub-boxes into larger reading units."""

from __future__ import annotations

from dataclasses import replace
import logging
import re
from typing import Any

from pdf_to_jats.models.block import TextBlock

LOGGER = logging.getLogger(__name__)


class BlockMerger:
    """Merge neighboring text sub-boxes that likely belong to the same logical block."""

    def __init__(
        self,
        vertical_gap_ratio: float = 0.9,
        horizontal_overlap_ratio: float = 0.5,
        max_font_size_delta: float = 1.5,
    ) -> None:
        self.vertical_gap_ratio = vertical_gap_ratio
        self.horizontal_overlap_ratio = horizontal_overlap_ratio
        self.max_font_size_delta = max_font_size_delta

    def merge(self, blocks: list[TextBlock]) -> list[TextBlock]:
        """Merge blocks on the same page when layout and typography are compatible."""

        if not blocks:
            return []

        merged: list[TextBlock] = []
        page_groups: dict[int, list[TextBlock]] = {}
        for block in blocks:
            page_groups.setdefault(block.page, []).append(block)

        for page in sorted(page_groups):
            page_blocks = sorted(page_groups[page], key=lambda b: (b.y, b.x))
            merged.extend(self._merge_page_blocks(page_blocks))

        LOGGER.info("Merged %d raw blocks into %d blocks", len(blocks), len(merged))
        return merged

    def _merge_page_blocks(self, blocks: list[TextBlock]) -> list[TextBlock]:
        if not blocks:
            return []

        merged: list[TextBlock] = [blocks[0]]
        for block in blocks[1:]:
            previous = merged[-1]
            if self._should_merge(previous, block):
                merged[-1] = self._merge_pair(previous, block)
            else:
                merged.append(block)
        return merged

    def _should_merge(self, first: TextBlock, second: TextBlock) -> bool:
        if first.page != second.page:
            return False
        if abs(first.font_size - second.font_size) > self.max_font_size_delta:
            return False
        if first.bold != second.bold or first.italic != second.italic:
            return False

        vertical_gap = max(0.0, second.y - (first.y + first.height))
        gap_threshold = max(first.height, second.height) * self.vertical_gap_ratio
        if first.alignment == second.alignment == "left":
            gap_threshold *= 1.8
        if vertical_gap > gap_threshold:
            return False

        overlap = self._horizontal_overlap_ratio(first, second)
        if overlap >= self.horizontal_overlap_ratio:
            return True
        if vertical_gap <= max(first.height, second.height) * 0.4 and self._looks_like_continuation(first, second):
            return True
        if vertical_gap <= max(first.height, second.height) * 0.8 and self._looks_like_numbered_sequence(first, second):
            return True
        return vertical_gap <= max(first.height, second.height) * 0.5 and self._looks_like_wrapped_line(first, second)

    def _merge_pair(self, first: TextBlock, second: TextBlock) -> TextBlock:
        bbox = [
            min(first.bbox[0], second.bbox[0]),
            min(first.bbox[1], second.bbox[1]),
            max(first.bbox[2], second.bbox[2]),
            max(first.bbox[3], second.bbox[3]),
        ]
        text_parts = [first.text.rstrip(), second.text.lstrip()]
        merged_text = "\n".join(part for part in text_parts if part)
        metadata: dict[str, Any] = dict(first.metadata)
        metadata.setdefault("merged_block_ids", []).append(second.id)
        metadata["merged_from"] = [first.id, second.id]
        metadata["merge_count"] = int(first.metadata.get("merge_count", 1)) + int(second.metadata.get("merge_count", 1))

        return replace(
            first,
            text=merged_text,
            bbox=bbox,
            x=min(first.x, second.x),
            y=min(first.y, second.y),
            width=max(bbox[2] - bbox[0], first.width, second.width),
            height=max(bbox[3] - bbox[1], first.height, second.height),
            metadata=metadata,
        )

    def _horizontal_overlap_ratio(self, first: TextBlock, second: TextBlock) -> float:
        left = max(first.bbox[0], second.bbox[0])
        right = min(first.bbox[2], second.bbox[2])
        overlap = max(0.0, right - left)
        width = max(1.0, min(first.width, second.width))
        return overlap / width

    def _looks_like_continuation(self, first: TextBlock, second: TextBlock) -> bool:
        if not first.text or not second.text:
            return False
        if first.text.rstrip().endswith("-"):
            return True
        if len(first.text.split()) < 4 and len(second.text.split()) < 4:
            return True
        if first.text.rstrip().endswith((";", ":", ",")) and second.text[:1].islower():
            return True
        return False

    def _looks_like_numbered_sequence(self, first: TextBlock, second: TextBlock) -> bool:
        first_text = first.text.strip()
        second_text = second.text.strip()
        if not first_text or not second_text:
            return False
        if re.match(r"^\d+\s", first_text) and re.match(r"^\d+\s", second_text):
            return True
        if re.search(r"[\d;:,]\s*$", first_text) and re.match(r"^\d+\s", second_text):
            return True
        return False

    def _looks_like_wrapped_line(self, first: TextBlock, second: TextBlock) -> bool:
        first_text = first.text.strip()
        second_text = second.text.strip()
        if not first_text or not second_text:
            return False
        if not first_text.endswith((".", ":", ";", "-", ")", ",")) and second_text[:1].islower():
            return True
        if first.alignment == second.alignment == "left" and second.y > first.y:
            if second.x <= first.x + max(first.width, second.width) * 0.2:
                return True
        return False
