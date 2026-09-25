"""Author-affiliation linking.

Matching follows the CAR author/affiliation table:

===================================  =============  =====================
Authors                              Affiliations   Rule
===================================  =============  =====================
any                                  none           no action
any                                  1              that affiliation applies
                                                    to every author
1                                    >1             the author receives all
                                                    affiliations
>1                                   >1             match by printed marker
>1                                   >1, no markers assumption is unsafe:
                                                    leave unlinked and flag
===================================  =============  =====================

Markers may be digits, symbols, or letters, in any combination, and ranges are
expanded (``1-3`` -> ``1,2,3``). They are read only from the marker position, so
digits inside institution text are never mistaken for one.
"""

from __future__ import annotations

from dataclasses import dataclass, field
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

# Superscript digits arrive as their own code points when a PDF keeps the
# raised glyph; flatten them so one marker vocabulary is enough.
_SUPERSCRIPT_DIGITS = str.maketrans("¹²³⁴⁵⁶⁷⁸⁹⁰", "1234567890")
_SYMBOL_MARKERS = "*†‡§¶#"

_MARKER_ATOM = rf"(?:\d{{1,3}}|[{re.escape(_SYMBOL_MARKERS)}]+|[A-Za-z])"
_MARKER_RUN = rf"(?:{_MARKER_ATOM})(?:\s*[,\-\u2013]\s*(?:{_MARKER_ATOM}))*"

# Digits or symbols glued to the end of a name: "Farias20", "Doe*".
_TRAILING_GLUED_MARKER = re.compile(rf"([\d{re.escape(_SYMBOL_MARKERS)}]+)\s*$")
# Markers glued to an initials token: "S.J.1 Nahas", "S.J.1,2 Nahas".
_INITIALS_GLUED_MARKER = re.compile(rf"^((?:[A-Z]\.)+)({_MARKER_RUN})\s+(\S.*)$")
# A standalone marker token separated by a space: "Jane Doe a", "Jane Doe 1,2".
_STANDALONE_MARKER = re.compile(rf"^(.*\S)\s+({_MARKER_RUN})$")

# Leading label on an affiliation block: "1 Univ", "1. Univ", "(1) Univ",
# "[a] Univ", "a) Univ". A bare letter needs a delimiter so that "Department of
# Oncology" is not read as the label "D".
_AFFILIATION_LABEL = re.compile(
    rf"^\s*(?:[\(\[](?P<bracket>\d{{1,3}}|[{re.escape(_SYMBOL_MARKERS)}]|[A-Za-z])[\)\]]"
    rf"|(?P<plain>\d{{1,3}}|[{re.escape(_SYMBOL_MARKERS)}]+)"
    rf"|(?P<letter>[A-Za-z])(?=[.,:)]))"
    rf"\s*[.,:)]?\s+"
)

# A leading single letter plus whitespace; accepted only when an author carries
# that letter as a marker.
_BARE_LETTER_LABEL = re.compile(r"^\s*(?P<letter>[A-Za-z])\s+(?P<rest>\S.*)$")

# Labels are often printed with no space after the digits: "1Atrium Health".
_GLUED_DIGIT_LABEL = re.compile(r"^(\d{1,3})(?=\S)")

# A block that ends on a connector is a wrapped continuation of the entry above.
_INCOMPLETE_TAIL = re.compile(
    r"[,;:\-\u2013\u2014&/]\s*$|\b(?:and|of|at|the|de|di|del|du|la|le)\s*$",
    re.IGNORECASE,
)


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
class LinkIssue:
    """One author/affiliation matching problem that a reviewer should see."""

    severity: str
    message: str
    author_id: str = ""
    affiliation_id: str = ""


@dataclass(slots=True)
class LinkedAuthors:
    """Grouped author and affiliation output."""

    authors: list[Author]
    affiliations: list[Affiliation]
    corresponding_author: Author | None = None
    issues: list[LinkIssue] = field(default_factory=list)


class AuthorLinker:
    """Link authors to affiliations using printed markers, with CAR fallbacks."""

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
        issues: list[LinkIssue] = []
        # Journal-style lines combine the name with the institution; split them
        # first and collect the institutional tails as extra affiliations.
        expanded_authors: list[str] = []
        embedded_affiliations: list[str] = []
        for text in authors_text:
            names, institutions = split_author_institution(text)
            if institutions:
                expanded_authors.extend(names)
                embedded_affiliations.extend(institutions)
            elif looks_like_address(text):
                # A wrapped address tail that landed in an author block is
                # affiliation data; parsing it as names produced "authors" such
                # as "Los Angeles" and "San Diego".
                embedded_affiliations.append(text)
            else:
                expanded_authors.append(text)

        # Pass 1 collects the author names and markers; pass 2 needs that marker
        # set to tell a lettered affiliation label ("a Alpha") from ordinary
        # text ("Alpha Institute").
        parsed: list[tuple[str, list[str]]] = []
        for text in expanded_authors:
            for author_text in self._split_author_list(text):
                name, markers = self._parse_author_chunk(author_text)
                if not name and markers:
                    # A bare marker chunk ("²") continues the previous author.
                    if parsed:
                        for marker in markers:
                            if marker not in parsed[-1][1]:
                                parsed[-1][1].append(marker)
                    continue
                if not self._is_valid_author_candidate(name or author_text):
                    continue
                parsed.append((name or author_text, markers))

        known_markers = {marker for _name, markers in parsed for marker in markers}
        affiliations = self._build_affiliations(
            [*affiliations_text, *embedded_affiliations], known_markers
        )
        marker_to_id: dict[str, str] = {}
        for affiliation in affiliations:
            for marker in affiliation.markers:
                marker_to_id.setdefault(marker, affiliation.id)

        authors: list[Author] = []
        for index, (name, markers) in enumerate(parsed, start=1):
            given_names, initials, surname = self._split_name(name)
            authors.append(
                Author(
                    initials=initials,
                    surname=surname,
                    display_name=name,
                    given_names=given_names,
                    id=f"author_{index}",
                    markers=markers,
                )
            )

        for author in authors:
            author.affiliation_ids = self._resolve_marker_ids(author.markers, marker_to_id)

        self._apply_matching_rules(authors, affiliations, issues)
        self._order_affiliations(authors, affiliations)
        return LinkedAuthors(authors=authors, affiliations=affiliations, issues=issues)

    # ------------------------------------------------------------------
    # Marker handling
    # ------------------------------------------------------------------

    def _parse_author_chunk(self, text: str) -> tuple[str, list[str]]:
        """Return an author name with its affiliation markers removed."""

        cleaned = " ".join(text.translate(_SUPERSCRIPT_DIGITS).replace("\n", " ").split()).strip(" ,;")
        if not cleaned:
            return "", []

        # A chunk holding nothing but markers ("1,2", "2", "*") is a comma
        # split artefact that continues the author before it.
        if not re.search(r"[A-Za-z]", cleaned):
            markers = self._split_marker_run(cleaned)
            if markers:
                return "", markers
        if len(cleaned) == 1 and cleaned.isalpha():
            return "", [cleaned]

        # Markers carried on the initials after surname-first normalization.
        initials_match = _INITIALS_GLUED_MARKER.match(cleaned)
        if initials_match:
            markers = self._split_marker_run(initials_match.group(2))
            name = f"{initials_match.group(1)} {initials_match.group(3)}".strip(" ,;")
            return name if self._has_name_letters(name) else "", markers

        markers: list[str] = []
        glued = _TRAILING_GLUED_MARKER.search(cleaned)
        if glued:
            trailing = glued.group(1)
            before = cleaned[: glued.start()].rstrip()
            # A lone trailing number is a marker only when a name precedes it.
            if before and self._has_name_letters(before):
                markers = self._split_marker_run(trailing)
                cleaned = before

        if not markers:
            standalone = _STANDALONE_MARKER.match(cleaned)
            if standalone and self._is_marker_run(standalone.group(2)) and self._has_name_letters(standalone.group(1)):
                markers = self._split_marker_run(standalone.group(2))
                cleaned = standalone.group(1)

        return cleaned.strip(" ,;"), markers

    def _split_marker_run(self, run: str) -> list[str]:
        """Expand a marker run such as ``"1,2"``, ``"1-3"`` or ``"*†"``."""

        markers: list[str] = []
        for token in re.split(r"\s*,\s*", str(run or "").strip(" ,;.")):
            token = token.strip()
            if not token:
                continue
            span = re.fullmatch(r"(\d{1,3})\s*[-\u2013]\s*(\d{1,3})", token)
            if span and int(span.group(1)) <= int(span.group(2)):
                markers.extend(
                    str(value) for value in range(int(span.group(1)), int(span.group(2)) + 1)
                )
                continue
            if re.fullmatch(r"\d{1,3}", token):
                markers.append(str(int(token)))
                continue
            markers.extend(character for character in token if not character.isspace())
        return self._dedupe_preserve_order([marker for marker in markers if marker])

    @staticmethod
    def _is_marker_run(token: str) -> bool:
        """True only for tokens that are entirely markers (never a real word)."""

        if not token or not re.fullmatch(_MARKER_RUN, token):
            return False
        # A multi-letter token is a word, not a lettered marker.
        letters = [character for character in token if character.isalpha()]
        return len(letters) <= 1 or bool(re.search(r"[,\-]", token))

    @staticmethod
    def _has_name_letters(text: str) -> bool:
        return len(re.findall(r"[A-Za-z]", text)) >= 2

    def _resolve_marker_ids(self, markers: list[str], marker_to_id: dict[str, str]) -> list[str]:
        resolved: list[str] = []
        for marker in self._dedupe_preserve_order(markers):
            affiliation_id = marker_to_id.get(marker)
            if affiliation_id and affiliation_id not in resolved:
                resolved.append(affiliation_id)
        return resolved

    # ------------------------------------------------------------------
    # Affiliation segmentation
    # ------------------------------------------------------------------

    def _build_affiliations(self, texts: list[str], known_markers: set[str] | None = None) -> list[Affiliation]:
        """Turn affiliation text blocks into deduplicated affiliations.

        Blocks are processed individually so two unmarked affiliations never
        merge just because neither carries a semicolon, while a wrapped line
        that ends on a connector is folded back into the entry above it.
        """

        entries: list[dict[str, str]] = []
        for text in texts:
            cleaned = " ".join(str(text or "").replace("\n", " ").split())
            if not cleaned:
                continue
            for candidate in (part.strip(" ,;") for part in re.split(r"\s*;\s*", cleaned)):
                if not candidate:
                    continue
                marker, raw_marker, body = self._split_affiliation_label(
                    candidate, known_markers or set()
                )
                if not marker and entries and self._looks_incomplete(entries[-1]["text"]):
                    entries[-1]["text"] = f"{entries[-1]['text']} {body}".strip()
                    continue
                entries.append({"marker": marker, "raw_marker": raw_marker, "text": body or candidate})

        by_key: dict[str, Affiliation] = {}
        affiliations: list[Affiliation] = []
        for index, entry in enumerate(entries, start=1):
            key = self._affiliation_key(entry["text"])
            if not key:
                continue
            existing = by_key.get(key)
            if existing is not None:
                marker = entry["marker"]
                if marker and marker not in existing.markers:
                    existing.markers.append(marker)
                continue
            marker = entry["marker"]
            affiliation = Affiliation(
                id=marker or f"aff_{index}",
                text=entry["text"],
                marker=marker,
                raw_marker=entry["raw_marker"],
                markers=[marker] if marker else [],
            )
            by_key[key] = affiliation
            affiliations.append(affiliation)
        return affiliations

    @staticmethod
    def _looks_incomplete(text: str) -> bool:
        """True when an affiliation entry is a fragment awaiting its next line.

        Wrapped affiliations arrive as a marked first line ("10Seoul") followed
        by unmarked text, so an entry that neither ends on a connector nor
        carries a country/postal marker is treated as unfinished.
        """

        if _INCOMPLETE_TAIL.search(text):
            return True
        return not looks_like_address(text)

    def _split_affiliation_label(
        self, text: str, known_markers: set[str]
    ) -> tuple[str, str, str]:
        """Return ``(normalized marker, printed marker, text without label)``.

        A leading single letter is only treated as a label when some author
        actually carries that letter as a marker, so "Alpha Institute" is never
        read as the label ``A``.
        """

        match = _AFFILIATION_LABEL.match(text)
        if match:
            raw_marker = next((group for group in match.groupdict().values() if group), "")
            markers = self._split_marker_run(raw_marker)
            marker = markers[0] if markers else ""
            if marker:
                body = text[match.end():].strip(" ,;")
                return marker, raw_marker, body

        bare_letter = _BARE_LETTER_LABEL.match(text)
        if bare_letter and bare_letter.group("letter") in known_markers:
            return (
                bare_letter.group("letter"),
                bare_letter.group("letter"),
                bare_letter.group("rest").strip(" ,;"),
            )

        glued = _GLUED_DIGIT_LABEL.match(text)
        if glued:
            digits = glued.group(1)
            # "214th Department" is marker 21 followed by "4th Department", not
            # marker 214. Prefer the longest prefix an author actually cites,
            # then the longest that leaves a capitalised body.
            for length in range(len(digits), 0, -1):
                if digits[:length] in known_markers:
                    return digits[:length], digits[:length], text[length:].strip(" ,;")
            for length in range(len(digits), 0, -1):
                remainder = text[length:]
                if remainder[:1].isupper():
                    return digits[:length], digits[:length], remainder.strip(" ,;")
        return "", "", text

    @staticmethod
    def _affiliation_key(text: str) -> str:
        return re.sub(r"[^a-z0-9]+", " ", str(text or "").casefold()).strip()

    # ------------------------------------------------------------------
    # CAR matching rules and ordering
    # ------------------------------------------------------------------

    def _apply_matching_rules(
        self, authors: list[Author], affiliations: list[Affiliation], issues: list[LinkIssue]
    ) -> None:
        if not affiliations:
            return

        if len(affiliations) == 1:
            for author in authors:
                if author.affiliation_ids:
                    continue
                author.affiliation_ids = [affiliations[0].id]
                issues.append(
                    LinkIssue(
                        severity="warning",
                        message=(
                            f"Single affiliation applied to \"{author.display_name}\" "
                            "because no marker could be resolved"
                        ),
                        author_id=author.id,
                        affiliation_id=affiliations[0].id,
                    )
                )
            return

        if len(authors) == 1 and not any(author.markers for author in authors):
            author = authors[0]
            author.affiliation_ids = [affiliation.id for affiliation in affiliations]
            issues.append(
                LinkIssue(
                    severity="warning",
                    message=(
                        f"Single author \"{author.display_name}\" received all "
                        f"{len(affiliations)} affiliations because the citation carries no markers"
                    ),
                    author_id=author.id,
                )
            )
            return

        for author in authors:
            if author.affiliation_ids:
                continue
            if author.markers:
                issues.append(
                    LinkIssue(
                        severity="error",
                        message=(
                            f"Author \"{author.display_name}\" has marker(s) "
                            f"{', '.join(author.markers)} that match no affiliation"
                        ),
                        author_id=author.id,
                    )
                )
            else:
                issues.append(
                    LinkIssue(
                        severity="warning",
                        message=(
                            f"Author \"{author.display_name}\" carries no marker; "
                            "left unlinked rather than assumed"
                        ),
                        author_id=author.id,
                    )
                )

        for affiliation in affiliations:
            if not any(affiliation.id in author.affiliation_ids for author in authors):
                issues.append(
                    LinkIssue(
                        severity="warning",
                        message=(
                            f"Affiliation \"{affiliation.text[:60]}\" is not referenced "
                            "by any author"
                        ),
                        affiliation_id=affiliation.id,
                    )
                )

    def _order_affiliations(self, authors: list[Author], affiliations: list[Affiliation]) -> None:
        """Order by first author mention; unreferenced entries keep reading order."""

        first_mention: dict[str, int] = {}
        for index, author in enumerate(authors):
            for affiliation_id in author.affiliation_ids:
                first_mention.setdefault(affiliation_id, index)
        affiliations.sort(key=lambda affiliation: first_mention.get(affiliation.id, len(authors)))

    # ------------------------------------------------------------------
    # Author parsing
    # ------------------------------------------------------------------

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
            return False
        return bool(re.search(r"[A-Za-z]", cleaned))
