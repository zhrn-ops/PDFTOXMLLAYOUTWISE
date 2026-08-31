"""Paragraph-level document model with source-line provenance."""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(slots=True)
class Paragraph:
    """A logical paragraph assembled from one or more extracted text lines."""

    id: str
    page: int
    column: int
    text: str
    bbox: list[float]
    source_line_ids: list[str] = field(default_factory=list)
    role: str = "body"
    status: str = "proposed"
    continued_from: list[str] = field(default_factory=list)
