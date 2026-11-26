"""Owner corrections over an immutable extracted report.

The stored report never changes. The effective view puts the latest correction of each fact on
top of it, keeps the bill's citations, and reruns the profile's summary arithmetic.
"""

import re
from collections.abc import Callable
from datetime import date
from decimal import Decimal, InvalidOperation

from pydantic import Field, field_validator

from markos_runtime.contracts.control import ControlValue
from markos_runtime.knowledge import amber, origin
from markos_runtime.knowledge.bills import BillCorrection, BillFacts, BillKey, plain_text

MAX_REASON = 200
SIGNED: set[BillKey] = {"brought_forward", "total_owed"}  # A credit balance is negative.
# Per profile: money facts, the ones the arithmetic needs, and the arithmetic itself.
CHECKS: dict[
    str, tuple[set[BillKey], set[BillKey], Callable[[dict[BillKey, Decimal]], list[str]]]
] = {
    "amber-pdf-v1": (amber.MONEY, amber.MONEY, amber.totals_problems),
    "origin-pdf-v1": (origin.MONEY, origin.MONEY - {"meter_charge"}, origin.totals_problems),
}


class CorrectionRequest(ControlValue):
    key: BillKey
    value: str | None = Field(max_length=40)  # None withdraws earlier corrections of this fact.
    reason: str = Field(min_length=1, max_length=MAX_REASON)

    @field_validator("reason")
    @classmethod
    def plain(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("a reason is required")
        return plain_text(value)


def normalise(key: BillKey, value: str) -> str:
    """The stored form of a corrected value, or ValueError."""
    value = value.strip()
    if key == "period":
        match = re.fullmatch(r"(\d{4}-\d{2}-\d{2}) to (\d{4}-\d{2}-\d{2})", value)
        if not match or date.fromisoformat(match[1]) > date.fromisoformat(match[2]):
            raise ValueError("period must be YYYY-MM-DD to YYYY-MM-DD")
        return value
    if key == "due_date":
        return str(date.fromisoformat(value))
    places = 3 if key == "usage_kwh" else 2
    sign = "-?" if key in SIGNED else ""
    if not re.fullmatch(rf"{sign}[0-9]{{1,9}}(?:\.[0-9]{{1,{places}}})?", value.replace(",", "")):
        raise ValueError("a plain number is required")
    try:
        return format(Decimal(value.replace(",", "")), f".{places}f")
    except InvalidOperation:
        raise ValueError("a plain number is required") from None


def effective(report: BillFacts, corrections: list[BillCorrection]) -> BillFacts:
    latest = {c.key: c for c in sorted(corrections, key=lambda c: c.correction_id)}
    active = {key: c.value for key, c in latest.items() if c.value is not None}
    if not active:
        return report
    facts = [
        f.model_copy(update={"state": "corrected", "value": active[f.key]})
        if f.key in active
        else f
        for f in report.facts
    ]
    notices = [
        *report.notices,
        f"You corrected {len(active)} figure(s). Answers use your values and still cite the bill.",
    ]
    check = CHECKS.get(report.extractor)
    if check is None or not active.keys() & check[0]:
        return report.model_copy(update={"facts": facts, "notices": notices})
    money, required, totals = check
    values = {
        f.key: Decimal(f.value)
        for f in facts
        if f.key in money and f.state in {"found", "corrected"} and f.value is not None
    }
    present = {f.key for f in facts if f.key in money and f.state != "missing"}
    if not values.keys() >= required | present:
        problems = ["some figures are still unclear, so the totals were not checked"]
    else:
        problems = totals(values)
    if problems:
        notices += ["Needs review after your corrections: " + p + "." for p in problems]
        facts = [
            f.model_copy(update={"state": "unreconciled", "value": None})
            if f.key in money and f.state in {"found", "corrected"}
            else f
            for f in facts
        ]
    else:
        notices.append("The totals add up using your corrections.")
    return report.model_copy(update={"facts": facts, "notices": notices})
