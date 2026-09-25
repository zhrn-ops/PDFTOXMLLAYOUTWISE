"""Affiliation model."""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(slots=True)
class Affiliation:
    """Scientific article affiliation.

    ``id`` is the stable reference used by ``Author.affiliation_ids``: the
    printed marker when one exists (``"1"``, ``"*"``, ``"a"``) and a
    synthesized ``aff_N`` key otherwise. ``markers`` collects every marker that
    points here, since one institution may be printed with several of them.
    """

    id: str
    text: str
    marker: str = ""
    raw_marker: str = ""
    markers: list[str] = field(default_factory=list)

