from markos.citations import Citation, parse_citations

_ACTIVE_BADGE_REFERENCES = (
    "# Active Badge\n"
    "\n"
    "## References\n"
    "\n"
    "- Want, R., Hopper, A., Falcão, V., Gibbons, J. "
    "*The Active Badge Location System.* "
    "ACM Transactions on Information Systems, January 1992.\n"
    "- Want, R., Hopper, A. *Active Badges and Personal Interactive "
    "Computing Objects.* IEEE Transactions on Consumer Electronics, "
    "February 1992.\n"
    "- Elrod, S., Hall, G., Costanza, R., Dixon, M., Rivieres, J. "
    "*Responsive Office Environments.* Communications of the ACM, "
    "July 1993.\n"
    "\n"
    "## Entity Summary\n"
    "\n"
    "- Not a reference.\n"
)


def test_parses_active_badge_references() -> None:
    citations = parse_citations(_ACTIVE_BADGE_REFERENCES)

    assert citations == (
        Citation(
            raw=(
                "Want, R., Hopper, A., Falcão, V., Gibbons, J. "
                "*The Active Badge Location System.* "
                "ACM Transactions on Information Systems, January 1992."
            ),
            authors=("Want, R.", "Hopper, A.", "Falcão, V.", "Gibbons, J."),
            title="The Active Badge Location System",
            venue="ACM Transactions on Information Systems, January 1992",
            year=1992,
            start_line=5,
            end_line=5,
        ),
        Citation(
            raw=(
                "Want, R., Hopper, A. *Active Badges and Personal Interactive "
                "Computing Objects.* IEEE Transactions on Consumer Electronics, "
                "February 1992."
            ),
            authors=("Want, R.", "Hopper, A."),
            title="Active Badges and Personal Interactive Computing Objects",
            venue="IEEE Transactions on Consumer Electronics, February 1992",
            year=1992,
            start_line=6,
            end_line=6,
        ),
        Citation(
            raw=(
                "Elrod, S., Hall, G., Costanza, R., Dixon, M., Rivieres, J. "
                "*Responsive Office Environments.* Communications of the ACM, "
                "July 1993."
            ),
            authors=("Elrod, S.", "Hall, G.", "Costanza, R.", "Dixon, M.", "Rivieres, J."),
            title="Responsive Office Environments",
            venue="Communications of the ACM, July 1993",
            year=1993,
            start_line=7,
            end_line=7,
        ),
    )


def test_ignores_list_items_outside_references_section() -> None:
    citations = parse_citations(_ACTIVE_BADGE_REFERENCES)

    assert all("Not a reference" not in citation.raw for citation in citations)


def test_no_references_heading_returns_empty() -> None:
    assert parse_citations("# Title\n\nSome prose with no bibliography.\n") == ()


def test_references_heading_without_list_returns_empty() -> None:
    text = "## References\n\nSee the appendix.\n\n## Next\n\n- unrelated item\n"

    assert parse_citations(text) == ()


def test_skips_list_items_without_italic_title() -> None:
    text = "## References\n\n- Just some plain text, no title markup.\n"

    assert parse_citations(text) == ()


def test_skips_list_items_without_parseable_authors() -> None:
    text = "## References\n\n- *Only A Title.* Some Venue, 2001.\n"

    assert parse_citations(text) == ()


def test_citation_without_year() -> None:
    text = "## References\n\n- Doe, J. *Undated Work.* Some Journal.\n"

    citations = parse_citations(text)

    assert len(citations) == 1
    assert citations[0].year is None
    assert citations[0].venue == "Some Journal"


def test_citation_source_location() -> None:
    citations = parse_citations(_ACTIVE_BADGE_REFERENCES)

    assert [citation.source_location for citation in citations] == ["L5", "L6", "L7"]
