"""Supplier PDF profiles by extractor name: (recognises the pages, extracts the facts)."""

from collections.abc import Callable

from markos_runtime.knowledge.amber import extract_amber, is_amber
from markos_runtime.knowledge.bills import BillFacts
from markos_runtime.knowledge.origin import extract_origin, is_origin

PDF_PROFILES: dict[
    str, tuple[Callable[[list[str]], bool], Callable[[list[str], str], BillFacts]]
] = {
    "amber-pdf-v1": (is_amber, extract_amber),
    "origin-pdf-v1": (is_origin, extract_origin),
}
