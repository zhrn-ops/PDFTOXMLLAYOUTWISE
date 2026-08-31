"""Build proposed and user-accepted paragraphs without losing raw PDF lines."""

from __future__ import annotations

from collections import defaultdict

from pdf_to_jats.models.block import TextBlock
from pdf_to_jats.models.paragraph import Paragraph


class ParagraphReconstructor:
    """Reconstruct conservative paragraph candidates from column-aware line order."""

    def propose(self, blocks: list[TextBlock]) -> list[Paragraph]:
        """Return candidates only; this method never changes the source blocks."""

        proposals: list[Paragraph] = []
        for page, page_blocks in self._pages(blocks).items():
            for column, column_blocks in self._columns(page_blocks):
                proposals.extend(self._paragraphs_for_column(page, column, column_blocks))
        return proposals

    def accepted_from_blocks(self, blocks: list[TextBlock]) -> list[Paragraph]:
        """Create exportable paragraphs from explicit user merge operations."""

        accepted: list[Paragraph] = []
        for block in blocks:
            metadata = block.metadata or {}
            if not metadata.get("is_paragraph_unit"):
                continue
            source_ids = [str(value) for value in metadata.get("source_line_ids", [])]
            accepted.append(
                Paragraph(
                    id=f"paragraph_{block.id}",
                    page=block.page,
                    column=int(metadata.get("column", 0)),
                    text=" ".join(block.text.split()),
                    bbox=list(block.bbox),
                    source_line_ids=source_ids or [block.id],
                    role="body" if block.role == "unclassified" else block.role,
                    status="accepted",
                    continued_from=[str(value) for value in metadata.get("continuation_from", [])],
                )
            )
        return sorted(accepted, key=lambda item: (item.page, item.column, item.bbox[1], item.bbox[0]))

    def _pages(self, blocks: list[TextBlock]) -> dict[int, list[TextBlock]]:
        pages: dict[int, list[TextBlock]] = defaultdict(list)
        for block in blocks:
            pages[block.page].append(block)
        return dict(pages)

    def _columns(self, blocks: list[TextBlock]) -> list[tuple[int, list[TextBlock]]]:
        """Split strongly separated left edges into reading columns."""

        ordered = sorted(blocks, key=lambda item: (item.x, item.y))
        columns: list[list[TextBlock]] = []
        for block in ordered:
            if not columns:
                columns.append([block])
                continue
            previous = columns[-1]
            anchor = sum(item.x for item in previous) / len(previous)
            typical_width = max(1.0, sum(item.width for item in previous) / len(previous))
            if block.x - anchor > typical_width * 0.55:
                columns.append([block])
            else:
                previous.append(block)
        return [(index, sorted(column, key=lambda item: (item.y, item.x))) for index, column in enumerate(columns)]

    def _paragraphs_for_column(self, page: int, column: int, blocks: list[TextBlock]) -> list[Paragraph]:
        groups: list[list[TextBlock]] = []
        for block in blocks:
            if not groups or not self._same_paragraph(groups[-1][-1], block):
                groups.append([block])
            else:
                groups[-1].append(block)
        return [self._make_paragraph(page, column, index, group) for index, group in enumerate(groups, start=1)]

    def _same_paragraph(self, first: TextBlock, second: TextBlock) -> bool:
        if first.bold != second.bold or first.italic != second.italic:
            return False
        if abs(first.font_size - second.font_size) > 1.5:
            return False
        gap = max(0.0, second.y - (first.y + first.height))
        if gap > max(first.height, second.height) * 1.5:
            return False
        return second.x <= first.x + max(first.width, second.width) * 0.2

    def _make_paragraph(self, page: int, column: int, index: int, blocks: list[TextBlock]) -> Paragraph:
        bbox = [
            min(block.bbox[0] for block in blocks), min(block.bbox[1] for block in blocks),
            max(block.bbox[2] for block in blocks), max(block.bbox[3] for block in blocks),
        ]
        return Paragraph(
            id=f"proposal_p{page:03d}_c{column:02d}_{index:03d}",
            page=page,
            column=column,
            text=" ".join(" ".join(block.text.split()) for block in blocks),
            bbox=bbox,
            source_line_ids=[block.id for block in blocks],
        )
