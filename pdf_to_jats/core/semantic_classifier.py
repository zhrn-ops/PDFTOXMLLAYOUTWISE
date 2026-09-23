"""Rule-based and optional LLM-backed semantic classification."""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass
from typing import Any

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
        if self._looks_like_footer_noise(block, lower, words):
            return ClassificationResult("unclassified", 0.0, rationale="footer/noise")
        if re.search(r"\b(?:presenting|corresponding)\s+author\b", lower):
            return ClassificationResult("corresponding_author", 1.0, rationale="corresponding-author marker")
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
        elif features.font_size_ratio >= 1.25:
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
        if block.page >= 1 and block.y > 600 and len(words) <= 18:
            return True
        return False

    def refine_with_llm(self, payload: dict[str, Any], llm_client: Any) -> dict[str, Any]:
        """Optionally refine classifications using a local LLM client."""

        return llm_client.classify(prompt=CLASSIFICATION_PROMPT, payload=payload)
