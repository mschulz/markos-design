"""Failure cases for the documentation link check."""

import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from check_docs import check  # noqa: E402


class LinkChecks(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        (self.root / "docs").mkdir()
        (self.root / "docs" / "Guide.md").write_text("[Readme](../README.md)\n")
        (self.root / "README.md").write_text("[Guide](docs/Guide.md)\n[[docs/Guide]]\n")
        self.tracked = {"README.md", "docs/Guide.md"}

    def test_valid_links_pass(self):
        self.assertEqual(check(self.root, self.tracked), [])

    def test_broken_markdown_and_wiki_links_fail(self):
        (self.root / "README.md").write_text("[Broken](missing.md)\n[[Absent]]\n")
        self.assertEqual(len(check(self.root, self.tracked)), 2)

    def test_untracked_target_fails(self):
        (self.root / "Draft.md").write_text("draft\n")
        (self.root / "README.md").write_text("[Draft](Draft.md)\n")
        self.assertEqual(len(check(self.root, self.tracked)), 1)

    def test_repository_escape_fails(self):
        (self.root / "README.md").write_text("[Outside](../outside.md)\n")
        self.assertIn("escapes repository", check(self.root, self.tracked)[0])

    def test_urls_fenced_code_and_changelog_are_ignored(self):
        (self.root / "README.md").write_text("[Web](https://example.com)\n```\n[x](gone.md)\n```\n")
        (self.root / "CHANGELOG.md").write_text("[Old](gone.md)\n")
        self.assertEqual(check(self.root, self.tracked | {"CHANGELOG.md"}), [])


if __name__ == "__main__":
    unittest.main()
