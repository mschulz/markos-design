"""Read PDF page text in a bounded child process. pypdf never runs in the gateway process.

The child sets its own CPU and memory limits before it imports pypdf, and the parent kills it
after a deadline. Its socket guard stops accidental network use. It is not an OS sandbox.
"""

import asyncio
import contextlib
import io
import json
import sys

MAX_PDF_BYTES = 2 * 1024 * 1024
MAX_PAGES = 20
MAX_PAGE_CHARS = 65536
DEADLINE_SECONDS = 15
CPU_SECONDS = 10
MEMORY_BYTES = 512 * 1024 * 1024
ERRORS = {"not-pdf", "too-large", "encrypted", "too-many-pages", "too-much-text", "no-text"}
# At most two parsers run at once, so uploads cannot exhaust gateway memory or process slots.
SLOTS = asyncio.Semaphore(2)


class PdfError(ValueError):
    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


async def read_pages(body: bytes) -> list[str]:
    """Return the text of each page, or raise PdfError with a bounded code."""
    if len(body) > MAX_PDF_BYTES:
        raise PdfError("too-large")
    if not body.startswith(b"%PDF-"):
        raise PdfError("not-pdf")
    async with SLOTS:
        child = await asyncio.create_subprocess_exec(
            sys.executable,
            "-I",
            "-B",
            "-m",
            "markos_runtime.knowledge.pdf",
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.DEVNULL,
            env={},
        )
        try:
            output, _ = await asyncio.wait_for(child.communicate(body), DEADLINE_SECONDS)
        except TimeoutError:
            raise PdfError("unreadable") from None
        finally:
            if child.returncode is None:
                child.kill()
                await child.wait()
    try:
        result = json.loads(output)
        pages = result.get("pages")
        if isinstance(pages, list) and pages and all(isinstance(p, str) for p in pages):
            return pages
        code = result.get("error")
    except (ValueError, AttributeError):
        code = None
    raise PdfError(code if code in ERRORS else "unreadable")


def parse(data: bytes) -> list[str]:
    from pypdf import PdfReader

    if len(data) > MAX_PDF_BYTES:
        raise PdfError("too-large")
    reader = PdfReader(io.BytesIO(data))
    if reader.is_encrypted:
        raise PdfError("encrypted")
    if len(reader.pages) > MAX_PAGES:
        raise PdfError("too-many-pages")
    pages = [page.extract_text() for page in reader.pages]
    if any(len(page) > MAX_PAGE_CHARS for page in pages):
        raise PdfError("too-much-text")
    if not any(page.strip() for page in pages):
        # Scanned bills hold images, which text extraction cannot read.
        raise PdfError("no-text")
    return pages


def main() -> None:
    import resource
    import socket

    for limit, value in ((resource.RLIMIT_CPU, CPU_SECONDS), (resource.RLIMIT_AS, MEMORY_BYTES)):
        # macOS does not support RLIMIT_AS. The parent's deadline still applies.
        with contextlib.suppress(ValueError, OSError):
            resource.setrlimit(limit, (value, value))

    def no_network(*args: object, **kwargs: object) -> None:
        raise OSError("network access is not allowed while reading a PDF")

    socket.socket = no_network  # type: ignore[assignment,misc]
    try:
        result: dict[str, object] = {"pages": parse(sys.stdin.buffer.read(MAX_PDF_BYTES + 1))}
    except PdfError as error:
        result = {"error": error.code}
    except Exception:
        result = {"error": "unreadable"}
    sys.stdout.write(json.dumps(result))


if __name__ == "__main__":
    main()
