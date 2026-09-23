"""Deterministic role refinement applied after heuristic classification.

The heuristic classifier scores blocks mostly from layout. On real conference
and journal PDFs it systematically mislabels abstract body text, author lines,
and funding/keyword lines as ``title``. This module applies conservative,
text-driven corrections and records where each role came from, so that manual
or LLM roles are never silently overwritten.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from pdf_to_jats.models.block import TextBlock, reading_order_key


def is_abstract_heading(text: str) -> bool:
    """Return True for pure abstract headings such as ``ABSTRACT`` or ``Abstract 4349``."""

    cleaned = " ".join(str(text or "").split())
    return bool(re.fullmatch(r"abstract(?:\s+(?:no\.?\s*)?[\w-]{1,30})?", cleaned, re.IGNORECASE))


# Postal address tails such as "Philadelphia, PA, USA" that arrive as their own
# block when an affiliation wraps across lines.
_COUNTRY_MARKER = re.compile(
    r"\b(?:usa|u\.s\.a\.?|united states|united kingdom|uk|canada|germany|"
    r"france|italy|spain|japan|china|india|australia|brazil|netherlands)\b",
    re.IGNORECASE,
)
_STATE_AFTER_COMMA = re.compile(r",\s*([A-Z]{2})\b")
_US_STATE_CODES = frozenset(
    "AL AK AZ AR CA CO CT DE FL GA HI ID IL IN IA KS KY LA ME MD MA MI MN MS "
    "MO MT NE NV NH NJ NM NY NC ND OH OK OR PA RI SC SD TN TX UT VT VA WA WV WI WY".split()
)


def looks_like_address(text: str) -> bool:
    """Return True for an affiliation/address line such as ``..., PA, USA``.

    These lines are frequently mistaken for author names, so both the role
    refiner and the author linker consult this check as a guard.
    """

    if _COUNTRY_MARKER.search(text):
        return True
    return any(code in _US_STATE_CODES for code in _STATE_AFTER_COMMA.findall(text))


@dataclass(slots=True)
class RefineReport:
    """Summary of the automatic refinement pass."""

    changed: dict[str, str] = field(default_factory=dict)  # block_id -> new role
    review_ids: list[str] = field(default_factory=list)  # blocks to double-check

    @property
    def changed_count(self) -> int:
        return len(self.changed)

    @property
    def review_count(self) -> int:
        return len(self.review_ids)


class AutoRefiner:
    """Fix predictable classifier mistakes and mark blocks needing review."""

    # Line-level content that must never be considered part of the article title.
    _TITLE_STOPWORDS = re.compile(
        r"^(?:keywords?|keyword\s*list|funding|financial support|conflicts? of interest|"
        r"disclosure[s]?|acknowledg(?:e?ments?)?|references|abbreviations?|"
        r"data availability|author contributions?)\b",
        re.IGNORECASE,
    )
    _FUNDING_NOISE = re.compile(
        r"(?:grant\s+(?:no\.?|number|support)|supported\s+by|funded\s+by|"
        r"research\s+was\s+supported|work\s+was\s+supported|nih\s+r\d+)",
        re.IGNORECASE,
    )
    _HEADER_NOISE = re.compile(
        r"^(?:page \d+|\d+\s*\|\s*\S|book\s*\||abstracts?\s*\||poster\s*session)\b",
        re.IGNORECASE,
    )
    _PAGE_HEADER_FRAGMENT = re.compile(
        r"^(?:[A-Z]?\d+\s*[|\u2502]\s*|[\s|\u2502]*[A-Z]?\d+\s*[|\u2502]?\s*)$"
    )
    _GRANT_NUMBER = re.compile(r"\b[A-Z]{1,3}\d{4,}(?:-\d+)?\b")
    # Sentence-like body text: several lowercase words in a row.
    _SENTENCE_LIKE = re.compile(r"(?:[a-z]{3,}[,;]?\s+){4,}[a-z]")
    _DOI_OR_URL = re.compile(r"(?:doi[:\s]|https?://|10\.\d{4,}/)", re.IGNORECASE)
    # "Sarah Smith, Bucknell University" – author + institution shape.
    _AUTHOR_NAME = re.compile(r"^[A-Z][a-zA-Z'’\-]+(?:\s+[A-Z]\.)?\s+[A-Z][a-zA-Z'’\-]+")
    _INSTITUTION_HINT = re.compile(
        r"\b(?:university|institute|college|hospital|laborator(?:y|ies)|center|centre|"
        r"school|academia|foundation|department)\b",
        re.IGNORECASE,
    )
    # "Josephina Vermillion, Joseph Feudale," – comma-separated given+surname list.
    _NAME_COMMA_LIST = re.compile(
        r"^[A-Z][a-zA-Z'’\-]+\s+[A-Z][a-zA-Z'’\-]+(?:,\s*[A-Z][a-zA-Z'’\-]+\s+[A-Z][a-zA-Z'’\-]+)+"
        r"(?:,?\s*(?:and|&)\s*[A-Z][a-zA-Z'’\-]+\s+[A-Z][a-zA-Z'’\-]+)?,?$"
    )
    _KEYWORDS_PREFIX = re.compile(r"^(?:keywords?|key\s*words)\s*[:\-]", re.IGNORECASE)
    _ABSTRACT_HEADING = re.compile(r"^(?:abstract|poster)(?:\s*(?:no\.?|number)?\s*[\w-]+)?$", re.IGNORECASE)
    _SECTION_HEADINGS = re.compile(
        r"^(?:references|acknowledg(?:e?ments?)?|keywords?\b|figure \d+|table \d+)\b",
        re.IGNORECASE,
    )
    # Structured-abstract section labels; they occur inside the abstract.
    _STRUCTURED_LABEL = re.compile(
        r"^(?:results?|conclusions?|methods?|materials and methods|introduction|background|"
        r"objectives?|discussion|aim[s]?|purpose)\b[:\s]",
        re.IGNORECASE,
    )

    def refine(self, blocks: list[TextBlock]) -> RefineReport:
        """Refine roles in place and return a report of what changed."""

        changed: dict[str, str] = {}
        review: list[str] = []
        self._mark_abstract_regions(blocks)

        for block in blocks:
            if block.metadata.get("role_source") == "manual":
                continue  # Never touch user-assigned roles.
            text = " ".join(str(block.text or "").split())
            if not text:
                continue
            lowered = text.lower()

            original_role = block.role
            new_role = original_role

            if original_role == "title":
                new_role = self._refine_title(block, text, lowered)
            elif original_role == "abstract":
                new_role = self._refine_abstract(block, text, lowered)
            elif original_role in {"author", "corresponding_author"}:
                new_role = self._refine_author(block, text, lowered)
            elif original_role == "affiliation":
                new_role = self._refine_affiliation(block, text, lowered)
            elif original_role == "unclassified":
                new_role = self._refine_unclassified(block, text, lowered)

            if new_role != original_role:
                block.role = new_role
                block.metadata["role_source"] = "auto_refine"
                block.metadata["role_before_refine"] = original_role
                changed[block.id] = new_role

            if self._needs_review(text, lowered):
                review.append(block.id)

        return RefineReport(changed=changed, review_ids=review)

    # ------------------------------------------------------------------
    # Per-role corrections
    # ------------------------------------------------------------------

    def _refine_title(self, block: TextBlock, text: str, lowered: str) -> str:
        if self._ABSTRACT_HEADING.fullmatch(text.strip()):
            return "abstract"
        if self._HEADER_NOISE.match(text):
            return "unclassified"
        # Funding / DOI / keyword / reference noise is never a title.
        if self._FUNDING_NOISE.search(text) or self._DOI_OR_URL.search(text):
            return "unclassified"
        if self._TITLE_STOPWORDS.match(text) or self._KEYWORDS_PREFIX.match(text):
            return "unclassified"
        # Affiliations and postal addresses are never titles.
        if looks_like_address(text):
            return "affiliation"
        if self._GRANT_NUMBER.search(text) and len(text) <= 80:
            return "unclassified"
        # Page-header fragments such as "S120\u2003|" or "|\u2003S127".
        if self._PAGE_HEADER_FRAGMENT.match(text):
            return "unclassified"
        # "Sarah Smith, Bucknell University" and wrapped author lists are
        # authors even when they sit inside the abstract region.
        if self._looks_like_author_line(text):
            return "author"
        # Abstract body text scored as title: reclaim everything below an
        # ABSTRACT heading that does not look like an actual title line.
        if self._near_abstract_heading(block) and not self._is_title_like(text):
            return "abstract"
        if self._SENTENCE_LIKE.search(text) and not self._is_title_like(text):
            return "unclassified"
        return "title"

    def _refine_abstract(self, block: TextBlock, text: str, lowered: str) -> str:
        return "abstract"

    def _refine_author(self, block: TextBlock, text: str, lowered: str) -> str:
        # Wrapped address tails are affiliation text, not people. This is what
        # previously produced "authors" such as "Los Angeles" and "San Diego".
        if looks_like_address(text):
            return "affiliation"
        # Author lines that are purely institutional (no personal name) become affiliations.
        if self._INSTITUTION_HINT.search(text) and not self._AUTHOR_NAME.match(text):
            return "affiliation"
        # Place-only tails ("Philadelphia", "PA", "USA") are affiliation text.
        tokens = [t for t in re.split(r"[\s,]+", text) if t]
        if tokens and all(t in {"PA", "CA", "NY", "MA", "IL", "TX", "USA", "UK", "NY,", "CA,"} for t in tokens):
            return "affiliation"
        return block.role

    def _refine_affiliation(self, block: TextBlock, text: str, lowered: str) -> str:
        return block.role

    def _refine_unclassified(self, block: TextBlock, text: str, lowered: str) -> str:
        if self._SECTION_HEADINGS.match(text):
            return "unclassified"
        return block.role

    def _is_structured_abstract_line(self, text: str) -> bool:
        """Lines like ``Results: ...`` belong to structured conference abstracts."""

        return bool(self._STRUCTURED_LABEL.match(text))

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _mark_abstract_regions(self, blocks: list[TextBlock]) -> None:
        """Flag body-text blocks that sit below an ABSTRACT heading.

        Conference abstracts put the whole abstract under an ``ABSTRACT``
        heading. The heuristic classifier scores that body text as ``title``
        because it is large and near the top of page 1. Knowing which blocks
        belong to the abstract region lets the refiner reclaim them.
        """

        ordered = sorted(blocks, key=reading_order_key)
        by_page: dict[int, list[TextBlock]] = {}
        for block in ordered:
            by_page.setdefault(block.page, []).append(block)

        for page_blocks in by_page.values():
            heading: TextBlock | None = None
            for block in page_blocks:
                text = " ".join(str(block.text or "").split())
                if is_abstract_heading(text) or (
                    block.role == "abstract" and len(text) <= 30
                ):
                    heading = block
                    continue
                if heading is None:
                    continue
                # An abstract belongs to the column its heading sits in. The
                # neighbouring column is a different article, not part of it.
                if not self._same_column(block, heading):
                    continue
                if self._SECTION_HEADINGS.match(text):
                    break  # Abstract region ends at the next real section heading.
                block.metadata["near_abstract_heading"] = True

    @staticmethod
    def _same_column(block: TextBlock, heading: TextBlock) -> bool:
        """Return True when both blocks sit in the same text column.

        Full-width blocks span every column, so they never break a region.
        """

        column = block.metadata.get("column")
        heading_column = heading.metadata.get("column")
        if column is None or heading_column is None:
            return True
        return column == heading_column

    _NAME_AND_LIST = re.compile(
        r"^[A-Z][a-zA-Z'’\-]+\s+[A-Z][a-zA-Z'’\-]+,?\s+(?:and|&)\s+[A-Z][a-zA-Z'’\-]+\s+[A-Z][a-zA-Z'’\-]+,?$"
    )

    def _looks_like_author_line(self, text: str) -> bool:
        if len(text) > 120:
            return False
        if self._NAME_COMMA_LIST.match(text) or self._NAME_AND_LIST.match(text):
            return True
        if self._INSTITUTION_HINT.search(text) and "," in text:
            return True
        return False

    def _is_title_like(self, text: str) -> bool:
        """Titles are short, mostly capitalized, and lack sentence punctuation."""

        if len(text) > 120:
            return False
        words = [w for w in text.split() if any(ch.isalpha() for ch in w)]
        if not words:
            return False
        capitalized = sum(1 for w in words if w[:1].isupper())
        if capitalized / len(words) >= 0.6 and not text.rstrip().endswith((".", ",", ";")):
            return True
        return False

    def _near_abstract_heading(self, block: TextBlock) -> bool:
        return bool(block.metadata.get("near_abstract_heading"))

    def _needs_review(self, text: str, lowered: str) -> bool:
        """Flag content whose role the heuristic classifier is unsure about."""

        if self._SENTENCE_LIKE.search(text) and len(text) > 200:
            return True
        return False
