"""Affiliation model."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(slots=True)
class Affiliation:
    """Scientific article affiliation."""

    id: str
    text: str

