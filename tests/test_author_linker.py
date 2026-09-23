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


def test_missing_affiliation_marker_does_not_assign_all_affiliations():
    linked = AuthorLinker().link_from_texts(["Jane Doe"], ["1 A; 2 B"])

    assert linked.authors[0].affiliation_ids == []
