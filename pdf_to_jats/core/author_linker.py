"""Author-affiliation linking."""

from __future__ import annotations

from dataclasses import dataclass
import re

from pdf_to_jats.models.affiliation import Affiliation
from pdf_to_jats.models.author import Author
from pdf_to_jats.models.block import TextBlock


@dataclass(slots=True)
class LinkedAuthors:
    """Grouped author and affiliation output."""

    authors: list[Author]
    affiliations: list[Affiliation]


class AuthorLinker:
    """Link authors to affiliations using simple graph heuristics."""

    def link(self, authors_text: list[str], affiliations_text: list[str]) -> LinkedAuthors:
        return self.link_from_texts(authors_text, affiliations_text)

    def link_from_blocks(self, author_blocks: list[TextBlock], affiliation_blocks: list[TextBlock]) -> LinkedAuthors:
        """Link authors and affiliations using the original text blocks."""

        authors_text = [block.text for block in sorted(author_blocks, key=lambda b: (b.page, b.y, b.x))]
        affiliations_text = [block.text for block in sorted(affiliation_blocks, key=lambda b: (b.page, b.y, b.x))]
        return self.link_from_texts(authors_text, affiliations_text)

    def link_from_texts(self, authors_text: list[str], affiliations_text: list[str]) -> LinkedAuthors:
        affiliation_texts = self._split_affiliation_list(affiliations_text)
        affiliations = [Affiliation(id=str(i + 1), text=text) for i, text in enumerate(affiliation_texts)]
        authors: list[Author] = []
        for text in authors_text:
            for author_text in self._split_author_list(text):
                name, markers = self._parse_author_chunk(author_text)
                if not self._is_valid_author_candidate(name or author_text):
                    continue
                if markers:
                    affiliation_ids = self._resolve_affiliation_ids(markers, affiliations)
                elif affiliations:
                    affiliation_ids = [aff.id for aff in affiliations]
                else:
                    affiliation_ids = []
                initials, surname = self._split_name(name or author_text)
                authors.append(
                    Author(
                        initials=initials,
                        surname=surname,
                        display_name=name or author_text,
                        affiliation_ids=affiliation_ids,
                    )
                )
        return LinkedAuthors(authors=authors, affiliations=affiliations)

    def _split_author_list(self, text: str) -> list[str]:
        """Split a compressed author line into individual author strings.

        The source PDF may separate authors with punctuation, spaces, or line breaks.
        This tries a few name-shaped patterns before falling back to the raw text.
        """

        cleaned = " ".join(text.replace("\n", " ").split())
        if not cleaned:
            return []

        # If the block is a flat list of surname-like tokens, split on separators first.
        if re.search(r"\s*;\s*", cleaned) or re.search(r"\s*,\s*", cleaned):
            parts = [part.strip(" ,;") for part in re.split(r"\s*(?:;|,|&|\band\b)\s*", cleaned) if part.strip(" ,;")]
            if len(parts) > 1:
                return parts

        # Common forms:
        #   "J. Bae, J. Kim"
        #   "Bae J, Kim J"
        #   "J.H. Bae J. Kim"
        surname_first = self._extract_surname_first_authors(cleaned)
        if surname_first:
            return surname_first

        initials_first = self._extract_initials_first_authors(cleaned)
        if len(initials_first) > 1:
            return [part.strip(" ,;") for part in initials_first if part.strip(" ,;")]

        return [cleaned]

    def _parse_author_chunk(self, text: str) -> tuple[str, list[str]]:
        cleaned = " ".join(text.replace("\n", " ").split()).strip(" ,;")
        markers = re.findall(r"\^?(\d+)", cleaned)
        name = re.sub(r"(?<!\d)[\^,]?\d+(?!\d)", "", cleaned).strip(" ,;")
        name = re.sub(r"\s{2,}", " ", name)
        return name, markers

    def _resolve_affiliation_ids(self, markers: list[str], affiliations: list[Affiliation]) -> list[str]:
        if not affiliations:
            return []
        resolved: list[str] = []
        known_ids = {aff.id for aff in affiliations}
        for marker in self._dedupe_preserve_order(markers):
            if marker in known_ids:
                resolved.append(marker)
        return resolved or [affiliations[0].id]

    def _extract_surname_first_authors(self, text: str) -> list[str]:
        pattern = re.compile(
            r"[A-Z][a-zA-Z'’\-\u00C0-\u017F]+(?:\s+[A-Z][a-zA-Z'’\-\u00C0-\u017F]+)*\s*,\s*(?:[A-Z]\.(?:\s*[A-Z]\.)*|[A-Z]\.?)"
        )
        return [match.group(0).strip(" ,;") for match in pattern.finditer(text)]

    def _extract_initials_first_authors(self, text: str) -> list[str]:
        pattern = re.compile(
            r"(?:[A-Z]\.(?:\s*[A-Z]\.)*|[A-Z]\.?)\s+[A-Z][a-zA-Z'’\-\u00C0-\u017F]+(?:\s+[A-Z][a-zA-Z'’\-\u00C0-\u017F]+)*"
        )
        return [match.group(0).strip(" ,;") for match in pattern.finditer(text)]

    def _split_affiliation_list(self, texts: list[str]) -> list[str]:
        """Split compressed affiliation lists into individual affiliation strings."""

        cleaned = " ".join(" ".join(text.replace("\n", " ").split()) for text in texts if text and text.strip())
        if not cleaned:
            return []

        parts = [
            part.strip(" ,;")
            for part in re.split(r"\s*;\s*(?=\d+\s)", cleaned)
            if part.strip(" ,;")
        ]
        if len(parts) > 1:
            return parts

        parts = [part.strip() for part in re.split(r"\s*;\s*", cleaned) if part.strip()]
        return parts if parts else [cleaned.strip()]

    def _dedupe_preserve_order(self, values: list[str]) -> list[str]:
        seen: set[str] = set()
        result: list[str] = []
        for value in values:
            if value in seen:
                continue
            seen.add(value)
            result.append(value)
        return result

    def _split_name(self, full_name: str) -> tuple[str, str]:
        parts = [part for part in full_name.split() if part]
        if not parts:
            return "", ""
        if len(parts) == 1:
            return f"{parts[0][0].upper()}.", parts[0]
        initials_parts: list[str] = []
        surname_parts: list[str] = []
        for part in parts:
            if re.fullmatch(r"(?:[A-Z]\.)+", part) or re.fullmatch(r"[A-Z]\.", part):
                initials_parts.append(part if part.endswith(".") else f"{part}.")
            elif not initials_parts and len(part) <= 3:
                initials_parts.append(f"{part[0].upper()}.")
            else:
                surname_parts.append(part)
        if not initials_parts:
            initials_parts = [f"{part[0].upper()}." for part in parts[:-1]]
            surname_parts = [parts[-1]]
        if not surname_parts:
            surname_parts = [parts[-1]]
        return "".join(initials_parts), " ".join(surname_parts)

    def _is_valid_author_candidate(self, text: str) -> bool:
        """Reject obvious footer noise and non-name text before author serialization."""

        cleaned = " ".join(text.replace("\ufeff", " ").replace("\u00a0", " ").split()).strip(" ,;")
        if not cleaned:
            return False
        lowered = cleaned.lower()
        if cleaned in {"|", "||", "©", "®"}:
            return False
        if "copyright" in lowered or "published by" in lowered or "all rights reserved" in lowered:
            return False
        if lowered.startswith("the authors.") or lowered.startswith("the authors"):
            return False
        if cleaned.upper() in {"ABSTRACT", "A B S T R A C T"}:
            return False
        if not any(ch.isalpha() for ch in cleaned):
            return False
        if sum(1 for ch in cleaned if ch.isalpha()) < 2:
            return False
        if re.fullmatch(r"[^\w]+", cleaned):
            return False
        if len(cleaned) > 8 and sum(1 for ch in cleaned if ch.isupper()) == len([ch for ch in cleaned if ch.isalpha()]):
            # Very short all-caps shards are usually OCR noise.
            return False
        return bool(re.search(r"[A-Za-z]", cleaned))
