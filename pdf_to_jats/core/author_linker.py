"""Author-affiliation linking."""

from __future__ import annotations

from dataclasses import dataclass
import re

from pdf_to_jats.core.auto_refiner import looks_like_address
from pdf_to_jats.models.affiliation import Affiliation
from pdf_to_jats.models.author import Author
from pdf_to_jats.models.block import TextBlock, reading_order_key

# "Nahas, S.J.1; Zhao, Y.2" — conference supplements put the surname first and
# attach the affiliation marker to the initials, instead of the usual
# "S.J. Nahas" given-name-first form.
_SURNAME_MARKER_AUTHOR = re.compile(
    r"([A-Z][A-Za-z'\u2019\-\u00C0-\u017F]+)\s*,\s*((?:[A-Z]\.){1,3})\s*(\d+(?:\s*,\s*\d+)*)?"
)

# "Sarah Smith, Bucknell University" — journal PDFs often embed the
# institution directly in the author line instead of a numbered list.
_AUTHOR_INSTITUTION_SPLIT = re.compile(r",\s*(?=(?:[A-Z][A-Za-z'’\-]+\s+){0,3}(?:University|Universit\u00e4t|Institute|Institut|Hospital|College|Center|Centre|School|Laborator|Foundation|Department|Academy))")


def split_author_institution(text: str) -> tuple[list[str], list[str]]:
    """Split an author line into (author names, embedded affiliations)."""

    cleaned = " ".join(str(text or "").replace("\n", " ").split()).strip(" ,;")
    if not cleaned:
        return [], []
    parts = _AUTHOR_INSTITUTION_SPLIT.split(cleaned)
    if len(parts) == 1:
        return [cleaned], []
    authors = [part.strip(" ,;") for part in parts if part.strip(" ,;")]
    # The institution is the last split part; everything before it is names.
    institution = parts[-1].strip(" ,;")
    authors = [part.strip(" ,;") for part in parts[:-1] if part.strip(" ,;")]
    return authors, [institution] if institution else []


@dataclass(slots=True)
class LinkedAuthors:
    """Grouped author and affiliation output."""

    authors: list[Author]
    affiliations: list[Affiliation]
    corresponding_author: Author | None = None


class AuthorLinker:
    """Link authors to affiliations using simple graph heuristics."""

    SURNAME_PARTICLES = {
        "da", "das", "de", "del", "della", "der", "di", "do", "dos",
        "du", "la", "le", "van", "vander", "von",
    }

    def link(self, authors_text: list[str], affiliations_text: list[str]) -> LinkedAuthors:
        return self.link_from_texts(authors_text, affiliations_text)

    def link_from_blocks(self, author_blocks: list[TextBlock], affiliation_blocks: list[TextBlock]) -> LinkedAuthors:
        """Link authors and affiliations using the original text blocks."""

        ordered_blocks = sorted(author_blocks, key=reading_order_key)
        authors_text = [block.text for block in ordered_blocks if block.role != "corresponding_author"]
        corresponding_texts = [block.text for block in ordered_blocks if block.role == "corresponding_author"]
        affiliations_text = [block.text for block in sorted(affiliation_blocks, key=reading_order_key)]
        linked = self.link_from_texts(authors_text, affiliations_text)
        linked.corresponding_author = self._find_corresponding_author(corresponding_texts, linked.authors)
        if linked.corresponding_author is None and linked.authors:
            linked.corresponding_author = linked.authors[0]
        return linked

    def _find_corresponding_author(self, texts: list[str], authors: list[Author]) -> Author | None:
        for text in texts:
            name_text = re.sub(r"^\s*[*\u2020\u2021\u2020]*\s*presenting\s+author\s*:\s*", "", text, flags=re.IGNORECASE).strip()
            name, _ = self._parse_author_chunk(name_text)
            if not name:
                continue
            normalized_name = " ".join(name.lower().split())
            for author in authors:
                if normalized_name == " ".join(author.display_name.lower().split()):
                    return author
                if author.surname.lower() in normalized_name or normalized_name.endswith(author.surname.lower()):
                    return author
        return None

    def link_from_texts(self, authors_text: list[str], affiliations_text: list[str]) -> LinkedAuthors:
        # Journal-style lines combine the name with the institution; split them
        # first and collect the institutional tails as extra affiliations.
        expanded_authors: list[str] = []
        embedded_affiliations: list[str] = []
        for text in authors_text:
            names, institutions = split_author_institution(text)
            if institutions:
                expanded_authors.extend(names)
                embedded_affiliations.extend(institutions)
            else:
                expanded_authors.append(text)
        affiliations_text = [*affiliations_text, *embedded_affiliations]

        affiliation_texts = self._split_affiliation_list(affiliations_text)
        affiliations = []
        for index, text in enumerate(affiliation_texts, start=1):
            match = re.match(r"\s*(\d+)\s+", text)
            affiliation_id = match.group(1) if match else str(index)
            affiliation_text = text[match.end():].strip() if match else text
            affiliations.append(Affiliation(id=affiliation_id, text=affiliation_text))
        authors: list[Author] = []
        for text in expanded_authors:
            for author_text in self._split_author_list(text):
                name, markers = self._parse_author_chunk(author_text)
                if not self._is_valid_author_candidate(name or author_text):
                    continue
                if markers:
                    affiliation_ids = self._resolve_affiliation_ids(markers, affiliations)
                else:
                    affiliation_ids = []
                given_names, initials, surname = self._split_name(name or author_text)
                authors.append(
                    Author(
                        initials=initials,
                        surname=surname,
                        display_name=name or author_text,
                        given_names=given_names,
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

        # Surname-first lists need normalizing before the generic comma split,
        # which would otherwise tear each name into a surname and an initial.
        surname_first = self._extract_surname_first_authors(cleaned)
        if surname_first:
            return surname_first

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
        """Normalize surname-first author lists into ``"Initials Surname"`` form.

        Downstream parsing assumes given names come first, so returning the
        source order (``"Nahas, S.J."``) made ``_split_name`` emit the initials
        as the surname. Inline affiliation markers are kept attached to the
        initials so they can still be recovered afterwards.
        """

        matches = _SURNAME_MARKER_AUTHOR.findall(text)
        if not matches:
            return []
        if len(matches) == 1 and matches[0][1].strip() and text.strip().strip(" ,;") != (
            f"{matches[0][0]}, {matches[0][1]}{matches[0][2]}".strip(" ,;")
        ):
            return []
        return [
            f"{initials}{markers or ''} {surname}"
            for surname, initials, markers in matches
        ]

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

    def _split_name(self, full_name: str) -> tuple[str, str, str]:
        parts = [part for part in full_name.split() if part]
        if not parts:
            return "", "", ""
        if len(parts) == 1:
            return parts[0], f"{parts[0][0].upper()}.", parts[0]

        surname_start = len(parts) - 1
        for index, part in enumerate(parts[1:], start=1):
            if part.rstrip(".").lower() in self.SURNAME_PARTICLES:
                surname_start = index
                break
        given_parts = parts[:surname_start]
        surname_parts = parts[surname_start:]
        initials_parts = []
        for part in given_parts:
            if re.fullmatch(r"(?:[A-Z]\.)+", part):
                initials_parts.append(part if part.endswith(".") else f"{part}.")
            else:
                initials_parts.append(f"{part[0].upper()}.")
        return " ".join(given_parts), "".join(initials_parts), " ".join(surname_parts)

    def _is_valid_author_candidate(self, text: str) -> bool:
        """Reject obvious footer noise and non-name text before author serialization."""

        cleaned = " ".join(text.replace("\ufeff", " ").replace("\u00a0", " ").split()).strip(" ,;")
        if not cleaned:
            return False
        lowered = cleaned.lower()
        # Address and affiliation tails are not people. Without this, a wrapped
        # affiliation such as "... Los Angeles and San Diego, CA, USA" produced
        # authors named "Los Angeles" and "San Diego".
        if looks_like_address(cleaned):
            return False
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
        # URLs, DOIs, and reference tails are never authors.
        if re.search(r"(?:https?:|doi\.org|\bdoi\b|10\.\d{4,}/|www\.)", lowered):
            return False
        if cleaned.count("/") >= 2 or "\u200b" in cleaned:
            return False
        # Names start with a capital letter; lowercase fragments are body text.
        first_alpha = next((ch for ch in cleaned if ch.isalpha()), "")
        if first_alpha and first_alpha.islower():
            return False
        # Institution tails that kept their own line are not people.
        last_word = cleaned.rstrip(" ,;.").rsplit(" ", 1)[-1].lower()
        if last_word in {
            "university", "institute", "hospital", "college", "center", "centre",
            "school", "laboratory", "department", "foundation", "academy", "institut",
        }:
            return False
        # Pure place-name fragments ("Philadelphia", "PA", "USA", "New York")
        # are affiliation tails, not people.
        place_tokens = {
            "usa", "uk", "canada", "germany", "france", "italy", "spain", "japan",
            "china", "india", "australia", "brazil", "netherlands", "sweden",
            "switzerland", "philadelphia", "boston", "london", "paris", "berlin",
            "new york", "new jersey", "united states", "united kingdom",
        }
        if lowered in place_tokens or re.fullmatch(r"[A-Z]{1,3}", cleaned):
            return False
        if len(cleaned) > 8 and sum(1 for ch in cleaned if ch.isupper()) == len([ch for ch in cleaned if ch.isalpha()]):
            # Very short all-caps shards are usually OCR noise.
            # spatial author and affiliations algorithm i have to build a better one
            return False
        return bool(re.search(r"[A-Za-z]", cleaned))
