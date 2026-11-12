"""Synthetic failure cases for the documentation CI gate."""

from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from check_docs import check  # noqa: E402


class DocumentationChecks(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.tracked = {"Documentation Authority Index.md", "README.md", "Old.md"}
        self.index = (
            "| [Index](Documentation%20Authority%20Index.md) | Current authority |\n"
            "| [Readme](README.md) | Current entry point |\n"
            "| [Old](Old.md) | Exact archived snapshot |\n"
        )
        front = "---\n" + "".join(f"{k}: value\n" for k in
                ("title", "type", "status", "version", "owner", "created", "updated", "reviewed")) + "---\n"
        (self.root / "Documentation Authority Index.md").write_text(front + self.index)
        (self.root / "README.md").write_text("[Old](Old.md)\n[[Old]]\n")
        (self.root / "Old.md").write_text("[Old missing link](missing.md)\n")

    def test_current_links_pass_and_archived_links_are_preserved(self):
        errors, count = check(self.root, self.tracked)
        self.assertEqual(errors, [])
        self.assertEqual(count, 2)

    def test_broken_markdown_and_wiki_links_fail(self):
        (self.root / "README.md").write_text("[Broken](missing.md)\n[[Absent]]")
        errors, _ = check(self.root, self.tracked)
        self.assertEqual(sum("missing tracked local link" in e for e in errors), 2)

    def test_subfolder_wikilinks_resolve_from_root(self):
        (self.root / "sub").mkdir()
        (self.root / "sub" / "README.md").write_text("[[Old]]\n[Old](../Old.md)")
        p = self.root / "Documentation Authority Index.md"
        p.write_text(p.read_text() + "| [Sub](sub/README.md) | Current |\n")
        errors, _ = check(self.root, self.tracked | {"sub/README.md"})
        self.assertEqual(errors, [])

    def test_unclassified_markdown_fails(self):
        errors, _ = check(self.root, self.tracked | {"New.md"})
        self.assertIn("Unclassified Markdown: New.md", errors)

    def test_stale_and_duplicate_inventory_fail(self):
        p = self.root / "Documentation Authority Index.md"
        p.write_text(p.read_text() + self.index + "| [Gone](Gone.md) | Current |\n")
        errors, _ = check(self.root, self.tracked)
        self.assertIn("Duplicate inventory entries", errors)
        self.assertIn("Inventory target is not tracked Markdown: Gone.md", errors)

    def test_repository_escape_fails(self):
        (self.root / "README.md").write_text("[Outside](../outside.md)")
        errors, _ = check(self.root, self.tracked)
        self.assertTrue(any("escapes repository" in e for e in errors))

    def test_missing_metadata_fails(self):
        p = self.root / "Documentation Authority Index.md"
        p.write_text(p.read_text().replace("status: value\n", ""))
        errors, _ = check(self.root, self.tracked)
        self.assertTrue(any("missing metadata status" in e for e in errors))


if __name__ == "__main__":
    unittest.main()
