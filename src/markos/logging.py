"""Application logging setup."""

import logging

from rich.logging import RichHandler

from markos.settings import LogLevel


def configure_logging(level: LogLevel = "INFO") -> None:
    """Configure concise console logging for the application."""

    logging.basicConfig(
        level=level,
        format="%(message)s",
        datefmt="[%X]",
        handlers=[RichHandler(rich_tracebacks=True, show_path=False)],
        force=True,
    )
