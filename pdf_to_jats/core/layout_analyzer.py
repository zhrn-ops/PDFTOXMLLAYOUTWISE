"""Feature extraction for layout analysis."""

from __future__ import annotations

from dataclasses import dataclass

from pdf_to_jats.models.block import TextBlock


@dataclass(slots=True)
class BlockFeatures:
    """Derived features used for semantic classification."""

    position_top: float
    position_bottom: float
    centered: bool
    left_aligned: bool
    font_size_ratio: float
    bold: bool
    italic: bool
    word_count: int
    sentence_count: int
    uppercase_ratio: float
    numbering_pattern: bool


class LayoutAnalyzer:
    """Compute simple features from extracted blocks."""

    def extract_features(self, block: TextBlock, body_font_size: float = 10.0, page_height: float = 800.0) -> BlockFeatures:
        text = block.text.strip()
        words = text.split()
        return BlockFeatures(
            position_top=block.y / max(page_height, 1.0),
            position_bottom=(page_height - (block.y + block.height)) / max(page_height, 1.0),
            centered=block.alignment == "center",
            left_aligned=block.alignment == "left",
            font_size_ratio=block.font_size / max(body_font_size, 1.0),
            bold=block.bold,
            italic=block.italic,
            word_count=len(words),
            sentence_count=text.count(".") + text.count("!") + text.count("?"),
            uppercase_ratio=(sum(1 for c in text if c.isupper()) / max(sum(1 for c in text if c.isalpha()), 1)),
            numbering_pattern=bool(text[:8].strip() and text[:8].strip()[0].isdigit()),
        )

