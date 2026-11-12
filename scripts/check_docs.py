"""Check the authority inventory, current metadata and local document links.

Standard library only; no application imports, network access or document writes.
Historical documents remain inventoried but their old links are non-normative.
"""

import argparse
from pathlib import Path
import re
import subprocess
from urllib.parse import unquote, urlsplit


INDEX = Path("Documentation Authority Index.md")
ROW = re.compile(r"^\| \[([^\]]+)\]\(([^)]+)\) \| (.*?) \|$", re.M)
LINK = re.compile(r"!?\[[^\]\n]*\]\(([^)\n]+)\)")
WIKI = re.compile(r"\[\[([^\]|#]+)(?:[^\]]*)\]\]")
REQUIRED = ("title", "type", "status", "version", "owner", "created", "updated", "reviewed")


def check(root: Path, tracked: set[str]) -> tuple[list[str], int]:
    root = root.resolve()
    errors: list[str] = []
    files = {name for name in tracked if name.endswith(".md")}
    rows = ROW.findall((root / INDEX).read_text(encoding="utf-8"))
    inventory = [unquote(target) for _, target, _ in rows]
    if len(inventory) != len(set(inventory)):
        errors.append("Duplicate inventory entries")
    for name in sorted(files - set(inventory)):
        errors.append(f"Unclassified Markdown: {name}")
    for name in sorted(set(inventory) - files):
        errors.append(f"Inventory target is not tracked Markdown: {name}")

    checked = 0
    for _, target, disposition in rows:
        name = unquote(target)
        if name not in files:
            continue
        historical = any(marker in disposition.lower() for marker in
                         ("historical", "superseded", "exact archived"))
        if historical and name != "CHANGELOG.md":
            continue
        checked += 1
        content = (root / name).read_text(encoding="utf-8")
        # Existing root architecture docs use front matter; contributor READMEs do not.
        if Path(name).parent == Path(".") and name not in {
            "README.md", "AGENTS.md", "CLAUDE.md", "CHANGELOG.md"
        }:
            parts = content.split("---", 2)
            if not content.startswith("---\n") or len(parts) != 3:
                errors.append(f"{name}: missing front matter")
            else:
                for key in REQUIRED:
                    if not re.search(rf"^{key}:\s*\S.*$", parts[1], re.M):
                        errors.append(f"{name}: missing metadata {key}")
        # Fenced examples are not links or instructions to access old local paths.
        content = re.sub(r"(?ms)^(`{3,}|~{3,}).*?^\1[^\n]*$", "", content)
        links = [(match.strip().strip("<>"), (root / name).parent)
                 for match in LINK.findall(content)]
        # Exact-title wikilinks resolve from the repository root, even in subfolders.
        links += [(target.strip() + ".md", root) for target in WIKI.findall(content)]
        for target, base in links:
            parsed = urlsplit(target)
            if parsed.scheme or not parsed.path:
                continue
            resolved = (base / unquote(parsed.path)).resolve()
            if not resolved.is_relative_to(root):
                errors.append(f"{name}: local link escapes repository: {target}")
                continue
            relative = resolved.relative_to(root).as_posix()
            if relative not in tracked or not resolved.is_file():
                errors.append(f"{name}: missing tracked local link: {target}")
    return errors, checked


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parent.parent)
    root = parser.parse_args().root.resolve()
    result = subprocess.run(["git", "ls-files", "-z"], cwd=root, check=True,
                            capture_output=True, text=True)
    tracked = set(result.stdout.rstrip("\0").split("\0"))
    errors, checked = check(root, tracked)
    for error in errors:
        print(error)
    if errors:
        return 1
    print(f"PASS: {sum(name.endswith('.md') for name in tracked)} Markdown documents "
          f"inventoried; metadata/local links checked in {checked} current/template documents.")
    print("Historical links and external URLs are not checked; this is not runtime acceptance.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
