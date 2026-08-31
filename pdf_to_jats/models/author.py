"""Author model."""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(slots=True)
class Author:
    """Scientific article author."""

    initials: str = ""
    surname: str = ""
    display_name: str = ""
    affiliation_ids: list[str] = field(default_factory=list)
