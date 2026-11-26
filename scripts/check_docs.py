"""Fail when a tracked Markdown file links to a local file that is not tracked.

Checks Markdown links and [[wikilinks]] (resolved from the repository root).
External URLs are not checked. CHANGELOG.md is history, so its links are skipped.
"""

import re
import subprocess
import sys
from pathlib import Path
from urllib.parse import unquote, urlsplit

LINK = re.compile(r"!?\[[^\]\n]*\]\(([^)\n]+)\)")
WIKI = re.compile(r"\[\[([^\]|#]+)(?:[^\]]*)\]\]")
FENCE = re.compile(r"(?ms)^(`{3,}|~{3,}).*?^\1[^\n]*$")
SKIP = {"CHANGELOG.md"}


def check(root: Path, tracked: set[str]) -> list[str]:
    root = root.resolve()
    errors = []
    for name in sorted(n for n in tracked if n.endswith(".md") and n not in SKIP):
        content = FENCE.sub("", (root / name).read_text(encoding="utf-8"))
        links = [(m.strip().strip("<>"), (root / name).parent) for m in LINK.findall(content)]
        links += [(t.strip() + ".md", root) for t in WIKI.findall(content)]
        for target, base in links:
            parts = urlsplit(target)
            if parts.scheme or not parts.path:
                continue
            resolved = (base / unquote(parts.path)).resolve()
            if not resolved.is_relative_to(root):
                errors.append(f"{name}: link escapes repository: {target}")
            elif resolved.relative_to(root).as_posix() not in tracked:
                errors.append(f"{name}: broken local link: {target}")
    return errors


def main() -> int:
    root = Path(__file__).resolve().parent.parent
    listing = subprocess.run(
        ["git", "ls-files", "-z"], cwd=root, check=True, capture_output=True, text=True
    )
    errors = check(root, set(listing.stdout.rstrip("\0").split("\0")))
    print("\n".join(errors) or "PASS: all local Markdown links resolve.")
    return 1 if errors else 0


if __name__ == "__main__":
    sys.exit(main())
