"""Block-level document model."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(slots=True)
class TextBlock:
    """Structured representation of a PDF text block."""

    id: str
    page: int
    text: str
    bbox: list[float]
    x: float
    y: float
    width: float
    height: float
    font_name: str = ""
    font_size: float = 0.0
    bold: bool = False
    italic: bool = False
    color: str = ""
    alignment: str = ""
    confidence: float = 0.0
    role: str = "unclassified"
    metadata: dict[str, Any] = field(default_factory=dict)

