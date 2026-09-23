from pdf_to_jats.core.author_linker import AuthorLinker


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
