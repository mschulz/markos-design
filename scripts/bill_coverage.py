"""Count how well each bill profile reads a private folder of PDF bills. Prints counts only.

Usage: uv run --no-sync python scripts/bill_coverage.py FOLDER

The output never holds file names, amounts, dates or other bill text, so it is safe to share.
Do not commit the folder or the output.
"""

import asyncio
import hashlib
import sys
from collections import Counter
from pathlib import Path

from markos_runtime.api.bills import PDF_PROFILES
from markos_runtime.knowledge.pdf import PdfError, read_pages


async def main(folder: Path) -> int:
    bills: Counter[str] = Counter()
    states: dict[str, Counter[str]] = {}
    for path in sorted(folder.glob("*.pdf")):
        body = path.read_bytes()
        try:
            pages = await read_pages(body)
        except PdfError:
            bills["unreadable"] += 1
            continue
        name = next((n for n, p in PDF_PROFILES.items() if p[0](pages)), None)
        if name is None:
            bills["unsupported"] += 1
            continue
        report = PDF_PROFILES[name][1](pages, hashlib.sha256(body).hexdigest())
        again = PDF_PROFILES[name][1](pages, hashlib.sha256(body).hexdigest())
        bills[name] += 1
        if report != again:
            bills[name + " not repeatable"] += 1
        states.setdefault(name, Counter()).update(f.state for f in report.facts)
        if any(f.state != "found" for f in report.facts if f.key == "total_due"):
            bills[name + " total not established"] += 1
    for key, count in sorted(bills.items()):
        print(f"bills {key}: {count}")
    for name, counter in sorted(states.items()):
        print(f"facts {name}: " + ", ".join(f"{s} {n}" for s, n in sorted(counter.items())))
    return 0


if __name__ == "__main__":
    if len(sys.argv) != 2 or not Path(sys.argv[1]).is_dir():
        sys.exit("usage: bill_coverage.py FOLDER")
    sys.exit(asyncio.run(main(Path(sys.argv[1]))))
