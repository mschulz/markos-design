from markos.links import resolve_internal_link


def test_resolves_relative_rooted_and_extensionless_paths() -> None:
    paths = ("Projects/Current.md", "Projects/Plan.md", "Vision.markdown")

    assert resolve_internal_link("Projects/Current.md", "Plan.md", paths) == "Projects/Plan.md"
    assert resolve_internal_link("Projects/Current.md", "/Vision", paths) == "Vision.markdown"
    assert resolve_internal_link("Projects/Current.md", "../Vision", paths) == "Vision.markdown"


def test_resolves_case_insensitively_and_preserves_canonical_path() -> None:
    paths = ("Projects/RoadMap.md",)

    assert resolve_internal_link("Home.md", "projects/roadmap", paths) == "Projects/RoadMap.md"


def test_prefers_same_directory_before_unique_basename() -> None:
    paths = ("Projects/Current.md", "Projects/Plan.md", "Archive/Plan.md")

    assert resolve_internal_link("Projects/Current.md", "Plan", paths) == "Projects/Plan.md"


def test_leaves_ambiguous_external_fragment_and_missing_targets_unresolved() -> None:
    paths = ("Projects/Plan.md", "Archive/Plan.md")

    assert resolve_internal_link("Home.md", "Plan", paths) is None
    assert resolve_internal_link("Home.md", "https://example.com", paths) is None
    assert resolve_internal_link("Home.md", "#section", paths) is None
    assert resolve_internal_link("Home.md", "Missing", paths) is None
