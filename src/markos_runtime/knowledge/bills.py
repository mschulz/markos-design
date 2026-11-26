"""Conservative bill-text extraction and answers backed by exact original lines."""

import hashlib
import re
from datetime import date
from decimal import Decimal
from typing import Literal
from uuid import UUID

from pydantic import Field, field_validator

from markos_runtime.contracts.control import ControlValue

MAX_TEXT_BYTES = 16384
BillKey = Literal[
    "period",
    "usage_kwh",
    "usage_charge",
    "supply_charge",
    "export_credit",
    "total_due",
    # Supplier profiles (knowledge/amber.py) add these.
    "demand_charge",
    "subscription",
    "charges_gst",
    "charges_total",
    "concessions",
    "credits_gst",
    "credits_total",
    "total_owed",
    "due_date",
]
FactState = Literal["found", "missing", "conflicting", "invalid", "unreconciled"]
KEYS: dict[BillKey, str] = {
    "period": "Billing period",
    "usage_kwh": "Electricity used",
    "usage_charge": "Usage charge",
    "supply_charge": "Supply charge",
    "export_credit": "Export credit",
    "total_due": "Amount due",
}
PATTERNS: dict[BillKey, str] = {
    "period": r"(?:Billing period|Bill period):\s*(\d{4}-\d{2}-\d{2}\s+to\s+\d{4}-\d{2}-\d{2})",
    "usage_kwh": r"(?:Electricity used|Total usage|Usage):\s*([0-9,]+(?:\.[0-9]{1,3})?)\s*kWh",
    "usage_charge": r"(?:Usage charge|Usage charges):\s*(?:AUD\s*)?\$([0-9,]+(?:\.[0-9]{2})?)",
    "supply_charge": r"(?:Supply charge|Supply charges):\s*(?:AUD\s*)?\$([0-9,]+(?:\.[0-9]{2})?)",
    "export_credit": r"(?:Export credit|Solar credit):\s*(?:AUD\s*)?\$([0-9,]+(?:\.[0-9]{2})?)",
    "total_due": (
        r"(?:Total due|Amount due|Total amount due):\s*"
        r"(?:AUD\s*)?\$([0-9,]+(?:\.[0-9]{2})?)"
    ),
}
SAMPLE = """SAMPLE ONLY - invented electricity bill, no personal data
Supplier: Example Energy
Currency: AUD
Billing period: 2026-10-01 to 2026-10-31
Electricity used: 420 kWh
Usage charge: $126.00
Supply charge: $31.00
Export credit: $8.50
Amount due: $148.50
All amounts include GST. No previous balance on this sample.
"""


class BillImport(ControlValue):
    name: str = Field(min_length=1, max_length=120)
    text: str = Field(min_length=1, max_length=MAX_TEXT_BYTES)

    @field_validator("name", "text")
    @classmethod
    def plain_text(cls, value: str) -> str:
        if not value.strip() or any(ord(c) < 32 and c not in "\n\r\t" for c in value):
            raise ValueError("plain bill text required")
        return value

    @field_validator("text")
    @classmethod
    def bounded_text(cls, value: str) -> str:
        if len(value.splitlines()) > 200 or any(len(line) > 512 for line in value.splitlines()):
            raise ValueError("at most 200 lines and 512 characters per line required")
        if len(value.encode("utf-8")) > MAX_TEXT_BYTES:
            raise ValueError("bill text exceeds 16 KiB")
        return value


class BillQuestion(ControlValue):
    question: str = Field(min_length=1, max_length=500)


class Evidence(ControlValue):
    line: int = Field(ge=1)
    quote: str
    page: int | None = Field(default=None, ge=1)


class Fact(ControlValue):
    key: BillKey
    label: str
    value: str | None
    unit: str
    state: FactState
    evidence: list[Evidence]


class BillFacts(ControlValue):
    extractor: Literal["bill-text-v1", "amber-pdf-v1"] = "bill-text-v1"
    source_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    facts: list[Fact]
    notices: list[str]


class BillRecord(ControlValue):
    bill_id: UUID
    name: str
    source_sha256: str
    report: BillFacts


def extract(text: str) -> BillFacts:
    lines = text.splitlines()
    currencies = {
        line.split(":", 1)[1].strip().upper()
        for line in lines
        if line.strip().lower().startswith("currency:")
    }
    currency = currencies == {"AUD"}
    facts: list[Fact] = []
    notices = ["Figures are extracted from the bill text. Check them against the original."]
    if not currency:
        notices.append(
            "Currency is not explicitly marked AUD; dollar amounts have no assumed currency."
        )
    for key, label in KEYS.items():
        found: list[tuple[str, Evidence]] = []
        invalid = False
        for number, line in enumerate(lines, 1):
            match = re.fullmatch(PATTERNS[key], line.strip(), re.I)
            if not match:
                continue
            value = match[1]
            try:
                if key == "period":
                    start, end = re.split(r"\s+to\s+", value)
                    if date.fromisoformat(start) > date.fromisoformat(end):
                        raise ValueError
                    value = start + " to " + end
                else:
                    # Commas must group thousands, never serve as decimal separators.
                    if not re.fullmatch(r"(?:[0-9]+|[0-9]{1,3}(?:,[0-9]{3})+)(?:\.[0-9]+)?", value):
                        raise ValueError
                    amount = Decimal(value.replace(",", ""))
                    if amount > Decimal("1000000000"):
                        raise ValueError
                    value = format(amount, ".3f" if key == "usage_kwh" else ".2f")
                found.append((value, Evidence(schema_version=1, line=number, quote=line)))
            except ValueError:
                invalid = True
                found.append(("", Evidence(schema_version=1, line=number, quote=line)))
        distinct = {v for v, _ in found}
        state: FactState = (
            "invalid"
            if invalid
            else "conflicting"
            if len(distinct) > 1
            else "found"
            if found
            else "missing"
        )
        unit = (
            ""
            if key == "period"
            else "kWh"
            if key == "usage_kwh"
            else "AUD"
            if currency
            else "$ (currency unconfirmed)"
        )
        facts.append(
            Fact(
                schema_version=1,
                key=key,
                label=label,
                value=found[0][0] if state == "found" else None,
                unit=unit,
                state=state,
                evidence=[e for _, e in found],
            )
        )
    values = {
        f.key: Decimal(f.value)
        for f in facts
        if f.state == "found" and f.value is not None and f.key not in {"period", "usage_kwh"}
    }
    if {"usage_charge", "supply_charge", "export_credit", "total_due"} <= values.keys():
        listed = values["usage_charge"] + values["supply_charge"] - values["export_credit"]
        comparison = "matches" if listed == values["total_due"] else "does not match"
        notices.append(
            f"Listed usage + supply - export credit = {listed:.2f}; this {comparison} "
            "the amount due. This is only a comparison of these listed figures, "
            "not a check of tax, prior balances or unlisted charges."
        )
    return BillFacts(
        schema_version=1,
        source_sha256=hashlib.sha256(text.encode()).hexdigest(),
        facts=facts,
        notices=notices,
    )


def answer(report: BillFacts, question: str) -> dict[str, object]:
    q = " ".join(question.lower().strip().rstrip("?.!").split())
    questions = {
        "how much do i owe": ["total_due", "total_owed"],
        "what is the amount due": ["total_due", "total_owed"],
        "how much electricity did i use": ["usage_kwh"],
        "what dates does this bill cover": ["period"],
        "when is this bill due": ["due_date"],
        "what are the charges": [
            "usage_charge",
            "supply_charge",
            "demand_charge",
            "subscription",
            "charges_gst",
            "charges_total",
            "export_credit",
            "concessions",
            "credits_gst",
            "credits_total",
            "total_due",
        ],
    }
    selected = questions.get(q)
    if selected is None:
        return {
            "answer": (
                "I cannot answer that question from the extracted bill figures. "
                "Try one of the suggested questions."
            ),
            "citations": [],
            "source_sha256": report.source_sha256,
        }
    parts: list[str] = []
    citations: list[dict[str, object]] = []
    for fact in report.facts:
        if fact.key not in selected:
            continue
        if fact.state != "found":
            parts.append(f"{fact.label}: not established ({fact.state}). Check the original text.")
        else:
            parts.append(f"{fact.label}: {fact.value} {fact.unit}.".replace(" .", "."))
            citations.extend(
                {"line": e.line, "quote": e.quote, **({"page": e.page} if e.page else {})}
                for e in fact.evidence
            )
    return {
        "answer": " ".join(parts),
        "citations": citations,
        "source_sha256": report.source_sha256,
    }
