from pdf_to_jats.core.author_linker import AuthorLinker


def test_surname_first_author_list_keeps_initials_and_surname_apart():
    """Conference supplements write "Nahas, S.J.1; Zhao, Y.2"."""

    linked = AuthorLinker().link_from_texts(
        ["Nahas, S.J.1; Zhao, Y.2; Graham, C.3; Erbe, A.3; Blumenfeld, A.M.4"],
        [],
    )

    assert [(a.initials, a.surname) for a in linked.authors] == [
        ("S.J.", "Nahas"),
        ("Y.", "Zhao"),
        ("C.", "Graham"),
        ("A.", "Erbe"),
        ("A.M.", "Blumenfeld"),
    ]


def test_address_tails_are_not_turned_into_authors():
    """"Los Angeles" and "San Diego" used to be emitted as authors here."""

    for line in (
        "Headache Center, Philadelphia, PA, USA, Philadelphia, PA, USA;",
        "USA; 4The Los Angeles Headache Center, Los Angeles and San Diego,",
        "CA, USA, Los Angeles, CA, USA",
    ):
        linked = AuthorLinker().link_from_texts([line], [])
        surnames = {a.surname for a in linked.authors}
        assert "Angeles" not in surnames
        assert "Diego" not in surnames


def test_compound_surname_and_affiliation_ids_are_preserved():
    linked = AuthorLinker().link_from_texts(
        [
            "João De Holanda Farias20",
            "Jing Christine Ye13",
            "Matthew J. Pianko33",
            "Chang-Ki Min10",
        ],
        ["1 A; 10 B; 13 C; 20 D; 33 E"],
    )

    assert linked.authors[0].given_names == "João"
    assert linked.authors[0].surname == "De Holanda Farias"
    assert linked.authors[0].affiliation_ids == ["20"]
    assert linked.authors[1].surname == "Ye"
    assert linked.authors[1].initials == "J.C."
    assert linked.authors[2].initials == "M.J."
    assert linked.authors[3].surname == "Min"


def test_single_author_takes_every_affiliation_and_is_flagged():
    """CAR: one author and several affiliations -> the author receives them all.

    The link is inferred rather than printed, so it is also reported as a
    warning. This replaces the previous behaviour, which dropped the linkage
    entirely and left the author with no affiliation group in the ANI output.
    """

    linked = AuthorLinker().link_from_texts(["Jane Doe"], ["1 A; 2 B"])

    assert linked.authors[0].affiliation_ids == ["1", "2"]
    assert any(issue.severity == "warning" for issue in linked.issues)


def test_single_affiliation_is_shared_by_every_author():
    """CAR: many authors and one affiliation -> that affiliation fits them all."""

    linked = AuthorLinker().link_from_texts(["Jane Doe", "John Smith"], ["1 A"])

    assert [author.affiliation_ids for author in linked.authors] == [["1"], ["1"]]


def test_letter_and_symbol_markers_are_matched():
    linked = AuthorLinker().link_from_texts(
        ["Jane Doe a", "John Smith*"],
        ["a Alpha University", "* Beta Institute"],
    )

    assert linked.authors[0].affiliation_ids == ["a"]
    assert linked.authors[1].affiliation_ids == ["*"]


def test_marker_ranges_and_lists_are_expanded():
    linked = AuthorLinker().link_from_texts(
        ["Jane Doe1,3", "John Smith2"],
        ["1 Alpha; 2 Beta; 3 Gamma"],
    )

    assert linked.authors[0].affiliation_ids == ["1", "3"]
    assert linked.authors[1].affiliation_ids == ["2"]


def test_digits_inside_affiliation_text_are_not_markers():
    """"Center 2, Boston" must not yield the marker 2 for its own block."""

    linked = AuthorLinker().link_from_texts(
        ["Jane Doe1", "John Smith2"],
        ["1 Alpha; 2 Center 2, Boston"],
    )

    assert {aff.id for aff in linked.affiliations} == {"1", "2"}
    assert linked.authors[1].affiliation_ids == ["2"]


def test_affiliations_follow_first_author_mention_order():
    linked = AuthorLinker().link_from_texts(
        ["Jane Doe2", "John Smith1"],
        ["1 Alpha; 2 Beta"],
    )

    assert [aff.id for aff in linked.affiliations] == ["2", "1"]


def test_repeated_affiliation_text_is_deduplicated():
    linked = AuthorLinker().link_from_texts(
        ["Jane Doe1", "John Smith2"],
        ["1 Alpha University; 2 Alpha University"],
    )

    assert len(linked.affiliations) == 1
    assert linked.authors[0].affiliation_ids == linked.authors[1].affiliation_ids


def test_author_with_two_affiliations_keeps_both():
    linked = AuthorLinker().link_from_texts(
        ["Jane Doe1,2", "John Smith1"],
        ["1 Alpha; 2 Beta"],
    )

    assert linked.authors[0].affiliation_ids == ["1", "2"]


def test_unreferenced_affiliation_is_kept_and_flagged():
    linked = AuthorLinker().link_from_texts(["Jane Doe1"], ["1 Alpha; 2 Beta"])

    assert [aff.id for aff in linked.affiliations] == ["1", "2"]
    assert any(
        issue.affiliation_id == "2" and issue.severity == "warning" for issue in linked.issues
    )


def test_unresolved_marker_is_left_unlinked_and_flagged():
    linked = AuthorLinker().link_from_texts(
        ["Jane Doe9", "John Smith1"],
        ["1 Alpha; 2 Beta"],
    )

    assert linked.authors[0].affiliation_ids == []
    assert any(
        issue.severity == "error" and issue.author_id == linked.authors[0].id
        for issue in linked.issues
    )


def test_no_markers_with_several_authors_is_not_assumed():
    """CAR: >1 authors, >1 affiliations, no indicators -> do not assume."""

    linked = AuthorLinker().link_from_texts(["Jane Doe", "John Smith"], ["1 A; 2 B"])

    assert [author.affiliation_ids for author in linked.authors] == [[], []]
    assert len([issue for issue in linked.issues if issue.severity == "warning"]) >= 2


def test_two_authors_with_inline_institutions_stay_separate():
    """Each author's own institution must not fold into the other's.

    The wrap-fold used to merge both institutions into one affiliation that
    every author then pointed at.
    """

    linked = AuthorLinker().link_from_texts(
        ["J. Smith, Boston University", "A. Lee, Massachusetts Institute of Technology"],
        [],
    )

    assert [author.display_name for author in linked.authors] == ["J. Smith", "A. Lee"]
    assert [aff.text for aff in linked.affiliations] == [
        "Boston University",
        "Massachusetts Institute of Technology",
    ]
    # The institution sharing an author's line is that author's affiliation,
    # so no marker is needed and nothing is left to flag.
    assert [author.affiliation_ids for author in linked.authors] == [["aff_1"], ["aff_2"]]
    assert linked.issues == []


def test_semicolon_entries_keep_the_second_author_and_its_institution():
    """"J. Smith, Boston University; A. Lee, MIT" used to lose A. Lee."""

    linked = AuthorLinker().link_from_texts(
        ["J. Smith, Boston University; A. Lee, MIT"], []
    )

    assert [author.display_name for author in linked.authors] == ["J. Smith", "A. Lee"]
    assert [aff.text for aff in linked.affiliations] == ["Boston University", "MIT"]
    assert [author.affiliation_ids for author in linked.authors] == [["aff_1"], ["aff_2"]]


def test_inline_institution_reuses_the_printed_affiliation():
    """The same institution on the line and in a block is one entry."""

    linked = AuthorLinker().link_from_texts(
        ["J. Smith, Boston University"], ["Boston University, Chestnut Hill, USA"]
    )

    assert [aff.text for aff in linked.affiliations] == ["Boston University, Chestnut Hill, USA"]
    assert linked.authors[0].affiliation_ids == [linked.affiliations[0].id]
    assert linked.issues == []


def test_address_tail_keeps_the_author_name():
    """No institution keyword on the line, yet the person must survive."""

    linked = AuthorLinker().link_from_texts(["Jane Doe, Philadelphia, PA, USA"], [])

    assert [author.display_name for author in linked.authors] == ["Jane Doe"]
    assert [aff.text for aff in linked.affiliations] == ["Philadelphia, PA, USA"]
    assert linked.authors[0].affiliation_ids == [linked.affiliations[0].id]


def test_inline_marker_still_wins_over_the_embedded_institution():
    """A printed marker points at the printed block, not a shorter duplicate."""

    linked = AuthorLinker().link_from_texts(
        ["J. Smith1, Boston University", "A. Lee2, MIT"],
        ["1 Boston University, USA", "2 MIT, USA"],
    )

    assert [aff.id for aff in linked.affiliations] == ["1", "2"]
    assert [author.affiliation_ids for author in linked.authors] == [["1"], ["2"]]
    assert linked.issues == []


def test_inline_affiliation_is_not_broadcast_to_the_rest_of_the_byline():
    """Only the author on the institution's line carries it.

    "Jennifer Warnock, Oak Ridge National Laboratory" followed by eight
    unmarked co-authors: the CAR single-affiliation rule used to hand Oak
    Ridge to every name in the byline.
    """

    linked = AuthorLinker().link_from_texts(
        [
            "Jennifer Warnock, Oak Ridge National Laboratory",
            "Sharique Khan, Wellington Leite, Qiu Zhang, Gregory Hura",
        ],
        [],
    )

    by_name = {author.display_name: author.affiliation_ids for author in linked.authors}
    assert len(by_name) == 5
    assert by_name["Jennifer Warnock"] == ["aff_1"]
    assert by_name["Sharique Khan"] == []
    assert by_name["Gregory Hura"] == []
    assert [aff.text for aff in linked.affiliations] == ["Oak Ridge National Laboratory"]
    # The unmarked co-authors are reported rather than quietly given an
    # institution the source never printed for them.
    assert len([issue for issue in linked.issues if issue.severity == "warning"]) == 4


def test_wrapped_byline_rows_split_on_line_breaks():
    """A row break inside one block must not merge names into the institution."""

    linked = AuthorLinker().link_from_texts(
        [
            "Jennifer Warnock, Oak Ridge National Laboratory\n"
            "Sharique Khan, Qiu Zhang"
        ],
        [],
    )

    assert [author.display_name for author in linked.authors] == [
        "Jennifer Warnock",
        "Sharique Khan",
        "Qiu Zhang",
    ]
    assert linked.authors[0].affiliation_ids == ["aff_1"]
    assert linked.authors[1].affiliation_ids == []
    assert [aff.text for aff in linked.affiliations] == ["Oak Ridge National Laboratory"]


def test_printed_single_affiliation_is_still_shared_by_every_author():
    """A block-printed affiliation keeps the CAR behaviour: it fits them all."""

    linked = AuthorLinker().link_from_texts(
        ["Jennifer Warnock", "Sharique Khan"], ["Oak Ridge National Laboratory"]
    )

    assert [author.affiliation_ids for author in linked.authors] == [["aff_1"], ["aff_1"]]
