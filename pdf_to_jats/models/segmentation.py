"""Article segmentation as boundaries over one canonical reading stream.

A PDF that holds several complete conference articles is described here by the
blocks that *start* an article rather than by page/column predicates. Every block
is read in one column-aware order (the stream) and consecutive boundaries slice
that stream into articles, so each block belongs to exactly one article.

The alternative - deciding membership per block from a page range plus optional
columns - cannot express two articles that share a page *and* a column, and it
lets a block match two segments or none at all. Slicing a stream has neither
problem: the partition is structural.

Detection and a hand-made split therefore speak the same language: both insert a
boundary at a block. :meth:`Segmentation.from_legacy_segments` translates the
existing ``article_segments`` dicts into that form, reporting every place where
the legacy answer was not a clean partition instead of hiding it.

An article can also be flagged deleted: a false detection (an advertisement,
boilerplate) that would export garbage. Deleting keeps the boundary so its
blocks stay owned and visible, and every export path drops deleted articles.
"""

from __future__ import annotations

from bisect import bisect_right
from dataclasses import dataclass
from typing import Any, Iterable, Sequence

from pdf_to_jats.models.block import TextBlock
from pdf_to_jats.models.document import block_matches_segment, segment_is_manual


# Where a boundary came from: detected from the PDF, or drawn by a person.
SOURCE_AUTO = "auto"
SOURCE_MANUAL = "manual"
SOURCES = frozenset({SOURCE_AUTO, SOURCE_MANUAL})


def reading_order(block: TextBlock | None) -> int | None:
    """Return the reading position extraction recorded for a block, if any.

    The value is a rank, not a constant: extraction numbers blocks from zero and
    the UI numbers from one, and both order the document the same way.
    """

    if block is None:
        return None
    value = (block.metadata or {}).get("reading_order")
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def common_column(blocks: Sequence[TextBlock]) -> tuple[int, ...] | None:
    """Return the one column a set of blocks occupies, or ``None`` for many/none.

    ``None`` matches what the legacy segment shape means by it: the article is
    not confined to a single column.
    """

    columns: set[int] = set()
    for block in blocks:
        value = (block.metadata or {}).get("column")
        if value is None:
            continue
        try:
            columns.add(int(value))
        except (TypeError, ValueError):
            continue
    return tuple(columns) if len(columns) == 1 else None


def reading_stream(blocks: Iterable[TextBlock]) -> tuple[TextBlock, ...]:
    """Return blocks in one canonical reading order.

    Extraction numbers every block with a ``reading_order`` index that already
    accounts for columns, and that is the order the rest of the pipeline uses.
    Blocks without an index (hand-built documents, tests) fall back to page, then
    column, then top-to-bottom and left-to-right position.
    """

    ordered = list(blocks)
    if not ordered:
        return ()
    if all(reading_order(block) is not None for block in ordered):
        ranked = sorted(
            enumerate(ordered),
            key=lambda entry: (reading_order(entry[1]) or 0, entry[0]),
        )
        return tuple(block for _position, block in ranked)

    def fallback_key(block: TextBlock) -> tuple[int, int, float, float]:
        """Order a document whose blocks carry no reading index."""

        value = (block.metadata or {}).get("column")
        try:
            column_rank = int(value)
        except (TypeError, ValueError):
            # A block with no column is full width, so it reads before the
            # columns it spans.
            column_rank = -1
        return (int(block.page), column_rank, float(block.y), float(block.x))

    return tuple(sorted(ordered, key=fallback_key))


def legacy_segment_boundaries(
    blocks: Iterable[TextBlock],
    segments: Iterable[Any] | None,
    *,
    stream: Iterable[TextBlock] | None = None,
) -> list[str | None]:
    """Return the boundary block id of each legacy segment, in the same order.

    While articles are still stored in the legacy shape, this is the identity to
    key per-article bookkeeping on: the block an article starts at does not move
    when another article is inserted before it, so nothing has to be renumbered.

    Membership is decided the same way as :meth:`Segmentation.from_legacy_segments`,
    which keeps the two consistent. Two segments the partition cannot tell apart
    (both claim the document's first block, say) are a single article, so only the
    one that owns it gets a boundary and the other gets ``None``.
    """

    ordered = list(stream) if stream is not None else list(reading_stream(blocks))
    legacy = [segment for segment in (segments or []) if isinstance(segment, dict)]
    boundaries: list[str | None] = [None] * len(legacy)
    if not ordered:
        return boundaries
    for position, owner in enumerate(_legacy_ownership(ordered, legacy, [])):
        if owner is not None and boundaries[owner] is None:
            boundaries[owner] = ordered[position].id
    return boundaries


def _segment_label(segment: dict[str, Any]) -> str:
    """Short human name for a legacy segment, for issue messages."""

    number = str(segment.get("abstract_number") or "").strip()
    return number or f"#{segment.get('index', '?')}"


def _legacy_ownership(
    ordered: Sequence[TextBlock], segments: list[dict[str, Any]], issues: list[str]
) -> list[int | None]:
    """Give every block the index of the legacy segment that owns it.

    A block that more than one segment claims goes to the article that started
    most recently at or before it, and a block no segment claims stays with the
    article it follows, so the result is one contiguous run per article. Every
    difference from the legacy per-block answer is appended to ``issues``.

    Every entry is ``None`` when no segment can own anything, which the callers
    read as "the whole document is one article".
    """

    if not ordered or not segments:
        if not segments:
            # The legacy pipeline treats "nothing detected" as one article
            # covering the whole document.
            issues.append(
                "no article segments were detected; the whole document is one article"
            )
        return [None] * len(ordered)

    matched_by_position: list[list[int]] = [[] for _ in ordered]
    for segment_index, segment in enumerate(segments):
        for position, block in enumerate(ordered):
            if block_matches_segment(block, segment):
                matched_by_position[position].append(segment_index)

    starts: list[int | None] = [None] * len(segments)
    for position, candidates in enumerate(matched_by_position):
        for segment_index in candidates:
            if starts[segment_index] is None:
                starts[segment_index] = position

    unmatched = 0
    for segment_index, start in enumerate(starts):
        if start is None:
            unmatched += 1
            issues.append(
                f"detected article {_segment_label(segments[segment_index])} does not match "
                "any block and was dropped"
            )
    if unmatched == len(segments):
        issues.append(
            "no detected article matched any block; the whole document is one article"
        )
        return [None] * len(ordered)

    first_started = min(
        (index for index in range(len(segments)) if starts[index] is not None),
        key=lambda index: (starts[index], index),
    )
    owners: list[int | None] = []
    contested = 0
    orphaned = 0
    for candidates in matched_by_position:
        if candidates:
            if len(candidates) > 1:
                contested += 1
            owners.append(max(candidates, key=lambda index: (starts[index], -index)))
        elif owners and owners[-1] is not None:
            # A block no article claims stays with the one it follows, so the
            # partition stays contiguous and the content is not lost.
            orphaned += 1
            owners.append(owners[-1])
        else:
            # Blocks above the first detected article belong to it.
            orphaned += 1
            owners.append(first_started)
    if contested:
        issues.append(
            f"{contested} block(s) matched more than one detected article and were "
            "given to the one that starts last"
        )
    if orphaned:
        issues.append(
            f"{orphaned} block(s) matched no detected article and stayed with the "
            "article they follow"
        )
    return owners


@dataclass(frozen=True, slots=True)
class ArticleBoundary:
    """The block that starts one article, plus where its number came from."""

    block_id: str
    source: str = SOURCE_AUTO
    number: str = ""
    marker_block_id: str = ""
    deleted: bool = False

    @property
    def is_manual(self) -> bool:
        """Whether a person drew this boundary rather than the detector."""

        return self.source == SOURCE_MANUAL

    def to_dict(self) -> dict[str, Any]:
        """Serialize the boundary for ``Document.metadata``."""

        return {
            "block_id": self.block_id,
            "source": self.source,
            "number": self.number,
            "marker_block_id": self.marker_block_id,
            "deleted": self.deleted,
        }

    @classmethod
    def from_dict(cls, data: Any) -> ArticleBoundary | None:
        """Read a boundary back, returning ``None`` when it is unusable."""

        if isinstance(data, ArticleBoundary):
            return data
        if not isinstance(data, dict):
            return None
        block_id = str(data.get("block_id") or "").strip()
        if not block_id:
            return None
        source = str(data.get("source") or SOURCE_AUTO)
        if source not in SOURCES:
            source = SOURCE_AUTO
        return cls(
            block_id=block_id,
            source=source,
            number=str(data.get("number") or ""),
            marker_block_id=str(data.get("marker_block_id") or ""),
            deleted=bool(data.get("deleted")),
        )


@dataclass(frozen=True, slots=True)
class ArticleSummary:
    """One article as a derived view of its slice of the reading stream."""

    index: int
    boundary: ArticleBoundary
    block_ids: tuple[str, ...]
    start_page: int
    end_page: int
    columns: tuple[int, ...] | None

    @property
    def number(self) -> str:
        """The article number, as printed or typed; empty when unknown."""

        return self.boundary.number

    @property
    def first_block_id(self) -> str:
        """The block the article starts at."""

        return self.boundary.block_id

    @property
    def is_manual(self) -> bool:
        """Whether the article was split by hand rather than detected."""

        return self.boundary.is_manual


class Segmentation:
    """Which blocks make up each article, as one boundary per article.

    Invariants, enforced on construction and after every edit:

    * ``stream`` holds every block id exactly once, in reading order.
    * ``boundaries`` is sorted by reading position and holds no duplicates.
    * The first stream block always starts an article, so a document with blocks
      always has at least one article and no block is left unowned.
    """

    __slots__ = ("stream", "boundaries", "issues", "_order", "_starts")

    def __init__(
        self,
        stream: Iterable[str],
        boundaries: Iterable[Any] = (),
        issues: Iterable[str] = (),
    ) -> None:
        self.stream: tuple[str, ...] = ()
        self.issues: list[str] = [str(issue) for issue in issues]
        self.boundaries: list[ArticleBoundary] = []
        self._order: dict[str, int] = {}
        self._starts: list[int] = []
        self._set_stream(stream)
        self._set_boundaries(boundaries)

    # Construction -----------------------------------------------------------

    @classmethod
    def from_legacy_segments(
        cls,
        blocks: Iterable[TextBlock],
        segments: Iterable[Any] | None,
        *,
        number: str = "",
        stream: Iterable[TextBlock] | None = None,
    ) -> Segmentation:
        """Translate the page/column segment dicts into stream boundaries.

        The legacy shape answers "does this block belong to this segment?" per
        block, so a block can answer yes for two segments or for none.
        :func:`_legacy_ownership` resolves that into one contiguous run per
        article, and every difference from the legacy answer is appended to
        ``issues``: blocks claimed twice, blocks claimed by nobody, and detected
        articles that would cover more than one range of the document.

        ``number`` is the document-level abstract number, used when nothing was
        detected and the whole PDF is therefore one article.
        """

        ordered = list(stream) if stream is not None else list(reading_stream(blocks))
        issues: list[str] = []
        if not ordered:
            return cls((), (), issues)

        legacy = [segment for segment in (segments or []) if isinstance(segment, dict)]
        owners = _legacy_ownership(ordered, legacy, issues)
        stream_ids = [block.id for block in ordered]
        if all(owner is None for owner in owners):
            # Nothing was detected, so the whole document is one article.
            return cls(stream_ids, [ArticleBoundary(stream_ids[0], SOURCE_AUTO, number)], issues)

        boundaries: list[ArticleBoundary] = []
        runs: list[int] = []
        previous: int | None = None
        for position, owner in enumerate(owners):
            if owner == previous:
                continue
            previous = owner
            runs.append(owner)
            segment = legacy[owner]
            # :meth:`to_legacy_segments` records the origin it rendered, and only
            # plain legacy geometry has to be inferred from its reading bounds:
            # a detected article that was rendered with bounds would otherwise
            # come back looking hand-made on the next edit.
            declared = str(segment.get("boundary_source") or "").strip()
            source = (
                declared
                if declared in SOURCES
                else (SOURCE_MANUAL if segment_is_manual(segment) else SOURCE_AUTO)
            )
            boundaries.append(
                ArticleBoundary(
                    block_id=stream_ids[position],
                    source=source,
                    number=str(segment.get("abstract_number") or "").strip(),
                    marker_block_id=str(segment.get("abstract_number_block_id") or ""),
                    deleted=bool(segment.get("deleted")),
                )
            )
        repeated = sorted({index for index in runs if runs.count(index) > 1})
        for segment_index in repeated:
            issues.append(
                f"detected article {_segment_label(legacy[segment_index])} covers more "
                f"than one reading range; it became {runs.count(segment_index)} articles"
            )
        return cls(stream_ids, boundaries, issues)

    @classmethod
    def from_dict(cls, data: Any) -> Segmentation | None:
        """Read a stored segmentation back, or ``None`` when it is unusable."""

        if not isinstance(data, dict):
            return None
        stream = data.get("stream")
        if not isinstance(stream, list):
            return None
        issues = data.get("issues")
        return cls(
            (str(block_id) for block_id in stream),
            data.get("boundaries") if isinstance(data.get("boundaries"), list) else (),
            (str(issue) for issue in issues) if isinstance(issues, list) else (),
        )

    def _set_stream(self, stream: Iterable[str]) -> None:
        """Store the reading stream, keeping the first copy of a repeated id."""

        positions: dict[str, int] = {}
        kept: list[str] = []
        for block_id in stream:
            value = str(block_id)
            if value in positions:
                self.issues.append(
                    f"block {value} appears more than once in the reading stream; the "
                    "first position is kept"
                )
                continue
            positions[value] = len(kept)
            kept.append(value)
        self.stream = tuple(kept)
        self._order = positions

    def _set_boundaries(self, boundaries: Iterable[Any]) -> None:
        """Normalize boundaries against the stream and recompute the slice starts."""

        ordered: list[ArticleBoundary] = []
        seen: set[str] = set()
        for entry in boundaries:
            boundary = ArticleBoundary.from_dict(entry)
            if boundary is None:
                continue
            if boundary.block_id not in self._order:
                self.issues.append(
                    f"boundary {boundary.block_id} is not in the reading stream; dropped"
                )
                continue
            if boundary.block_id in seen:
                self.issues.append(
                    f"block {boundary.block_id} started more than one article; the first "
                    "boundary is kept"
                )
                continue
            seen.add(boundary.block_id)
            ordered.append(boundary)
        ordered.sort(key=lambda boundary: self._order[boundary.block_id])
        if self.stream and (not ordered or ordered[0].block_id != self.stream[0]):
            # The first block of the document always starts the first article.
            ordered.insert(0, ArticleBoundary(self.stream[0], SOURCE_AUTO))
        self.boundaries = ordered
        self._starts = [self._order[boundary.block_id] for boundary in ordered]

    # Shape ------------------------------------------------------------------

    def __len__(self) -> int:
        return len(self.boundaries)

    def __bool__(self) -> bool:
        return bool(self.boundaries)

    def __repr__(self) -> str:
        return (
            f"Segmentation(articles={len(self.boundaries)}, blocks={len(self.stream)}, "
            f"issues={len(self.issues)})"
        )

    @property
    def first_block_id(self) -> str:
        """The block the document starts at, or an empty string."""

        return self.stream[0] if self.stream else ""

    @property
    def article_numbers(self) -> tuple[str, ...]:
        """The number of every article, in reading order."""

        return tuple(boundary.number for boundary in self.boundaries)

    def is_deleted(self, block_id: str) -> bool:
        """Whether the article a block belongs to was flagged deleted."""

        article = self.article_position(block_id)
        return article is not None and self.boundaries[article].deleted

    def set_deleted(self, block_id: str, deleted: bool) -> bool:
        """Flag or unflag the article a block belongs to as deleted.

        The boundary itself stays: its blocks must remain owned and visible on
        the page, only the export skips them.
        """

        article = self.article_position(block_id)
        if article is None:
            return False
        current = self.boundaries[article]
        if current.deleted == deleted:
            return False
        self.boundaries[article] = ArticleBoundary(
            block_id=current.block_id,
            source=current.source,
            number=current.number,
            marker_block_id=current.marker_block_id,
            deleted=deleted,
        )
        return True

    def article_position(self, block_id: str) -> int | None:
        """Return the 0-based article a block belongs to, or ``None`` if unknown."""

        position = self._order.get(block_id)
        if position is None:
            return None
        return bisect_right(self._starts, position) - 1

    def block_ids(self, article: int) -> tuple[str, ...]:
        """Return the block ids of one 0-based article slice."""

        if not 0 <= article < len(self.boundaries):
            return ()
        start = self._starts[article]
        end = self._starts[article + 1] if article + 1 < len(self.boundaries) else len(self.stream)
        return self.stream[start:end]

    def block_ids_of(self, block_id: str) -> tuple[str, ...]:
        """Return the blocks of the article a block belongs to."""

        article = self.article_position(block_id)
        return () if article is None else self.block_ids(article)

    def number_of(self, block_id: str) -> str:
        """Return the number of the article a block belongs to, empty when unknown."""

        article = self.article_position(block_id)
        if article is None:
            return ""
        return self.boundaries[article].number

    def is_manual(self, block_id: str) -> bool:
        """Whether the article a block belongs to was split by hand."""

        article = self.article_position(block_id)
        return article is not None and self.boundaries[article].is_manual

    def summaries(self, blocks: Iterable[TextBlock]) -> list[ArticleSummary]:
        """Describe every article, deriving its pages and column from the blocks."""

        by_id = {block.id: block for block in blocks}
        summaries: list[ArticleSummary] = []
        for article, boundary in enumerate(self.boundaries):
            block_ids = self.block_ids(article)
            subset = [by_id[block_id] for block_id in block_ids if block_id in by_id]
            pages = [int(block.page) for block in subset]
            summaries.append(
                ArticleSummary(
                    index=article + 1,
                    boundary=boundary,
                    block_ids=block_ids,
                    start_page=min(pages) if pages else 0,
                    end_page=max(pages) if pages else 0,
                    columns=common_column(subset),
                )
            )
        return summaries

    def validate(self, blocks: Iterable[TextBlock]) -> list[str]:
        """Report where the stream and the document disagree.

        An empty result means the articles are an exact partition of the
        document: every block is owned by exactly one article and the stream
        invents nothing.
        """

        known = {block.id for block in blocks}
        problems = [
            f"block {block_id} is not in the reading stream, so no article owns it"
            for block_id in sorted(known - set(self.stream))
        ]
        problems.extend(
            f"stream entry {block_id} is not a block of this document"
            for block_id in sorted(set(self.stream) - known)
        )
        return problems

    # Edits ------------------------------------------------------------------

    def insert_boundary(
        self,
        block_id: str,
        *,
        source: str = SOURCE_MANUAL,
        number: str = "",
        marker_block_id: str = "",
    ) -> bool:
        """Start a new article at a block.

        Returns ``False`` when the cut is void: an unknown block, the first block
        of the document (which already starts the first article), or a block that
        already starts one.
        """

        position = self._order.get(block_id)
        if position is None or position == 0:
            return False
        if any(boundary.block_id == block_id for boundary in self.boundaries):
            return False
        self._set_boundaries(
            [
                *self.boundaries,
                ArticleBoundary(
                    block_id=block_id,
                    source=source if source in SOURCES else SOURCE_MANUAL,
                    number=number,
                    marker_block_id=marker_block_id,
                ),
            ]
        )
        return True

    def remove_boundary(self, block_id: str) -> bool:
        """Fold one article back into the one before it.

        Returns ``False`` when the cut is void: an unknown block, the first
        article's boundary (which the stream always keeps), or a block that does
        not start an article.
        """

        if not self.boundaries or self.boundaries[0].block_id == block_id:
            return False
        remaining = [boundary for boundary in self.boundaries if boundary.block_id != block_id]
        if len(remaining) == len(self.boundaries):
            return False
        self._set_boundaries(remaining)
        return True

    def set_number(self, block_id: str, number: str) -> bool:
        """Name the article a block belongs to, and report whether that exists."""

        article = self.article_position(block_id)
        if article is None:
            return False
        current = self.boundaries[article]
        self.boundaries[article] = ArticleBoundary(
            block_id=current.block_id,
            source=current.source,
            number=str(number),
            marker_block_id=current.marker_block_id,
            deleted=current.deleted,
        )
        return True

    # Persistence ------------------------------------------------------------

    def to_dict(self) -> dict[str, Any]:
        """Serialize for ``Document.metadata``.

        The stream is stored alongside the boundaries so a boundary can still be
        read as a position if the document is later re-extracted.
        """

        return {
            "version": 1,
            "stream": list(self.stream),
            "boundaries": [boundary.to_dict() for boundary in self.boundaries],
            "issues": list(self.issues),
        }

    def to_legacy_segments(self, blocks: Iterable[TextBlock]) -> list[dict[str, Any]]:
        """Render the boundaries into the legacy page/column segment dicts.

        Each article is written with reading-order bounds as well as its page
        range and columns, because a page range alone would re-merge articles
        that share a page. The legacy vocabulary reads those bounds as "split by
        hand", so ``boundary_source`` carries the real origin beside them.
        """

        by_id = {block.id: block for block in blocks}
        rendered: list[dict[str, Any]] = []
        for article, boundary in enumerate(self.boundaries):
            subset = [
                by_id[block_id]
                for block_id in self.block_ids(article)
                if block_id in by_id
            ]
            if not subset:
                continue
            pages = [int(block.page) for block in subset]
            columns = common_column(subset)
            segment: dict[str, Any] = {
                "index": article + 1,
                "abstract_number": boundary.number,
                "start_page": min(pages),
                "end_page": max(pages),
                "abstract_number_block_id": boundary.marker_block_id,
                "columns": list(columns) if columns else None,
                "boundary_source": boundary.source,
                "deleted": boundary.deleted,
            }
            start_order = reading_order(subset[0])
            end_order = reading_order(subset[-1])
            if start_order is not None:
                segment["start_order"] = start_order
            if end_order is not None:
                segment["end_order"] = end_order
            rendered.append(segment)
        return rendered
