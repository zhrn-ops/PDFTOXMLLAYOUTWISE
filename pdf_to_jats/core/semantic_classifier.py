"""Rule-based and optional LLM-backed semantic classification."""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass
from typing import Any

from pdf_to_jats.core.auto_refiner import abstract_number_marker, looks_like_address
from pdf_to_jats.core.layout_analyzer import LayoutAnalyzer
from pdf_to_jats.models.block import TextBlock
from pdf_to_jats.llm.prompts import CLASSIFICATION_PROMPT

LOGGER = logging.getLogger(__name__)


@dataclass(slots=True)
class ClassificationResult:
    """Classification output for a block."""

    role: str
    confidence: float
    rationale: str = ""


class SemanticClassifier:
    """Assign semantic roles to PDF blocks."""

    # Surname-first author lists carrying inline affiliation markers, e.g.
    # "Nahas, S.J.1; Zhao, Y.2; Graham, C.3". The initials-first patterns
    # below cannot see these, so they used to score as titles.
    _SURNAME_FIRST_AUTHORS = re.compile(
        r"^(?:[A-Z][A-Za-z'\u2019\-]+,\s*(?:[A-Z]\.){1,3}\d*(?:\s*[,;]\s*)?){2,}"
    )

    # Digital and typographic markers an author carries for the affiliation
    # they belong to.
    _MARKER_CHARS = "0-9\u00b9\u00b2\u00b3\u2070\u2074-\u2079\u207f*\u2020\u2021\u00a7\u00b6#"
    _AUTHOR_NAME = r"[A-Z][A-Za-z'\u2019\-]+(?:\s+[A-Z][A-Za-z'\u2019\-]+){0,2}"
    # A full-name author list in which every author prints its own affiliation
    # marker: "Alessandro La Rosa\u00b2, Marco Scaglione\u00b9, ...". The first line
    # of such a list is often bold italic, so scoring on font alone made it a
    # title and glued the author names onto the article title.
    _MARKED_AUTHOR_LIST = re.compile(
        rf"^\s*{_AUTHOR_NAME}\s*[{_MARKER_CHARS}]+"
        rf"(?:[\s,;{_MARKER_CHARS}]*{_AUTHOR_NAME}\s*[{_MARKER_CHARS}]+)+"
    )
    _INSTITUTION_HINT = re.compile(
        r"\b(?:university|universit\u00e4t|institut|hospital|college|center|centre|school|"
        r"laborator|foundation|department|academy|clinic|society|association)\b",
        re.IGNORECASE,
    )

    # Text set this much smaller than the document body is page furniture
    # (running heads, banners, margin notes), never an article title.
    UNDERSIZED_FONT_RATIO = 0.70
    # A page footer sits in the bottom margin of its page: this fraction of the
    # page height, or this many points, whichever is larger.
    FOOTER_BAND_RATIO = 0.04
    FOOTER_BAND_MIN_POINTS = 20.0

    def __init__(self) -> None:
        self.layout_analyzer = LayoutAnalyzer()

    def classify_blocks(self, blocks: list[TextBlock]) -> list[ClassificationResult]:
        results: list[ClassificationResult] = []
        body_font = self._estimate_body_font(blocks)
        for block in blocks:
            features = self.layout_analyzer.extract_features(block, body_font_size=body_font)
            results.append(self._classify_block(block, features))
        return results

    def _estimate_body_font(self, blocks: list[TextBlock]) -> float:
        sizes = sorted(b.font_size for b in blocks if b.font_size > 0)
        return sizes[len(sizes) // 2] if sizes else 10.0

    def _classify_block(self, block: TextBlock, features: Any) -> ClassificationResult:
        text = block.text.strip()
        lower = text.lower()
        words = [part for part in re.split(r"\s+", text) if part]
        # Checked before footer noise so a marker sitting low on the page is
        # not mistaken for a footer line; bare digits never match, so page
        # numbers still fall through to the noise rule below.
        marker = abstract_number_marker(text)
        if marker:
            return ClassificationResult(
                "abstract_number", 1.0, rationale=f"abstract-number marker {marker}"
            )
        if features.font_size_ratio < self.UNDERSIZED_FONT_RATIO:
            # Running heads and banners sit in the page margins, well above the
            # body size in position but well below it in font size. The
            # position bonus used to claim them as titles.
            return ClassificationResult(
                "unclassified", 0.0, rationale="undersized page furniture"
            )
        if self._looks_like_footer_noise(block, lower, words):
            return ClassificationResult("unclassified", 0.0, rationale="footer/noise")
        if re.search(r"\b(?:presenting|corresponding)\s+author\b", lower):
            return ClassificationResult("corresponding_author", 1.0, rationale="corresponding-author marker")
        if re.match(r"^key\s*words?\s*[:\-]", text, re.IGNORECASE):
            return ClassificationResult("keywords", 1.0, rationale="keywords label")
        scores = {
            "title": 0,
            "author": 0,
            "affiliation": 0,
            "abstract": 0,
        }
        if block.page == 1 and features.position_top < 0.28:
            scores["title"] += 30
        if block.page == 1 and features.position_top < 0.40 and features.font_size_ratio >= 1.10:
            scores["title"] += 18
        if features.font_size_ratio >= 1.45:
            scores["title"] += 30
        elif features.font_size_ratio >= 1.15:
            # Conference abstracts set the title only a little above the 9pt
            # body, and their articles do not start at the top of the page, so
            # the position bonus alone leaves the real title below the cutoff.
            scores["title"] += 15
        if block.bold:
            scores["title"] += 10
        if features.centered:
            scores["title"] += 12
            scores["author"] += 10
        if 2 <= len(words) <= 18:
            scores["title"] += 8
        if len(words) <= 10 and any(ch.isalpha() for ch in text):
            scores["title"] += 5
        if len(words) <= 6:
            scores["author"] += 10
        if re.search(r"(?:[A-Z]\.\s*[A-Z]?[a-zA-Z'’\-]+(?:\s+[A-Z]\.)?\s*\d+\s*[;,.]?\s*){2,}", text):
            scores["author"] += 35
        if re.search(r"(?:[A-Z]\.\s*[A-Z]?[a-zA-Z'’\-]+(?:\s+[A-Z]\.)?\s*;\s*)+[A-Z]\.\s*[A-Z]?[a-zA-Z'’\-]+", text):
            scores["author"] += 35
        if self._SURNAME_FIRST_AUTHORS.match(text):
            scores["author"] += 40
        if self._looks_like_author_list(text):
            scores["author"] += 35
        if re.search(r"^\s*[A-Z]\.\s*[A-Z][a-zA-Z'’\-]+(?:\s+\d+)?\s*$", text):
            scores["author"] += 28
        if re.search(r"^\s*[A-Z]\.\s*[A-Z][a-zA-Z'’\-]+(?:\s+\d+)?(?:\s*[;,.]\s*)?$", text):
            scores["author"] += 28
        if re.search(r"^\s*\d+\s+[A-Z][A-Za-z0-9'’&\-\s]+", text) and any(keyword in lower for keyword in ("hospital", "university", "medical", "center", "centre", "institute", "department")):
            scores["affiliation"] += 35
        if (
            block.page == 1
            and 0.30 <= features.position_top <= 0.55
            and not re.search(r"\bpresenting author\b", lower)
            and (
                re.search(r"^\s*\d+\s+", text)
                or any(keyword in lower for keyword in ("hospital", "university", "medical", "center", "centre", "institute", "department"))
                or (len(words) >= 3 and any(word[:1].isupper() for word in words[:4]))
            )
        ):
            scores["affiliation"] += 18
        if len(words) <= 12 and not re.search(r"\babstract\b", lower) and features.position_top < 0.45:
            scores["title"] += 6
        if words and sum(1 for word in words if word[:1].isupper()) >= max(1, len(words) // 2):
            scores["title"] += 6
        if re.search(r"^\s*[A-Z]\.[A-Z]\.", text) or re.search(r"\b\d+(\s*[,;]\s*\d+)*\b", text):
            scores["author"] += 12
        if re.search(r"\babstract\b", lower):
            scores["abstract"] += 60
            if len(words) <= 3:
                scores["abstract"] += 20
        if features.position_top < 0.55 and features.word_count >= 30 and features.sentence_count >= 1:
            scores["abstract"] += 20
        if "conflict of interest" in lower or "acknowledg" in lower:
            scores["abstract"] -= 10
        if "university" in lower or "institute" in lower or "department" in lower or "hospital" in lower:
            scores["affiliation"] += 30

        role = max(scores, key=scores.get)
        confidence = min(max(scores[role], 0) / 100.0, 1.0)
        if scores[role] < 20:
            role = "unclassified"
        elif role == "title" and "abstract" in lower:
            role = "abstract"
        return ClassificationResult(role=role, confidence=confidence, rationale=f"scores={scores}")

    def _looks_like_footer_noise(self, block: TextBlock, lower: str, words: list[str]) -> bool:
        """Identify page footer/copyright boilerplate before semantic scoring."""

        if re.fullmatch(r"[\s|.\-]*\d+[\s|.\-]*", block.text.strip()):
            return True
        footer_terms = (
            "©",
            "copyright",
            "published by",
            "john wiley",
            "wiley and sons",
            "allergy_",
            "onlinelibrary",
        )
        if any(term in lower for term in footer_terms):
            return True
        # Page furniture sits in the bottom margin of its own page. A fixed
        # ``y > 600`` cutoff used to stand in for that, which also swallowed the
        # title and author lines of every article that starts low on a
        # two-column page.
        page_height = self._page_height(block)
        if page_height and len(words) <= 18:
            band = max(self.FOOTER_BAND_MIN_POINTS, page_height * self.FOOTER_BAND_RATIO)
            if page_height - (block.y + block.height) <= band:
                return True
        return False

    def _looks_like_author_list(self, text: str) -> bool:
        """Return True for a list of personal names carrying affiliation markers.

        The markers are what make this safe: a comma-separated list of
        capitalised words without them is far more likely to be a title or a
        section heading than a set of authors.
        """

        if not self._MARKED_AUTHOR_LIST.match(text):
            return False
        # Institution and postal lines also carry markers, but they name places
        # and organisations rather than people.
        return not (looks_like_address(text) or self._INSTITUTION_HINT.search(text))

    @staticmethod
    def _page_height(block: TextBlock) -> float:
        """Height of the block's page from the visible area extraction recorded."""

        area = block.metadata.get("visible_area")
        if not isinstance(area, (list, tuple)) or len(area) != 4:
            return 0.0
        try:
            height = float(area[3]) - float(area[1])
        except (TypeError, ValueError):
            return 0.0
        return height if height > 0 else 0.0

    def refine_with_llm(self, payload: dict[str, Any], llm_client: Any) -> dict[str, Any]:
        """Optionally refine classifications using a local LLM client."""

        return llm_client.classify(prompt=CLASSIFICATION_PROMPT, payload=payload)
