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
