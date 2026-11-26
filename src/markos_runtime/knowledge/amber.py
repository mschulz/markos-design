"""Amber Electric bill profile: page text in, cited facts out. No PDF parsing or I/O.

The figures come from the page 2 "Understand your bill" summary and the page 1 header.
Every total is checked against its lines. Any mismatch or unknown summary line marks the
money facts unreconciled, so no answer repeats them with confidence.
"""

import re
from datetime import datetime
from decimal import Decimal
from typing import cast

from markos_runtime.knowledge.bills import BillFacts, BillKey, Evidence, Fact, FactState

SUMMARY_TITLE = "Understand your bill"
COLUMN_HEADERS = {"Charge Description Amount Rate Cost", "Charge Description Amount Rate Credit"}


def amount(key: str) -> str:
    return rf"\$(?P<{key}>[0-9]{{1,3}}(?:,[0-9]{{3}})*\.[0-9]{{2}}|[0-9]+\.[0-9]{{2}})"


RATE = r"[0-9]+(?:\.[0-9]+)?"
# Summary lines on page 2, by section. Each named group is one fact.
CHARGES = [
    rf"Usage (?P<usage_kwh>{RATE}) kWh {RATE} \$/kWh {amount('usage_charge')}",
    rf"Network Daily Supply Charges [0-9]+ days {RATE} \$/Day {amount('supply_charge')}",
    rf"Network Demand Charges {amount('demand_charge')}",
    # Older bills say "Amber Membership".
    rf"Amber (?:Monthly Subscription|Membership) {amount('subscription')}",
    rf"GST +10% {amount('charges_gst')}",
    rf"CHARGES TOTAL {amount('charges_total')}",
]
CREDITS = [
    rf"Solar Exports {RATE} kWh {RATE} \$/kWh {amount('export_credit')}",
    rf"Vouchers & Concessions {amount('concessions')}",
    rf"GST +10% {amount('credits_gst')}",
    rf"CREDITS TOTAL {amount('credits_total')}",
]
OTHER = [
    # pypdf drops the dash between the two dates.
    r"(?P<period>[0-9]{2}/[0-9]{2}/[0-9]{4} *[-–]? *[0-9]{2}/[0-9]{2}/[0-9]{4})",
    rf"BILL TOTAL {amount('total_due')}",
]

LABELS: dict[BillKey, str] = {
    "period": "Billing period",
    "usage_kwh": "Electricity used",
    "usage_charge": "Usage charge",
    "supply_charge": "Network daily supply charge",
    "demand_charge": "Network demand charge",
    "subscription": "Amber subscription",
    "charges_gst": "GST on charges",
    "charges_total": "Charges total",
    "export_credit": "Solar export credit",
    "concessions": "Vouchers and concessions",
    "credits_gst": "GST on credits",
    "credits_total": "Credits total",
    "total_due": "Bill total",
    "total_owed": "Total owed",
    "due_date": "Due date",
}
CHARGE_LINES: list[BillKey] = [
    "usage_charge",
    "supply_charge",
    "demand_charge",
    "subscription",
    "charges_gst",
]
CREDIT_LINES: list[BillKey] = ["export_credit", "concessions", "credits_gst"]
MONEY: set[BillKey] = {*CHARGE_LINES, *CREDIT_LINES, "charges_total", "credits_total", "total_due"}

Found = dict[BillKey, list[tuple[str | None, Evidence]]]


def is_amber(pages: list[str]) -> bool:
    return any("Amber Electric Pty Ltd" in page for page in pages)


def unspace(line: str) -> str:
    """Normalise one extracted line for matching. Citations keep the original line.

    Amber's font maps `(`, `)` and the date dash to private-use code points, so these
    become spaces. Space runs collapse to one. Letter-spaced amounts such as
    `$ 5 7 . 1 5` join into `$57.15`.
    """
    line = " ".join(re.sub(r"[-]", " ", line).split())
    return re.sub(
        r"\$ ((?:[0-9.,] )+[0-9.,]?)", lambda m: "$" + m[1].replace(" ", "") + " ", line + " "
    ).strip()


def period(value: str) -> str | None:
    """Day-first dates to `YYYY-MM-DD to YYYY-MM-DD`, or None when invalid."""
    try:
        start, end = (
            datetime.strptime(d, "%d/%m/%Y").date() for d in re.findall(r"[0-9/]{10}", value)
        )
    except ValueError:
        return None
    return f"{start} to {end}" if start <= end else None


def due_date(value: str) -> str | None:
    try:
        return str(datetime.strptime(value, "%d %b %Y").date())
    except ValueError:
        return None


def read_summary(page: str, number: int, found: Found) -> list[str]:
    """Record summary facts from one page. Returns the summary lines it did not recognise."""
    unknown: list[str] = []
    section: list[str] | None = None
    for n, quote in enumerate(page.splitlines(), 1):
        line = unspace(quote)
        if line in {"CHARGES SUMMARY", "CREDITS SUMMARY"}:
            section = CHARGES if line == "CHARGES SUMMARY" else CREDITS
            continue
        match = next((m for p in (section or []) + OTHER if (m := re.fullmatch(p, line))), None)
        if match is None:
            if section is not None and line not in COLUMN_HEADERS:
                unknown.append(f"page {number} line {n}: {quote.strip()}")
            continue
        for key, value in match.groupdict().items():
            parsed = period(value) if key == "period" else value.replace(",", "")
            evidence = Evidence(schema_version=1, page=number, line=n, quote=quote)
            found[cast(BillKey, key)].append((parsed, evidence))
        if line.startswith(("CHARGES TOTAL", "CREDITS TOTAL")):
            section = None
    return unknown


def read_header(page: str, found: Found) -> None:
    lines = page.splitlines()
    for n, quote in enumerate(lines, 1):
        line = unspace(quote)
        if line == "Total Owed" and n < len(lines):
            owed = re.fullmatch(amount("total_owed"), unspace(lines[n]))
            if owed:
                evidence = Evidence(schema_version=1, page=1, line=n + 1, quote=lines[n])
                found["total_owed"].append((owed["total_owed"].replace(",", ""), evidence))
        due = re.fullmatch(r"Due Date: ([0-9]{2} [A-Z][a-z]{2} [0-9]{4})", line)
        if due:
            evidence = Evidence(schema_version=1, page=1, line=n, quote=quote)
            found["due_date"].append((due_date(due[1]), evidence))


def fact(key: BillKey, found: list[tuple[str | None, Evidence]]) -> Fact:
    distinct = {value for value, _ in found}
    state: FactState = (
        "invalid"
        if None in distinct
        else "conflicting"
        if len(distinct) > 1
        else "found"
        if found
        else "missing"
    )
    unit = "kWh" if key == "usage_kwh" else "" if key in {"period", "due_date"} else "AUD"
    return Fact(
        schema_version=1,
        key=key,
        label=LABELS[key],
        value=found[0][0] if state == "found" else None,
        unit=unit,
        state=state,
        evidence=[e for _, e in found],
    )


def reconcile(facts: list[Fact], notices: list[str], unknown: list[str]) -> list[Fact]:
    """Check every total. On any failure, money facts become unreconciled."""
    values = {f.key: Decimal(f.value) for f in facts if f.key in MONEY and f.value is not None}
    problems = []
    if unknown:
        problems.append("unrecognised summary lines: " + "; ".join(unknown))
    elif not values.keys() >= MONEY:
        problems.append("some figures are missing or unclear, so the totals were not checked")
    else:
        if sum(values[k] for k in CHARGE_LINES) != values["charges_total"]:
            problems.append("the charge lines do not add up to the charges total")
        if sum(values[k] for k in CREDIT_LINES) != values["credits_total"]:
            problems.append("the credit lines do not add up to the credits total")
        if values["charges_total"] - values["credits_total"] != values["total_due"]:
            problems.append("charges total minus credits total is not the bill total")
    if not problems:
        notices.append(
            "Every charge and credit line adds up to its total, and charges minus credits "
            "equals the bill total."
        )
        owed = next(f for f in facts if f.key == "total_owed")
        if owed.value is not None and Decimal(owed.value) != values["total_due"]:
            notices.append(
                "Total owed differs from this bill's total. The difference may be an earlier "
                "unpaid balance or credit. Check the original."
            )
        return facts
    notices.extend("Needs review: " + problem + "." for problem in problems)
    return [
        f.model_copy(update={"state": "unreconciled", "value": None})
        if f.key in MONEY and f.state == "found"
        else f
        for f in facts
    ]


def extract_amber(pages: list[str], source_sha256: str) -> BillFacts:
    found: Found = {key: [] for key in LABELS}
    notices = [
        "Figures are extracted from the Amber Electric bill PDF. Check them against the original.",
        "Amounts are AUD: Amber Electric is an Australian retailer and its bills show GST.",
    ]
    summaries = [i for i, page in enumerate(pages, 1) if page.splitlines()[:1] == [SUMMARY_TITLE]]
    unknown: list[str] = []
    if len(summaries) == 1:
        unknown = read_summary(pages[summaries[0] - 1], summaries[0], found)
    else:
        notices.append("Needs review: the bill summary page was not found exactly once.")
    if pages:
        read_header(pages[0], found)
    facts = reconcile([fact(key, found[key]) for key in LABELS], notices, unknown)
    return BillFacts(
        schema_version=1,
        extractor="amber-pdf-v1",
        source_sha256=source_sha256,
        facts=facts,
        notices=notices,
    )
